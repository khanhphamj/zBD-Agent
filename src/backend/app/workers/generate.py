from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_context
from app.core.config import settings
from app.core.logging import get_logger
from app.core.text import truncate
from app.db.models.customer import Customer, CustomerFact, CustomerSolution
from app.db.models.enums import (
    JobEventType,
    NotificationType,
    ProposalStatus,
    SkillStage,
)
from app.db.models.job import Job
from app.db.models.knowledge import KnowledgeFile
from app.db.models.library import ProposalTemplate, Skill, TemplateSlide
from app.db.models.product import Product
from app.db.models.proposal import Proposal, ProposalSlide
from app.db.models.user import User
from app.integrations.maas import MaasError, maas
from app.services.events import bus
from app.services.notifications import push
from app.workers.base import JobCancelled, check_cancel, claim, fail, finish, step

log = get_logger(__name__)

MARKET = settings.research_market_label

OUTLINE_SYSTEM = f"""\
Bạn là chuyên gia viết proposal B2B của đội Business Development Zalo,
làm việc trên thị trường {MARKET}.

Nhiệm vụ: dựng dàn ý một bộ slide đề xuất giải pháp cho khách hàng.

Quy tắc:
- Bám sát dữ kiện được cung cấp. Không bịa số liệu, không bịa tên đối tác.
- Mỗi slide có mục tiêu rõ ràng, tránh trùng lặp nội dung.
- Văn phong doanh nghiệp {MARKET}, tiếng Việt, ngắn gọn, hướng kết quả.
- Nếu template yêu cầu các slide bắt buộc, PHẢI có đủ và giữ đúng thứ tự.

Trả về DUY NHẤT JSON:
{{"summary":"<2-3 câu tóm tắt đề xuất>",
  "slides":[{{"index":1,"title":"<tiêu đề>","layout":"cover|content|table|chart|closing",
              "goal":"<slide này cần đạt điều gì>",
              "points":["<gạch đầu dòng định hướng>"]}}]}}
"""

SLIDE_SYSTEM = f"""\
Bạn là chuyên gia viết proposal B2B của Zalo, thị trường {MARKET}.
Viết nội dung hoàn chỉnh cho MỘT slide theo định hướng được giao.

Quy tắc:
- Tiếng Việt, văn phong doanh nghiệp, không sáo rỗng.
- Bullet tối đa 18 từ. Tối đa 5 bullet mỗi khối.
- Chỉ dùng số liệu có trong dữ kiện. Không có thì viết định tính.
- `citations` liệt kê id dữ kiện hoặc id sản phẩm đã dùng.

Trả về DUY NHẤT JSON:
{{"title":"<tiêu đề slide>",
  "blocks":[{{"type":"bullets","items":["..."]}},
            {{"type":"text","text":"..."}},
            {{"type":"table","columns":["..."],"rows":[["..."]]}}],
  "speakerNotes":"<gợi ý trình bày 2-3 câu>",
  "citations":[{{"kind":"fact|product","id":"<id>"}}]}}
"""


async def run_generate(ctx: dict[str, Any], job_id: str) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        job = await claim(session, job_id)
        if job is None:
            return
        try:
            await _generate(session, job)
        except JobCancelled:
            return
        except Exception as exc:
            log.exception("generate_crashed", job=job_id, error=str(exc))
            await fail(session, job, "GENERATE_FAILED", f"Không tạo được nội dung: {exc}"[:300])


async def _generate(session: AsyncSession, job: Job) -> None:
    proposal = await session.get(Proposal, job.proposal_id) if job.proposal_id else None
    if proposal is None:
        await fail(session, job, "GENERATE_FAILED", "Không tìm thấy proposal.")
        return

    if not maas.configured:
        await fail(
            session,
            job,
            "GENERATE_FAILED",
            "Chưa cấu hình AI_PLATFORM_API_KEY — không gọi được MaaS.",
        )
        return

    params = job.params or {}
    target_slides = int(params.get("slideCount") or 12)
    tone = params.get("tone") or proposal.tone or "professional"

    customer = await session.get(Customer, proposal.customer_id)
    if customer is None:
        await fail(session, job, "GENERATE_FAILED", "Không tìm thấy khách hàng.")
        return

    await check_cancel(session, job)
    await step(session, job, "context", "Đang tổng hợp dữ kiện và sản phẩm", 0.08)

    facts = list(
        (
            await session.execute(
                select(CustomerFact)
                .where(CustomerFact.customer_id == customer.id)
                .order_by(CustomerFact.is_opportunity.desc(), CustomerFact.confidence.desc())
                .limit(40)
            )
        )
        .scalars()
        .all()
    )

    solution_ids: list[str] = params.get("solutionIds") or []
    solution_stmt = select(CustomerSolution).where(CustomerSolution.customer_id == customer.id)
    solution_stmt = (
        solution_stmt.where(CustomerSolution.id.in_(solution_ids))
        if solution_ids
        else solution_stmt.where(CustomerSolution.selected.is_(True))
    )
    solutions = list((await session.execute(solution_stmt)).scalars().all())

    product_ids = [s.product_id for s in solutions if s.product_id]
    products = (
        list(
            (await session.execute(select(Product).where(Product.id.in_(product_ids))))
            .scalars()
            .all()
        )
        if product_ids
        else list(
            (await session.execute(select(Product).where(Product.status == "active").limit(10)))
            .scalars()
            .all()
        )
    )

    kb_ids: list[str] = params.get("knowledgeBaseIds") or []
    kb_context = ""
    if kb_ids:
        files = (
            (
                await session.execute(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id.in_(kb_ids),
                        KnowledgeFile.extracted_text.is_not(None),
                    )
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        kb_context = "\n".join(
            f"[{f.name}] {truncate(f.extracted_text or '', 1200)}" for f in files
        )

    skill_ids: list[str] = params.get("skillIds") or []
    skill_stmt = select(Skill).where(Skill.stage == SkillStage.PROPOSAL)
    if skill_ids:
        skill_stmt = select(Skill).where(Skill.id.in_(skill_ids))
    skills = list((await session.execute(skill_stmt)).scalars().all())
    skill_guidance = "\n\n".join(f"[SKILL {s.name}]\n{s.prompt}" for s in skills if s.prompt)

    template = (
        await session.get(ProposalTemplate, proposal.template_id) if proposal.template_id else None
    )
    template_slides: list[TemplateSlide] = []
    if template:
        template_slides = list(
            (
                await session.execute(
                    select(TemplateSlide)
                    .where(TemplateSlide.template_id == template.id)
                    .order_by(TemplateSlide.index)
                )
            )
            .scalars()
            .all()
        )
        if template.slide_count:
            target_slides = template.slide_count

    context = _format_context(customer, facts, solutions, products, kb_context)

    await check_cancel(session, job)
    await step(session, job, "outline", "Đang dựng dàn ý", 0.20)

    required = (template.required_slides if template else []) or []
    outline_prompt = (
        f"{today_context()}\n\n"
        f"{context}\n\n"
        f"YÊU CẦU:\n"
        f"- Số slide: {target_slides}\n"
        f"- Tông giọng: {tone}\n"
        f"- Thị trường: {MARKET}\n"
    )
    if required:
        outline_prompt += f"- Slide bắt buộc (đúng thứ tự): {', '.join(required)}\n"
    if template_slides:
        outline_prompt += "- Khung template:\n" + "\n".join(
            f"    {t.index}. {t.title} ({t.layout})" + (f" — {t.guidance}" if t.guidance else "")
            for t in template_slides
        )
    if skill_guidance:
        outline_prompt += f"\n\nHƯỚNG DẪN THÊM:\n{skill_guidance[:4000]}"

    try:
        completion = await maas.run("proposal.outline", OUTLINE_SYSTEM, outline_prompt)
        outline_data = completion.as_json()
    except MaasError as exc:
        await fail(session, job, "GENERATE_FAILED", f"Không dựng được dàn ý: {exc}"[:300])
        return

    if not isinstance(outline_data, dict):
        outline_data = {}
    outline = [s for s in (outline_data.get("slides") or []) if isinstance(s, dict)]
    if not outline:
        await fail(session, job, "GENERATE_FAILED", "Model không trả về dàn ý hợp lệ.")
        return

    proposal.outline = outline
    proposal.summary = outline_data.get("summary") or proposal.summary
    await session.commit()

    for stale in (
        (
            await session.execute(
                select(ProposalSlide).where(ProposalSlide.proposal_id == proposal.id)
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(stale)
    await session.commit()

    total = len(outline)
    written = 0

    for position, entry in enumerate(outline, start=1):
        await check_cancel(session, job)
        title = str(entry.get("title") or f"Slide {position}")
        await step(
            session,
            job,
            "slides",
            f"Đang viết slide {position}/{total}: {title}",
            0.25 + 0.65 * (position - 1) / max(1, total),
        )

        guidance = next((t for t in template_slides if t.index == position), None)
        slide_prompt = (
            f"{context}\n\n"
            f"SLIDE CẦN VIẾT: {position}/{total}\n"
            f"TIÊU ĐỀ ĐỀ XUẤT: {title}\n"
            f"MỤC TIÊU: {entry.get('goal') or '—'}\n"
            f"ĐỊNH HƯỚNG: {'; '.join(entry.get('points') or []) or '—'}\n"
            f"LAYOUT: {entry.get('layout') or 'content'}\n"
            f"TÔNG GIỌNG: {tone}\n"
        )
        if guidance:
            slide_prompt += (
                f"KHUNG TEMPLATE: {guidance.title} — {guidance.guidance or ''}\n"
                f"PLACEHOLDER: {guidance.placeholders}\n"
            )

        blocks: list[dict[str, Any]] = []
        notes: str | None = None
        citations: list[dict[str, Any]] = []
        final_title = title

        try:
            slide_completion = await maas.run("proposal.slide", SLIDE_SYSTEM, slide_prompt)
            payload = slide_completion.as_json()
            if isinstance(payload, dict):
                final_title = str(payload.get("title") or title)
                blocks = [b for b in (payload.get("blocks") or []) if isinstance(b, dict)]
                notes = payload.get("speakerNotes")
                citations = [c for c in (payload.get("citations") or []) if isinstance(c, dict)]
        except MaasError as exc:
            log.warning("slide_generation_failed", job=job.id, index=position, error=str(exc))
            blocks = [
                {
                    "type": "text",
                    "text": "Không tạo được nội dung cho slide này. Hãy thử tạo lại hoặc viết tay.",
                }
            ]

        row = ProposalSlide(
            id=ProposalSlide.make_id(),
            proposal_id=proposal.id,
            index=position,
            title=final_title,
            layout=str(entry.get("layout") or "content"),
            blocks=blocks,
            speaker_notes=notes,
            citations=citations,
            status="done" if blocks else "failed",
        )
        session.add(row)
        await session.commit()
        written += 1

        await bus.publish(
            session,
            job.id,
            JobEventType.SLIDE,
            {
                "index": position,
                "title": final_title,
                "status": row.status,
                "slideId": row.id,
            },
            message=f"Slide {position}: {final_title}",
        )

    await check_cancel(session, job)
    await step(session, job, "finalize", "Đang hoàn thiện bộ slide", 0.95)

    proposal.slide_count = written
    if proposal.status not in (
        ProposalStatus.PENDING_APPROVAL,
        ProposalStatus.APPROVED,
    ):
        proposal.status = ProposalStatus.DRAFT
    await session.commit()

    if job.created_by_id:
        owner = await session.get(User, job.created_by_id)
        if owner:
            await push(
                session,
                user_id=owner.id,
                kind=NotificationType.PROPOSAL_DONE,
                title="Proposal đã sẵn sàng",
                body=f"{proposal.title} — {written} slide.",
                target_url=f"/proposals/{proposal.id}",
            )
            await session.commit()

    await finish(session, job, {"proposalId": proposal.id, "slideCount": written})


def _format_context(
    customer: Customer,
    facts: list[CustomerFact],
    solutions: list[CustomerSolution],
    products: list[Product],
    kb_context: str,
) -> str:
    lines = [
        f"KHÁCH HÀNG: {customer.name}",
        f"NGÀNH: {customer.industry or 'chưa rõ'}",
        f"WEBSITE: {customer.website or 'chưa rõ'}",
        f"THỊ TRƯỜNG: {MARKET}",
    ]
    if customer.profile:
        lines.append(f"HỒ SƠ: {customer.profile}")

    if facts:
        lines.append(
            "DỮ KIỆN NGHIÊN CỨU:\n"
            + "\n".join(
                f"  - [{f.id}] ({f.topic}, tin cậy {f.confidence}%"
                f"{', CƠ HỘI' if f.is_opportunity else ''}) {truncate(f.summary, 260)}"
                for f in facts
            )
        )
    if solutions:
        lines.append(
            "PHƯƠNG ÁN ĐÃ CHỌN:\n"
            + "\n".join(
                f"  - {s.title} (fit {s.fit_score:.0f}) — {truncate(s.rationale or '', 200)}"
                for s in solutions
            )
        )
    if products:
        lines.append(
            "SẢN PHẨM ZALO:\n"
            + "\n".join(
                f"  - [{p.id}] {p.name} ({p.family}): {truncate(p.description or '', 200)}"
                + (f" | Gói: {', '.join(p.packages)}" if p.packages else "")
                for p in products
            )
        )
    if kb_context:
        lines.append(f"TÀI LIỆU NỘI BỘ:\n{kb_context[:8000]}")

    return "\n\n".join(lines)
