import io
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import now
from app.core.text import slugify
from app.db.models.customer import Customer, CustomerActivity
from app.db.models.enums import ActivityKind, ExportStage, ExportStatus
from app.db.models.job import Job
from app.db.models.library import ProposalTemplate
from app.db.models.proposal import Proposal, ProposalExport, ProposalSlide
from app.services.storage import storage
from app.workers.base import JobCancelled, check_cancel, claim, fail, finish, step

log = get_logger(__name__)


NAVY = (0x02, 0x1F, 0x4E)
BLUE = (0x00, 0x68, 0xFF)
GREY = (0x4D, 0x5F, 0x85)

STAGE_ORDER = [
    ExportStage.PREPARE,
    ExportStage.RENDER,
    ExportStage.EMBED,
    ExportStage.PACKAGE,
]


async def run_export(ctx: dict[str, Any], job_id: str) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        job = await claim(session, job_id)
        if job is None:
            return

        record = await session.get(ProposalExport, job.export_id) if job.export_id else None
        try:
            await _export(session, job, record)
        except JobCancelled:
            if record:
                record.status = ExportStatus.FAILED
                record.error = {"code": "CANCELLED", "message": "Đã dừng theo yêu cầu."}
                await session.commit()
        except Exception as exc:
            log.exception("export_crashed", job=job_id, error=str(exc))
            stage = str(record.stage) if record else "prepare"
            if record:
                record.status = ExportStatus.FAILED
                record.error = {
                    "code": "EXPORT_503",
                    "message": f"Mất kết nối dịch vụ kết xuất: {exc}"[:280],
                    "stage": stage,
                    "slideIndex": None,
                }
                await session.commit()
            await fail(
                session,
                job,
                "EXPORT_503",
                "Mất kết nối tới dịch vụ kết xuất",
                stage=stage,
                slideIndex=None,
            )


async def _export(session: AsyncSession, job: Job, record: ProposalExport | None) -> None:
    if record is None:
        await fail(session, job, "EXPORT_422", "Không tìm thấy yêu cầu kết xuất.")
        return

    proposal = await session.get(Proposal, record.proposal_id)
    if proposal is None:
        await fail(session, job, "EXPORT_422", "Không tìm thấy proposal.", stage="prepare")
        return

    resume_from = (job.params or {}).get("resumeFrom")
    start_index = 0
    if resume_from:
        try:
            start_index = STAGE_ORDER.index(ExportStage(resume_from))
        except ValueError:
            start_index = 0

    def resumes_past(stage: ExportStage) -> bool:
        return start_index > STAGE_ORDER.index(stage)

    await check_cancel(session, job)
    record.stage = ExportStage.PREPARE
    record.progress = 0.1
    await session.commit()
    if not resumes_past(ExportStage.PREPARE):
        await step(session, job, "prepare", "Đang chuẩn bị dữ liệu", 0.1)

    slides = list(
        (
            await session.execute(
                select(ProposalSlide)
                .where(ProposalSlide.proposal_id == proposal.id)
                .order_by(ProposalSlide.index)
            )
        )
        .scalars()
        .all()
    )
    if not slides:
        await _mark_failed(
            session, job, record, "EXPORT_422", "Proposal chưa có slide nào.", "prepare", None
        )
        return

    customer = await session.get(Customer, proposal.customer_id)
    template = (
        await session.get(ProposalTemplate, record.template_id) if record.template_id else None
    )

    await check_cancel(session, job)
    record.stage = ExportStage.RENDER
    record.progress = 0.35
    await session.commit()

    ext = "pdf"
    content_type = "application/pdf"
    payload: bytes | None = None

    reuse = (
        resumes_past(ExportStage.RENDER)
        and record.object_key is not None
        and await storage.exists(settings.bucket_exports, record.object_key)
    )
    if reuse:
        payload = await storage.get(settings.bucket_exports, record.object_key or "")
    else:
        await step(session, job, "render", "Đang dựng từng slide", 0.35)
        try:
            payload, content_type, ext = _build_pdf(proposal, customer, slides, template)
        except _SlideRenderError as exc:
            await _mark_failed(
                session,
                job,
                record,
                "EXPORT_422",
                f"Không dựng được slide {exc.slide_index}: {exc}",
                "render",
                exc.slide_index,
            )
            return
        except Exception as exc:
            await _mark_failed(
                session,
                job,
                record,
                "EXPORT_422",
                f"Không dựng được tài liệu: {exc}"[:280],
                "render",
                None,
            )
            return

    await check_cancel(session, job)
    record.stage = ExportStage.EMBED
    record.progress = 0.7
    await session.commit()
    if not resumes_past(ExportStage.EMBED):
        await step(session, job, "embed", "Đang nhúng hình ảnh và bảng", 0.7)

    await check_cancel(session, job)
    record.stage = ExportStage.PACKAGE
    record.progress = 0.9
    await session.commit()
    await step(session, job, "package", "Đang đóng gói file", 0.9)

    filename = (
        record.filename or f"{slugify(proposal.title) or 'proposal'}-{proposal.version}.{ext}"
    )
    key = record.object_key if reuse else storage.export_key(proposal.id, record.id, filename)

    try:
        stored = await storage.put(
            settings.bucket_exports, key or "", payload or b"", content_type=content_type
        )
    except Exception as exc:
        await _mark_failed(
            session,
            job,
            record,
            "EXPORT_503",
            f"Không lưu được file kết xuất: {exc}"[:280],
            "package",
            None,
        )
        return

    record.object_key = key
    record.filename = filename
    record.size_bytes = stored.size
    record.status = ExportStatus.DONE
    record.progress = 1.0
    record.error = None
    record.completed_at = now()

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=proposal.customer_id,
            actor_id=job.created_by_id,
            kind=ActivityKind.PROPOSAL_EXPORTED,
            title=f"Xuất proposal {record.format.value.upper()}",
            body=proposal.title,
            meta={"exportId": record.id, "filename": filename},
        )
    )
    await session.commit()

    download_url = await storage.presigned_url(settings.bucket_exports, key, download_name=filename)
    await finish(
        session,
        job,
        {
            "exportId": record.id,
            "format": str(record.format),
            "filename": filename,
            "sizeBytes": stored.size,
            "downloadUrl": download_url,
        },
    )


async def _mark_failed(
    session: AsyncSession,
    job: Job,
    record: ProposalExport,
    code: str,
    message: str,
    stage: str,
    slide_index: int | None,
) -> None:
    record.status = ExportStatus.FAILED
    record.stage = ExportStage(stage)
    record.error = {
        "code": code,
        "message": message,
        "stage": stage,
        "slideIndex": slide_index,
    }
    await session.commit()
    await fail(session, job, code, message, stage=stage, slideIndex=slide_index)


class _SlideRenderError(Exception):
    def __init__(self, slide_index: int, message: str) -> None:
        super().__init__(message)
        self.slide_index = slide_index


def _rgb(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        try:
            return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
        except ValueError:
            return fallback
    return fallback


def _build_pdf(
    proposal: Proposal,
    customer: Customer | None,
    slides: list[ProposalSlide],
    template: ProposalTemplate | None = None,
) -> tuple[bytes, str, str]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from app.workers.fonts import unicode_font

    font, font_bold = unicode_font()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.6 * cm,
        bottomMargin=1.6 * cm,
        title=proposal.title,
    )

    base = getSampleStyleSheet()
    palette = (
        [
            c
            for c in ((template.theme or {}).get("palette") or [])
            if isinstance(c, str) and c.startswith("#") and len(c) == 7
        ]
        if template
        else []
    )
    navy_rgb = _rgb(next((c for c in palette if sum(_rgb(c, NAVY)) < 400), None), NAVY)
    blue_rgb = _rgb(
        next((c for c in palette if _rgb(c, BLUE) != navy_rgb and sum(_rgb(c, BLUE)) >= 400), None),
        BLUE,
    )

    navy = colors.Color(*[c / 255 for c in navy_rgb])
    grey = colors.Color(*[c / 255 for c in GREY])
    blue = colors.Color(*[c / 255 for c in blue_rgb])

    title_style = ParagraphStyle(
        "ZbTitle",
        parent=base["Title"],
        fontName=font_bold,
        fontSize=26,
        textColor=navy,
        spaceAfter=14,
    )
    heading_style = ParagraphStyle(
        "ZbHeading",
        parent=base["Heading1"],
        fontName=font_bold,
        fontSize=18,
        textColor=navy,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "ZbBody",
        parent=base["BodyText"],
        fontName=font,
        fontSize=11,
        textColor=grey,
        leading=16,
    )
    bullet_style = ParagraphStyle(
        "ZbBullet", parent=body_style, leftIndent=12, bulletIndent=2, spaceAfter=4
    )

    story: list[Any] = [
        Paragraph(_escape(proposal.title), title_style),
        Paragraph(
            _escape(
                " · ".join(
                    filter(
                        None,
                        [customer.name if customer else None, proposal.version, "Zalo Business"],
                    )
                )
            ),
            ParagraphStyle("ZbSub", parent=body_style, fontName=font, textColor=blue, fontSize=13),
        ),
    ]
    if proposal.summary:
        story += [Spacer(1, 0.8 * cm), Paragraph(_escape(proposal.summary), body_style)]
    story.append(PageBreak())

    for slide in slides:
        try:
            story.append(Paragraph(_escape(slide.title), heading_style))
            for block in slide.blocks or []:
                kind = block.get("type")
                if kind == "bullets":
                    for item in block.get("items") or []:
                        story.append(Paragraph(_escape(str(item)), bullet_style, bulletText="•"))
                elif kind == "text":
                    story.append(Paragraph(_escape(str(block.get("text") or "")), body_style))
                elif kind == "table":
                    columns = [str(c) for c in (block.get("columns") or [])]
                    rows = block.get("rows") or []
                    if columns and rows:
                        data = [columns] + [[str(cell) for cell in row] for row in rows]
                        table = Table(data, hAlign="LEFT")
                        table.setStyle(
                            TableStyle(
                                [
                                    ("BACKGROUND", (0, 0), (-1, 0), navy),
                                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                    ("FONTNAME", (0, 0), (-1, 0), font_bold),
                                    ("FONTNAME", (0, 1), (-1, -1), font),
                                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                                    ("GRID", (0, 0), (-1, -1), 0.4, colors.Color(0.86, 0.9, 0.97)),
                                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                                ]
                            )
                        )
                        story += [Spacer(1, 0.3 * cm), table, Spacer(1, 0.3 * cm)]
                story.append(Spacer(1, 0.2 * cm))
            story.append(PageBreak())
        except Exception as exc:
            raise _SlideRenderError(slide.index, str(exc)) from exc

    doc.build(story)
    return buffer.getvalue(), "application/pdf", "pdf"


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
