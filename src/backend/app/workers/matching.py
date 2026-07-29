import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import today_context
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import now
from app.core.text import truncate
from app.db.models.customer import Customer, CustomerFact, CustomerSolution
from app.db.models.job import Job
from app.db.models.product import Product
from app.integrations.maas import MaasError, maas
from app.integrations.search import search_client
from app.workers.base import JobCancelled, check_cancel, claim, fail, finish, step

log = get_logger(__name__)

MARKET = settings.research_market_label

MATCH_SYSTEM = f"""\
Bạn là Solution Consultant của Zalo, phụ trách thị trường {MARKET}.

Nhiệm vụ: đối chiếu dữ kiện về doanh nghiệp với danh mục sản phẩm Zalo và đề
xuất các phương án phù hợp.

Quy tắc:
- Chỉ đề xuất sản phẩm có trong DANH MỤC. Không bịa sản phẩm.
- Mỗi phương án phải nêu rõ dựa trên dữ kiện nào (dùng id dữ kiện).
- `fitScore` 0-100: mức phù hợp thực tế, không phải mức mong muốn bán.
- `package` chỉ được chọn trong danh sách gói của chính sản phẩm đó.
- `pricingNote`: **chỉ ghi khi DANH MỤC có mục Giá cho sản phẩm đó.** Danh mục
  không có giá thì để chuỗi rỗng — tuyệt đối không suy đoán con số, vì ghi chú này
  đi thẳng vào proposal gửi khách.
- Bối cảnh {MARKET}: cân nhắc hành vi người dùng Zalo trong nước, quy định
  trong nước, và kênh phân phối nội địa.
- Tiếng Việt, ngắn gọn.

Trả về DUY NHẤT JSON:
{{"solutions":[{{"productId":"<prd_...>","title":"<tên phương án>",
   "rationale":"<vì sao phù hợp, 1-3 câu>","fitScore":<0-100>,
   "expectedImpact":"<kết quả kỳ vọng>","package":"<gói đề xuất>",
   "pricingNote":"<ghi chú chi phí nếu có>",
   "evidence":[{{"kind":"fact","id":"<f_...>"}}]}}]}}
"""


def _price_line(entry: Any) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    parts = [str(entry.get(k)) for k in ("package", "price", "note") if entry.get(k)]
    return " ".join(parts)


def _catalogue_entry(p: Product) -> str:
    lines = [
        f"  - [{p.id}] {p.name} ({p.family} / {p.group_name})",
        f"      Mô tả: {truncate(p.description or '—', 320)}",
    ]
    if p.positioning:
        lines.append(f"      Định vị: {truncate(p.positioning, 240)}")
    if p.capabilities:
        lines.append(f"      Năng lực: {', '.join(p.capabilities)}")
    if p.target_industries:
        lines.append(f"      Ngành phù hợp: {', '.join(p.target_industries)}")
    if p.packages:
        lines.append(f"      Gói: {', '.join(p.packages)}")
    if p.use_cases:
        titles = [
            str(u.get("title")) for u in p.use_cases if isinstance(u, dict) and u.get("title")
        ]
        if titles:
            lines.append(f"      Use case: {', '.join(titles[:6])}")
    if p.pricing:
        priced = [_price_line(row) for row in p.pricing]
        lines.append(f"      Giá: {' | '.join(x for x in priced if x)}")
    else:
        lines.append("      Giá: chưa có dữ liệu — để pricingNote rỗng")
    return "\n".join(lines)


async def run_product_matching(ctx: dict[str, Any], job_id: str) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        job = await claim(session, job_id)
        if job is None:
            return
        try:
            await _match(session, job)
        except JobCancelled:
            return
        except Exception as exc:
            log.exception("matching_crashed", job=job_id, error=str(exc))
            await fail(session, job, "MATCHING_FAILED", f"Không khớp được sản phẩm: {exc}"[:280])


async def _match(session: AsyncSession, job: Job) -> None:
    customer = await session.get(Customer, job.customer_id) if job.customer_id else None
    if customer is None:
        await fail(session, job, "MATCHING_FAILED", "Không tìm thấy khách hàng.")
        return
    if not maas.configured:
        await fail(session, job, "MATCHING_FAILED", "Chưa cấu hình AI_PLATFORM_API_KEY.")
        return

    await check_cancel(session, job)
    await step(session, job, "collect", "Đang đọc dữ kiện khách hàng", 0.2)

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
    if not facts:
        await fail(
            session,
            job,
            "MATCHING_FAILED",
            "Chưa có dữ kiện nào để khớp sản phẩm. Hãy chạy Deep Research trước.",
        )
        return

    products = list(
        (
            await session.execute(
                select(Product).where(Product.status == "active").order_by(Product.sort_order)
            )
        )
        .scalars()
        .all()
    )
    if not products:
        await fail(session, job, "MATCHING_FAILED", "Danh mục sản phẩm đang trống.")
        return

    await check_cancel(session, job)
    await step(session, job, "match", "Đang khớp sản phẩm với nhu cầu", 0.55)

    prompt = (
        f"{today_context()}\n\n"
        f"KHÁCH HÀNG: {customer.name}\n"
        f"NGÀNH: {customer.industry or 'chưa rõ'}\n"
        f"THỊ TRƯỜNG: {MARKET}\n"
        f"HỒ SƠ: {customer.profile or {}}\n\n"
        "DỮ KIỆN:\n"
        + "\n".join(
            f"  - [{f.id}] ({f.topic}, {f.confidence}%"
            f"{', CƠ HỘI' if f.is_opportunity else ''}) {truncate(f.summary, 240)}"
            for f in facts
        )
        + "\n\nDANH MỤC SẢN PHẨM:\n"
        + "\n".join(_catalogue_entry(p) for p in products)
    )

    try:
        completion = await maas.run("matching.solutions", MATCH_SYSTEM, prompt)
        parsed = completion.as_json()
    except MaasError as exc:
        await fail(session, job, "MATCHING_FAILED", f"Dịch vụ AI không phản hồi: {exc}"[:280])
        return

    entries = (parsed or {}).get("solutions") if isinstance(parsed, dict) else None
    if not entries:
        await fail(session, job, "MATCHING_FAILED", "Model không trả về phương án nào.")
        return

    await check_cancel(session, job)
    await step(session, job, "save", "Đang lưu phương án", 0.85)

    valid_ids = {p.id for p in products}

    for stale in (
        (
            await session.execute(
                select(CustomerSolution).where(
                    CustomerSolution.customer_id == customer.id,
                    CustomerSolution.selected.is_(False),
                )
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(stale)
    await session.commit()

    created = 0
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("title"):
            continue
        product_id = entry.get("productId")
        if product_id and product_id not in valid_ids:
            product_id = None
        session.add(
            CustomerSolution(
                id=CustomerSolution.make_id(),
                customer_id=customer.id,
                product_id=product_id,
                title=truncate(str(entry["title"]), 255),
                rationale=entry.get("rationale"),
                fit_score=float(max(0, min(100, int(entry.get("fitScore") or 0)))),
                expected_impact=entry.get("expectedImpact"),
                pricing_note=entry.get("pricingNote"),
                package=entry.get("package"),
                evidence=[e for e in (entry.get("evidence") or []) if isinstance(e, dict)],
                job_id=job.id,
            )
        )
        created += 1
    await session.commit()

    await finish(session, job, {"solutionCount": created})


CRAWL_SYSTEM = f"""\
Bạn là chuyên viên quản lý danh mục sản phẩm Zalo tại {MARKET}.
Đọc nội dung trang chính thức và cập nhật mô tả sản phẩm.

Trả về DUY NHẤT JSON:
{{"description":"<2-3 câu>","positioning":"<định vị>",
  "capabilities":["<năng lực>"],"packages":["<tên gói>"],
  "targetIndustries":["<ngành>"],
  "useCases":[{{"title":"<use case>","summary":"<mô tả>"}}],
  "pricing":[{{"package":"<gói>","price":"<giá kèm đơn vị>","note":"<hạn mức, điều kiện>"}}]}}

Quy tắc:
- **Chỉ dùng thông tin có trong NGUỒN.** Thiếu thì để mảng rỗng hoặc chuỗi rỗng.
- `pricing`: sao lại đúng con số và đơn vị như nguồn ghi (ví dụ “1.000.000đ/năm”,
  “từ 200đ/tin”). Không quy đổi, không làm tròn, không suy đoán.
- `note` của pricing: ghi hạn mức đi kèm nếu nguồn có (số nhân viên, số tin/tháng).
- Giá **không theo gói** vẫn phải đưa vào `pricing`, để `package` rỗng — ví dụ nguồn
  ghi “từ 200đ mỗi tin gửi thành công” thì trả
  `{{"package":"","price":"từ 200đ/tin","note":"tính theo tin gửi thành công"}}`.
  Đơn giá theo lượt dùng là thông tin quan trọng nhất với BD, đừng bỏ sót.
- Tên gói giữ nguyên tiếng Việt như nguồn (“Tiêu chuẩn”, “Tăng trưởng”, “Toàn diện”).
"""

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")
_PRICE_HINT_RE = re.compile(r"(pricing|bang-?gia|gia-?ca|price)", re.I)

FETCH_TIMEOUT = 20.0
MAX_PAGES = 3
MAX_PAGE_CHARS = 24_000


def html_to_text(html: str) -> str:
    body = _TAG_RE.sub(" ", html)
    body = body.replace("</p>", "\n").replace("<br", "\n<br").replace("</li>", "\n")
    body = _ANY_TAG_RE.sub(" ", body)
    body = unescape(body)
    body = _WS_RE.sub(" ", body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    return _BLANK_RE.sub("\n\n", body).strip()


def _price_links(html: str, base: str) -> list[str]:
    out: list[str] = []
    host = urlparse(base).netloc
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = match.group(1)
        if not _PRICE_HINT_RE.search(href):
            continue
        absolute = urljoin(base, href)
        if urlparse(absolute).netloc != host or absolute in out or absolute == base:
            continue
        out.append(absolute)
    return out


async def fetch_pages(url: str) -> list[tuple[str, str]]:
    """Fetch trang sản phẩm và lần thêm trang bảng giá cùng host (sâu 1 cấp).

    Bản cũ chỉ đưa source_url vào search engine — với URL đã biết thì đó là đường
    vòng, và bảng giá thường nằm ở trang con nên search hầu như không lấy được.
    """
    pages: list[tuple[str, str]] = []
    headers = {"User-Agent": "zBD-Agent/1.0 (+product catalogue sync)"}
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT, follow_redirects=True, headers=headers
    ) as client:
        try:
            first = await client.get(url)
            first.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("product_fetch_failed", url=url, error=str(exc))
            return pages

        pages.append((url, html_to_text(first.text)[:MAX_PAGE_CHARS]))

        for extra in _price_links(first.text, str(first.url))[: MAX_PAGES - 1]:
            try:
                more = await client.get(extra)
                more.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("product_fetch_failed", url=extra, error=str(exc))
                continue
            pages.append((extra, html_to_text(more.text)[:MAX_PAGE_CHARS]))

    return pages


async def run_product_crawl(ctx: dict[str, Any], job_id: str) -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        job = await claim(session, job_id)
        if job is None:
            return
        try:
            await _crawl(session, job)
        except JobCancelled:
            return
        except Exception as exc:
            log.exception("product_crawl_crashed", job=job_id, error=str(exc))
            await fail(session, job, "CRAWL_FAILED", f"Không đồng bộ được: {exc}"[:280])


async def _crawl(session: AsyncSession, job: Job) -> None:
    product_id = (job.params or {}).get("productId")
    product = await session.get(Product, product_id) if product_id else None
    if product is None:
        await fail(session, job, "CRAWL_FAILED", "Không tìm thấy sản phẩm.")
        return

    await check_cancel(session, job)
    await step(session, job, "fetch", f"Đang tải nguồn của {product.name}", 0.3)

    pages: list[tuple[str, str]] = []
    source = "fetch"

    if product.source_url:
        pages = await fetch_pages(product.source_url)

    if not pages:
        source = "search"
        query = product.source_url or f"{product.name} Zalo {MARKET}"
        outcome = await search_client.search(query, limit=4)
        if not outcome.ok or not outcome.hits:
            await fail(
                session,
                job,
                "CRAWL_FAILED",
                f"Không lấy được nội dung nguồn ({outcome.reason or 'no_result'}).",
            )
            return
        pages = [(h.url, truncate(h.content or h.snippet, 3000)) for h in outcome.hits]

    await check_cancel(session, job)
    await step(session, job, "parse", "Đang cập nhật thông tin sản phẩm", 0.7)

    corpus = "\n\n".join(f"[{url}]\n{text}" for url, text in pages)
    log.info("product_crawl_source", product=product.id, mode=source, pages=len(pages))

    try:
        completion = await maas.run(
            "product.crawl",
            CRAWL_SYSTEM,
            f"SẢN PHẨM: {product.name}\n\nNGUỒN:\n{corpus[:30000]}",
        )
        parsed = completion.as_json()
    except MaasError as exc:
        await fail(session, job, "CRAWL_FAILED", f"Dịch vụ AI không phản hồi: {exc}"[:280])
        return

    if isinstance(parsed, dict):
        if parsed.get("description"):
            product.description = str(parsed["description"])
        if parsed.get("positioning"):
            product.positioning = str(parsed["positioning"])
        for field, key in (
            ("capabilities", "capabilities"),
            ("packages", "packages"),
            ("target_industries", "targetIndustries"),
            ("use_cases", "useCases"),
            ("pricing", "pricing"),
        ):
            value = parsed.get(key)
            if value:
                setattr(product, field, value)

    product.last_crawled_at = now()
    await session.commit()

    await finish(
        session,
        job,
        {
            "productId": product.id,
            "mode": source,
            "sourceCount": len(pages),
            "sources": [url for url, _ in pages],
            "pricingRows": len(product.pricing or []),
            "useCases": len(product.use_cases or []),
        },
    )
