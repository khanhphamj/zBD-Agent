from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_context
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import now
from app.core.text import clean_website, normalize, slugify, truncate
from app.db.models.customer import (
    Customer,
    CustomerActivity,
    CustomerFact,
    CustomerSource,
)
from app.db.models.enums import (
    ActivityKind,
    CustomerStatus,
    FactVerdict,
    JobEventType,
    JobStatus,
    NotificationType,
    SourceStatus,
)
from app.db.models.job import Job
from app.db.models.knowledge import KnowledgeFile
from app.db.models.user import User
from app.integrations.harness import profile as model_profile
from app.integrations.maas import MaasError, maas
from app.integrations.search import SearchHit, search_client, vn_rank
from app.services.events import bus
from app.services.notifications import email_if_opted_in, push
from app.workers.base import JobCancelled, check_cancel, claim, fail, finish, step
from app.workers.research_agent import build_corpus, plan_trace, run_agent

log = get_logger(__name__)

MARKET = settings.research_market_label


EXTRACT_ATTEMPTS: tuple[tuple[float, int], ...] = ((1.0, 16_384), (0.45, 8_192))

SOURCE_SLOTS = [
    ("official", "Website chính thức"),
    ("registry", "Hồ sơ pháp nhân"),
    ("linkedin", "LinkedIn công ty"),
    ("fanpage", "Fanpage & kênh social"),
    ("careers", "Trang tuyển dụng"),
    ("press", "Báo chí ngành"),
    ("kb", "Knowledge Base"),
]

REGISTRY_HOSTS = (
    "masothue.com",
    "hosocongty.vn",
    "thongtindoanhnghiep.co",
    "infodoanhnghiep.com",
    "dangkykinhdoanh.gov.vn",
    "cbonline.ssc.gov.vn",
    "hsx.vn",
    "hnx.vn",
)

SOCIAL_HOSTS = (
    "facebook.com",
    "fb.com",
    "zalo.me",
    "tiktok.com",
    "youtube.com",
    "instagram.com",
)

CAREER_HOSTS = (
    "vietnamworks.com",
    "topcv.vn",
    "itviec.com",
    "topdev.vn",
    "careerbuilder.vn",
    "jobsgo.vn",
)

CAREER_WORDS = ("tuyen-dung", "career", "job", "viec-lam", "recruit")


def name_key(value: str) -> str:
    """“VietMoney” -> “vietmoney” để so với host. Dùng cho trường hợp hồ sơ khách hàng
    chưa có website: nhận ra domain chính chủ từ chính kết quả tìm kiếm."""
    return slugify(value).replace("-", "")


EXTRACT_SYSTEM = f"""\
Bạn là chuyên viên phân tích doanh nghiệp của đội Business Development Zalo,
phụ trách **thị trường {MARKET}**.

Nhiệm vụ: đọc các nguồn được cung cấp và rút ra dữ kiện về hoạt động của doanh
nghiệp **tại {MARKET}**.

Phạm vi — bắt buộc:
- CHỈ lấy dữ kiện về thị trường {MARKET}. Ví dụ với "Napas": lấy thông tin Napas
  vận hành tại {MARKET} (sản phẩm, đối tác ngân hàng trong nước, số liệu giao
  dịch nội địa, quy định của NHNN), KHÔNG lấy thông tin công ty trùng tên ở
  nước khác, KHÔNG lấy hoạt động của công ty mẹ ở thị trường ngoài {MARKET}.
- Nếu một nguồn nói về thị trường khác, bỏ qua nguồn đó.
- Nếu công ty là chi nhánh/pháp nhân {MARKET} của tập đoàn nước ngoài, chỉ giữ
  phần liên quan tới {MARKET}.
- Không bịa. Mọi dữ kiện phải truy được về một nguồn trong danh sách.
- Viết bằng tiếng Việt, mỗi dữ kiện 1–3 câu, có số liệu nếu nguồn có.

Trả về DUY NHẤT một JSON object:
{{
  "facts": [
    {{"topic": "<chủ đề ngắn>", "summary": "<1-3 câu>",
      "confidence": <0-100>, "sourceUrl": "<url>", "isOpportunity": <true|false>}}
  ],
  "profile": {{
    "industry": "<ngành>", "hq": "<trụ sở tại {MARKET}>",
    "size": "<quy mô nhân sự>", "channels": ["<kênh bán/CSKH>"],
    "techStack": ["<công nghệ>"], "marketPosition": "<vị thế tại {MARKET}>"
  }},
  "opportunities": [
    {{"topic": "<cơ hội cho Zalo>", "summary": "<lý do>", "confidence": <0-100>}}
  ]
}}

`isOpportunity` = true khi dữ kiện cho thấy nhu cầu mà sản phẩm Zalo
(ZNS, Zalo OA, Mini App, Zalo Ads, Zalo Pay) có thể giải quyết.
"""


def build_queries(customer: Customer) -> list[str]:
    name = customer.name
    site = clean_website(customer.website or "")
    industry = customer.industry or ""

    queries: list[str] = [
        f"{name} {MARKET} giới thiệu công ty",
        f"{name} {MARKET} sản phẩm dịch vụ khách hàng",
        f'"{name}" {MARKET} tin tức mới nhất',
        f"{name} {MARKET} tuyển dụng nhân sự",
        f"{name} {MARKET} chuyển đổi số công nghệ",
    ]

    if site:
        queries.insert(0, f"site:{site} giới thiệu")
        queries.insert(1, f"site:{site} sản phẩm")
    if industry:
        queries.append(f"{name} {industry} thị trường {MARKET}")

    press = " OR ".join(f"site:{d}" for d in settings.research_preferred_domains[:6])
    queries.append(f"{name} ({press})")
    queries.append(f"{name} khách hàng doanh nghiệp {MARKET} quy mô")

    return queries


async def _emit_source(
    session: AsyncSession,
    job: Job,
    customer_id: str,
    *,
    name: str | None,
    url: str | None,
    title: str | None,
    status: SourceStatus,
    http: int | None = None,
    reason: str | None = None,
    excerpt: str | None = None,
) -> CustomerSource:
    row = CustomerSource(
        id=CustomerSource.make_id(),
        customer_id=customer_id,
        job_id=job.id,
        name=name,
        url=url,
        title=title,
        status=status,
        http_status=http,
        reason=reason,
        excerpt=excerpt,
        fetched_at=now() if status == SourceStatus.OK else None,
    )
    session.add(row)
    await session.commit()

    await bus.publish(
        session,
        job.id,
        JobEventType.SOURCE,
        {
            "url": url or name,
            "title": truncate(title or name or "", 160),
            "status": str(status),
            **({"http": http} if http else {}),
            **({"reason": reason} if reason else {}),
        },
        message=f"{name or url}: {status}",
    )
    return row


async def run_research(ctx: dict[str, Any], job_id: str) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        job = await claim(session, job_id)
        if job is None:
            return
        try:
            await _research(session, job)
            # _research gọi fail() rồi return ở nhiều nhánh; nếu không hạ cờ ở đây thì
            # khách hàng kẹt mãi ở trạng thái “Đang Deep Research”.
            if job.status == JobStatus.FAILED:
                await _revert_status(session, job)
        except JobCancelled:
            await _revert_status(session, job)
        except Exception as exc:
            log.exception("research_crashed", job=job_id, error=str(exc))
            await fail(
                session,
                job,
                "RESEARCH_503",
                "Mất kết nối tới dịch vụ nghiên cứu",
                stage=job.stage or "crawl",
                sources=[],
                reasons=[str(exc)[:300]],
            )
            await _revert_status(session, job)


async def _revert_status(session: AsyncSession, job: Job) -> None:
    if not job.customer_id:
        return
    customer = await session.get(Customer, job.customer_id)
    if customer and customer.status == CustomerStatus.RESEARCHING:
        customer.status = CustomerStatus.RESEARCHED if customer.fact_count else CustomerStatus.NEW
        await session.commit()


async def _research(session: AsyncSession, job: Job) -> None:
    customer = await session.get(Customer, job.customer_id) if job.customer_id else None
    if customer is None:
        await fail(session, job, "RESEARCH_424", "Không tìm thấy hồ sơ khách hàng.")
        return

    depth = (job.params or {}).get("depth", "standard")
    kb_ids: list[str] = (job.params or {}).get("knowledgeBaseIds") or []

    for stale in (
        (
            await session.execute(
                select(CustomerSource).where(CustomerSource.customer_id == customer.id)
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(stale)
    await session.commit()

    reasons: list[str] = []
    slot_status: dict[str, dict[str, Any]] = {
        key: {"name": label, "status": "pending"} for key, label in SOURCE_SLOTS
    }

    await check_cancel(session, job)
    await step(
        session,
        job,
        "crawl",
        f"Đang tìm nguồn công khai về {customer.name} tại {MARKET}",
        0.10,
    )

    if not search_client.configured:
        await fail(
            session,
            job,
            "RESEARCH_503",
            "Chưa cấu hình dịch vụ tìm kiếm (LangSearch/Tavily).",
            stage="crawl",
            sources=list(slot_status.values()),
            reasons=["Thiếu LANGSEARCH_API_KEY và TAVILY_API_KEY."],
        )
        return

    site = clean_website(customer.website or "")
    key = name_key(customer.name)
    found_site: list[str] = []

    def slot_of(hit: SearchHit) -> str:
        host = hit.host
        url = hit.url.lower()

        def matches(hosts: tuple[str, ...]) -> bool:
            return any(host == h or host.endswith(f".{h}") for h in hosts)

        if site and (host == site or host.endswith(f".{site}")):
            return "official"
        if matches(REGISTRY_HOSTS) or host.endswith(".gov.vn"):
            return "registry"
        if "linkedin.com" in host:
            return "linkedin"
        if matches(SOCIAL_HOSTS):
            return "fanpage"
        if matches(CAREER_HOSTS) or any(w in url for w in CAREER_WORDS):
            return "careers"
        if not site and key and key in name_key(host):
            if host not in found_site:
                found_site.append(host)
            return "official"
        return "press"

    async def note_source(hit: SearchHit) -> None:
        host = hit.host
        slot_status[slot_of(hit)]["status"] = "ok"
        await _emit_source(
            session,
            job,
            customer.id,
            name=host,
            url=hit.url,
            title=hit.title,
            status=SourceStatus.OK,
            excerpt=truncate(hit.snippet, 500),
        )

    async def note_step(label: str, progress: float) -> None:
        await step(session, job, "crawl", label, progress)

    agent = await run_agent(
        customer.name,
        customer.website,
        customer.industry,
        max_rounds=6 if depth == "deep" else 3,
        max_sources=settings.research_max_sources,
        on_hit=note_source,
        on_step=note_step,
        admin=await _ai_config(session),
    )
    hits = agent.hits
    trace = plan_trace(agent)
    log.info("research_agent_done", customer=customer.id, **trace)

    # Website chính thức tìm ra được thì ghi vào hồ sơ: lần chạy sau đã có `site:` để
    # neo, và màn Khách hàng hiện đúng domain thay vì để trống.
    if not site and found_site:
        customer.website = found_site[0]
        customer.website_norm = normalize(found_site[0])
        await session.commit()
        site = found_site[0]
        reasons.append(f"Deep Research tự tìm ra website chính thức: {site}")

    if slot_status["official"]["status"] != "ok":
        slot_status["official"].update({"status": "blocked", "reason": "no_result"})
        reasons.append(
            f"Không truy cập được website chính thức của {customer.name} "
            f"({site or 'chưa có website'})."
        )
        await _emit_source(
            session,
            job,
            customer.id,
            name="Website chính thức",
            url=site or None,
            title=None,
            status=SourceStatus.BLOCKED,
            reason="no_result",
        )
    if slot_status["linkedin"]["status"] != "ok":
        slot_status["linkedin"].update({"status": "blocked", "reason": "no_result"})
        reasons.append(f"Không thấy trang LinkedIn công ty của {customer.name}.")
    for key, note in (
        ("registry", f"Không tra được hồ sơ pháp nhân của {customer.name} (mã số thuế, đăng ký)."),
        ("fanpage", f"Không thấy fanpage hoặc kênh social chính chủ của {customer.name}."),
        ("careers", None),
        ("press", f"Không tìm thấy bài báo ngành nào về {customer.name} tại {MARKET}."),
    ):
        if slot_status[key]["status"] != "ok":
            slot_status[key].update({"status": "blocked", "reason": "no_result"})
            if note:
                reasons.append(note)

    await check_cancel(session, job)
    await step(session, job, "kb", "Đang đọc tài liệu nội bộ", 0.35)

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
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        if files:
            slot_status["kb"]["status"] = "ok"
            kb_context = "\n\n".join(
                f"[NỘI BỘ: {f.name}]\n{truncate(f.extracted_text or '', 2500)}" for f in files
            )
            await _emit_source(
                session,
                job,
                customer.id,
                name="Knowledge Base",
                url=None,
                title=f"{len(files)} tài liệu nội bộ",
                status=SourceStatus.OK,
            )
        else:
            slot_status["kb"].update({"status": "blocked", "reason": "no_result"})
    else:
        slot_status["kb"].update({"status": "pending"})

    if not hits and not kb_context:
        reasons.append(
            f"Không đủ nguồn công khai tại {MARKET} để dựng hồ sơ. "
            "Hãy nhập dữ kiện tay hoặc tải tài liệu lên."
        )

        customer.status = CustomerStatus.NEW
        customer.source_count = 0
        customer.fact_count = 0
        customer.opportunity_count = 0
        await session.commit()
        await fail(
            session,
            job,
            "RESEARCH_424",
            f"Không đủ nguồn công khai để dựng hồ sơ {customer.name} tại {MARKET}",
            stage="crawl",
            sources=list(slot_status.values()),
            reasons=reasons,
        )
        return

    await check_cancel(session, job)
    await step(session, job, "extract", f"Đang rút dữ kiện về hoạt động tại {MARKET}", 0.55)

    if not maas.configured:
        await fail(
            session,
            job,
            "RESEARCH_503",
            "Chưa cấu hình AI_PLATFORM_API_KEY — không gọi được MaaS.",
            stage="extract",
            sources=list(slot_status.values()),
            reasons=["Thiếu AI_PLATFORM_API_KEY."],
        )
        return

    budget = model_profile("research.extract").input_budget_chars

    def build_prompt(share: float) -> str:
        body = build_corpus(agent, int(budget * share * 0.85))
        if kb_context:
            body = f"{body}\n\n{truncate(kb_context, int(budget * share * 0.15))}"
        return (
            f"{today_context()}\n\n"
            f"DOANH NGHIỆP: {customer.name}\n"
            f"WEBSITE: {customer.website or 'chưa rõ'}\n"
            f"NGÀNH: {customer.industry or 'chưa rõ'}\n"
            f"THỊ TRƯỜNG CẦN PHÂN TÍCH: {MARKET}\n"
            f"SỐ NGUỒN THU ĐƯỢC: {len(hits)} qua {agent.rounds} vòng tìm kiếm\n"
            f"GÓC ĐÃ PHỦ: {', '.join(sorted(agent.covered)) or 'chưa rõ'}\n\n"
            f"CÁC NGUỒN:\n{body}"
        )

    ai_config = await _ai_config(session)
    parsed: Any = None
    last_error: Exception | None = None

    # Lần 2 rút ngắn ngữ liệu và hạ trần sinh: gateway MaaS cắt ở khoảng 900s, nên
    # một lần chạy dài không phải lý do để cả job thất bại.
    for attempt, (share, cap) in enumerate(EXTRACT_ATTEMPTS, start=1):
        try:
            completion = await maas.run(
                "research.extract",
                EXTRACT_SYSTEM,
                build_prompt(share),
                admin=ai_config,
                max_tokens=cap,
            )
            parsed = completion.as_json()
            log.info(
                "research_extract_ok",
                customer=customer.id,
                attempt=attempt,
                tokens_in=completion.tokens_in,
                tokens_out=completion.tokens_out,
                finish=completion.finish_reason,
            )
            break
        except Exception as exc:
            last_error = exc
            log.warning(
                "research_extract_failed",
                customer=customer.id,
                attempt=attempt,
                share=share,
                cap=cap,
                error=str(exc)[:200],
            )
            if attempt < len(EXTRACT_ATTEMPTS):
                await step(
                    session,
                    job,
                    "extract",
                    "Ngữ liệu quá dài — đang rút gọn rồi thử lại",
                    0.55,
                )

    if parsed is None:
        code, message = (
            ("RESEARCH_503", f"Dịch vụ AI không phản hồi: {last_error}")
            if isinstance(last_error, MaasError)
            else ("RESEARCH_424", "Model không trả về dữ liệu đọc được.")
        )
        await fail(
            session,
            job,
            code,
            message,
            stage="extract",
            sources=list(slot_status.values()),
            reasons=[str(last_error)[:300]],
        )
        return

    if not isinstance(parsed, dict):
        parsed = {}

    raw_facts: list[dict[str, Any]] = list(parsed.get("facts") or [])
    for opportunity in parsed.get("opportunities") or []:
        if isinstance(opportunity, dict):
            raw_facts.append({**opportunity, "isOpportunity": True})

    if not raw_facts:
        reasons.append(
            f"Các nguồn tìm được không chứa dữ kiện nào về {customer.name} tại {MARKET}."
        )
        customer.status = CustomerStatus.NEW
        customer.source_count = len(hits)
        customer.fact_count = 0
        await session.commit()
        await fail(
            session,
            job,
            "RESEARCH_424",
            f"Không rút được dữ kiện nào về {customer.name} tại {MARKET}",
            stage="extract",
            sources=list(slot_status.values()),
            reasons=reasons,
        )
        return

    # Dọn dữ kiện của các lần chạy trước. Không lọc theo verdict: dữ kiện có
    # confidence >= ngưỡng được gán verdict OK bởi **máy**, nên lọc theo verdict để
    # lại toàn bộ chúng — Long Châu từng có 3 bản “Quy mô hệ thống” với 3 con số khác
    # nhau (2.417 / 2.639 / 2.678 nhà thuốc). Mốc đúng là verified_by_id: có người
    # xác minh thì giữ, còn lại thay bằng kết quả mới.
    for stale_fact in (
        (
            await session.execute(
                select(CustomerFact).where(
                    CustomerFact.customer_id == customer.id,
                    CustomerFact.origin == "research",
                    CustomerFact.verified_by_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(stale_fact)
    await session.commit()

    threshold = await _auto_approve_threshold(session)
    fact_count = 0
    opportunity_count = 0

    for entry in raw_facts:
        if not isinstance(entry, dict) or not entry.get("summary"):
            continue
        confidence = int(entry.get("confidence") or 0)
        is_opportunity = bool(entry.get("isOpportunity"))

        row = CustomerFact(
            id=CustomerFact.make_id(),
            customer_id=customer.id,
            topic=truncate(str(entry.get("topic") or "Dữ kiện"), 180),
            summary=str(entry["summary"]),
            confidence=max(0, min(100, confidence)),
            verdict=FactVerdict.OK if confidence >= threshold else FactVerdict.AI,
            source_url=entry.get("sourceUrl"),
            origin="research",
            job_id=job.id,
            is_opportunity=is_opportunity,
        )
        session.add(row)
        await session.commit()

        fact_count += 1
        if is_opportunity:
            opportunity_count += 1

        await bus.publish(
            session,
            job.id,
            JobEventType.FACT,
            {
                "id": row.id,
                "topic": row.topic,
                "summary": row.summary,
                "confidence": row.confidence,
                "verdict": str(row.verdict),
                "isOpportunity": row.is_opportunity,
                "sourceUrl": row.source_url,
            },
            message=f"{row.topic}: {truncate(row.summary, 120)}",
        )

    await check_cancel(session, job)
    await step(session, job, "profile", "Đang dựng hồ sơ khách hàng", 0.85)

    profile = parsed.get("profile") if isinstance(parsed.get("profile"), dict) else {}
    if profile:
        merged = dict(customer.profile or {})
        merged.update({k: v for k, v in profile.items() if v})

        merged["market"] = MARKET
        customer.profile = merged
        if profile.get("industry") and not customer.industry:
            customer.industry = str(profile["industry"])[:120]

    customer.source_count = len(hits) + (1 if slot_status["kb"]["status"] == "ok" else 0)
    customer.fact_count = fact_count
    customer.opportunity_count = opportunity_count
    customer.researched_at = now()
    customer.status = CustomerStatus.RESEARCHED

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer.id,
            actor_id=job.created_by_id,
            kind=ActivityKind.RESEARCH_DONE,
            title="Deep Research hoàn tất",
            body=(
                f"{customer.source_count} nguồn, {fact_count} dữ kiện, "
                f"{opportunity_count} cơ hội (thị trường {MARKET})."
            ),
            meta={"jobId": job.id, "market": settings.research_market},
        )
    )
    await session.commit()

    if job.created_by_id:
        owner = await session.get(User, job.created_by_id)
        if owner:
            await push(
                session,
                user_id=owner.id,
                kind=NotificationType.RESEARCH_DONE,
                title="Deep Research hoàn tất",
                body=(
                    f"{customer.name} — {customer.source_count} nguồn, "
                    f"{fact_count} dữ kiện, {opportunity_count} cơ hội mới."
                ),
                target_url=f"/customers/{customer.id}",
            )
            await email_if_opted_in(
                session,
                owner,
                NotificationType.RESEARCH_DONE,
                lambda: __import__(
                    "app.integrations.resend_client", fromlist=["resend"]
                ).resend.send_research_done(
                    owner.email,
                    customer_name=customer.name,
                    source_count=customer.source_count,
                    fact_count=fact_count,
                    opportunity_count=opportunity_count,
                    url=f"/customers/{customer.id}",
                ),
            )
            await session.commit()

    await finish(
        session,
        job,
        {
            "sourceCount": customer.source_count,
            "factCount": fact_count,
            "opportunityCount": opportunity_count,
            "market": settings.research_market,
            "searchRounds": agent.rounds,
            "queriesRun": len(agent.queries_run),
            "coveredAngles": sorted(agent.covered),
        },
    )


async def _ai_config(session: AsyncSession):
    from app.db.models.admin import AiConfig

    return await session.get(AiConfig, 1)


async def _auto_approve_threshold(session: AsyncSession) -> int:
    config = await _ai_config(session)
    return config.auto_approve_threshold if config else 85


__all__ = ["build_queries", "run_research", "vn_rank"]
