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
from app.db.models.product import Product, ProductService
from app.integrations.maas import MaasError, maas
from app.integrations.search import search_client
from app.workers.base import JobCancelled, check_cancel, claim, fail, finish, step

log = get_logger(__name__)

MARKET = settings.research_market_label
FOCUS = settings.focus_prompt_block

MATCH_SYSTEM = f"""\
Bạn là Solution Consultant của Zalo, phụ trách thị trường {MARKET}.

Nhiệm vụ: đi từ dữ kiện Deep Research -> tìm painpoint, cơ hội và tiềm năng
khai thác của doanh nghiệp -> rồi mới chọn sản phẩm Zalo giải quyết được điều đó.

Trình tự bắt buộc, không đảo ngược:
1. Đọc DỮ KIỆN và nhận diện painpoint / cơ hội: doanh nghiệp đang tắc ở đâu,
   tốn kém ở đâu, đang bỏ ngỏ cơ hội nào, đã có sẵn nền tảng gì để khai thác
   thêm. Dữ kiện gắn CƠ HỘI là gợi ý mạnh nhưng không phải danh sách đóng —
   painpoint có thể suy ra từ nhiều dữ kiện ghép lại.
2. Với mỗi painpoint/cơ hội đáng theo, chọn DỊCH VỤ CON trong DANH MỤC giải
   quyết được nó — mỗi sản phẩm lớn gồm nhiều dịch vụ con, từng dịch vụ ghi
   rõ GIẢI QUYẾT gì và TÍN HIỆU nhu cầu nào. Không có dịch vụ nào giải quyết
   được thì bỏ painpoint đó — đừng nhồi cho đủ số lượng.

Về ngành: sản phẩm Zalo đều có thể áp dụng cho nhiều ngành. KHÔNG chọn sản phẩm
theo ngành của doanh nghiệp, và KHÔNG loại sản phẩm chỉ vì mục “Ngành phù hợp”
của nó không có ngành này — mục đó chỉ liệt kê ví dụ đã triển khai, không phải
giới hạn. Căn cứ duy nhất là painpoint có khớp năng lực sản phẩm hay không.

{FOCUS}
Quy tắc:
- Chỉ đề xuất dịch vụ/sản phẩm có trong DANH MỤC. Không bịa.
- MỖI SẢN PHẨM xuất hiện TỐI ĐA MỘT LẦN trong "solutions". Sản phẩm đáp ứng
  nhiều painpoint thì thêm nhiều phần tử vào "services" của nó — mỗi dịch vụ
  một painpoint/cơ hội/lý do riêng. KHÔNG tách thành nhiều phương án trùng
  sản phẩm.
- `serviceId`: id dịch vụ con (svc_...) khớp painpoint nhất của từng mục trong
  "services". Nhu cầu phủ toàn sản phẩm mà không dịch vụ con nào khớp thì để
  serviceId chuỗi rỗng.
- Mỗi phương án phải nêu rõ dựa trên dữ kiện nào (dùng id dữ kiện).
- `painpoint`: nói đúng cái khách đang vướng hoặc đang bỏ ngỏ, đọc ra từ dữ kiện.
  Không viết chung chung kiểu “cần chuyển đổi số” — phải cụ thể tới mức người
  bán đọc là biết mở đầu cuộc nói chuyện thế nào.
- `opportunity`: cơ hội/tiềm năng khai thác được nếu giải quyết painpoint đó.
- `fitScore` 0-100: mức khớp giữa painpoint và năng lực sản phẩm, không phải
  mức mong muốn bán. Painpoint mờ nhạt thì điểm phải thấp.
- `package` chỉ được chọn trong danh sách Gói của SẢN PHẨM CHA chứa dịch vụ đó.
- `pricingNote`: **chỉ ghi khi DANH MỤC có mục Giá cho sản phẩm đó.** Danh mục
  không có giá thì để chuỗi rỗng — tuyệt đối không suy đoán con số, vì ghi chú này
  đi thẳng vào proposal gửi khách.
- Dòng ĐIỀU KIỆN trong DANH MỤC là điều kiện tiên quyết, không phải tùy chọn
  (ví dụ: ZBS Template Message cần Zalo OA gói Tăng trưởng hoặc Toàn diện, sau
  đó mới tính đơn giá mỗi tin). Đề xuất dịch vụ có ĐIỀU KIỆN thì `rationale`
  hoặc `pricingNote` phải nhắc điều kiện đó — người bán cần biết phải bán kèm
  gì; không được đề xuất như thể dùng độc lập được ngay.
- Bối cảnh {MARKET}: cân nhắc hành vi người dùng Zalo trong nước, quy định
  trong nước, và kênh phân phối nội địa.
- Tiếng Việt, ngắn gọn.

Trả về DUY NHẤT JSON — MỖI SẢN PHẨM CHỈ MỘT PHẦN TỬ trong "solutions"; mọi
dịch vụ khớp của sản phẩm đó nằm trong mảng "services" của chính nó:
{{"solutions":[{{"productId":"<prd_...>",
   "title":"<tên phương án chung cho sản phẩm>",
   "fitScore":<0-100 — bằng điểm của dịch vụ khớp nhất>,
   "services":[{{"serviceId":"<svc_...>",
      "painpoint":"<painpoint/nhu cầu cụ thể đọc ra từ dữ kiện>",
      "opportunity":"<cơ hội hoặc tiềm năng khai thác được>",
      "rationale":"<dịch vụ giải quyết painpoint đó thế nào, 1-3 câu>",
      "fitScore":<0-100>,
      "expectedImpact":"<kết quả kỳ vọng>","package":"<gói đề xuất>",
      "pricingNote":"<ghi chú chi phí nếu có>",
      "evidence":[{{"kind":"fact","id":"<f_...>"}}]}}]}}]}}
"""

def _price_line(entry: Any) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    parts = [str(entry.get(k)) for k in ("package", "price", "note") if entry.get(k)]
    return " ".join(parts)


def _fmt_requires(requires: list[dict[str, Any]] | None) -> str:
    """Ghép danh sách điều kiện tiên quyết thành một dòng cho catalogue."""
    parts: list[str] = []
    for r in requires or []:
        if not isinstance(r, dict):
            continue
        note = str(r.get("note") or "").strip()
        if not note:
            ref = str(r.get("ref") or "")
            pkgs = [str(x) for x in (r.get("packages") or [])]
            note = f"cần {ref}" + (f" (gói {', '.join(pkgs)})" if pkgs else "")
        if r.get("optional"):
            note += " (tùy chọn)"
        parts.append(note)
    return " • ".join(parts)


def _product_requires(p: Product) -> str:
    req = _fmt_requires(getattr(p, "requires", None))
    if not req:
        return ""
    return f"\n      ĐIỀU KIỆN (áp dụng cho mọi dịch vụ bên dưới): {req}"


def _service_lines(services: list["ProductService"]) -> str:
    """Khối DỊCH VỤ CON nối vào sau mỗi sản phẩm trong catalogue.

    Mỗi dòng là một đơn vị match được: solves/signals viết bằng ngôn ngữ
    painpoint nên LLM đối chiếu thẳng với dữ kiện, không phải suy từ mô tả
    tính năng chung chung của sản phẩm lớn.
    """
    if not services:
        return ""
    lines = ["      DỊCH VỤ CON (đơn vị để match — dùng id svc_... này):"]
    for s in services:
        lines.append(f"      * [{s.id}] {s.name}")
        if s.solves:
            lines.append(f"          GIẢI QUYẾT: {truncate(s.solves, 280)}")
        if s.signals:
            lines.append(f"          TÍN HIỆU NHU CẦU: {', '.join(s.signals[:8])}")
        if s.pricing_ref:
            priced = [x for x in (_price_line(row) for row in s.pricing_ref) if x]
            if priced:
                lines.append(f"          GIÁ: {' | '.join(priced)}")
        req = _fmt_requires(getattr(s, "requires", None))
        if req:
            lines.append(f"          ĐIỀU KIỆN: {req}")
    return "\n" + "\n".join(lines)


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

    # Dịch vụ con — đơn vị matching thật sự. Sản phẩm lớn chỉ là ngữ cảnh pitch.
    services = list(
        (
            await session.execute(
                select(ProductService)
                .where(ProductService.status == "active")
                .order_by(ProductService.product_id, ProductService.sort_order)
            )
        )
        .scalars()
        .all()
    )
    svc_by_product: dict[str, list[ProductService]] = {}
    for svc in services:
        svc_by_product.setdefault(svc.product_id, []).append(svc)

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
        + "\n\nDANH MỤC SẢN PHẨM (mỗi sản phẩm liệt kê DỊCH VỤ CON — match theo dịch vụ con):\n"
        + "\n".join(
            _catalogue_entry(p)
            + _product_requires(p)
            + _service_lines(svc_by_product.get(p.id, []))
            for p in products
        )
    )

    try:
        # Skill stage Phương án (MATCHING) — cách đội BD muốn chấm và ưu tiên.
        from app.db.models.enums import SkillStage
        from app.services.skill_prompt import log_and_build

        skill_hint = await log_and_build(
            session,
            SkillStage.MATCHING,
            owner_id=job.created_by_id,
            where="matching",
            job_id=job.id,
        )
        completion = await maas.run(
            "matching.solutions", MATCH_SYSTEM + skill_hint, prompt
        )
        parsed = completion.as_json()
    except MaasError as exc:
        await fail(session, job, "MATCHING_FAILED", f"Dịch vụ AI không phản hồi: {exc}"[:280])
        return

    # Model có thể trả {"solutions":[...]} đúng như yêu cầu, hoặc trả thẳng
    # mảng [...] (hay bỏ vào khoá khác). Nội dung vẫn dùng được nên nhận cả,
    # thay vì để job chết chỉ vì cái vỏ bọc.
    if isinstance(parsed, dict):
        entries = parsed.get("solutions")
        if not isinstance(entries, list):
            # một số model gói dưới khoá khác; lấy mảng đầu tiên tìm được
            entries = next(
                (v for v in parsed.values() if isinstance(v, list) and v),
                None,
            )
    elif isinstance(parsed, list):
        entries = parsed
    else:
        entries = None
    if not entries:
        await fail(session, job, "MATCHING_FAILED", "Model không trả về phương án nào.")
        return

    await check_cancel(session, job)
    await step(session, job, "save", "Đang lưu phương án", 0.85)

    valid_ids = {p.id for p in products}
    svc_by_id = {s.id: s for s in services}

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
    # MỘT phương án cho MỖI sản phẩm. Server tự gom theo product cha nên kể cả
    # khi model trả dạng phẳng cũ (mỗi dịch vụ một phần tử, trùng sản phẩm)
    # kết quả vẫn không nhân bản. Field đơn của phương án (painpoint, gói...)
    # lấy từ dịch vụ điểm cao nhất; chi tiết đầy đủ nằm trong service_matches.
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        svc_items = entry.get("services")
        if not isinstance(svc_items, list) or not svc_items:
            svc_items = [entry]  # dạng phẳng cũ -> bọc thành một dịch vụ
        entry_pid = entry.get("productId")
        for it in svc_items:
            if not isinstance(it, dict):
                continue
            service_id = it.get("serviceId")
            svc = svc_by_id.get(service_id) if service_id else None
            pid = svc.product_id if svc is not None else entry_pid
            if svc is None:
                service_id = None
                if pid and pid not in valid_ids:
                    pid = None
            if pid is None:
                continue
            try:
                score = float(max(0, min(100, int(it.get("fitScore") or entry.get("fitScore") or 0))))
            except (TypeError, ValueError):
                score = 0.0
            g = grouped.get(pid)
            if g is None:
                g = {"title": entry.get("title") or "", "best": None,
                     "matches": [], "evidence": [], "seen": set()}
                grouped[pid] = g
                order.append(pid)
            if not g["title"] and entry.get("title"):
                g["title"] = entry["title"]
            detail = {
                "serviceId": service_id,
                "serviceName": svc.name if svc is not None else None,
                "fitScore": score,
                "painpoint": it.get("painpoint"),
                "opportunity": it.get("opportunity"),
                "rationale": it.get("rationale"),
                "expectedImpact": it.get("expectedImpact"),
                "package": it.get("package"),
                "pricingNote": it.get("pricingNote"),
                "evidence": [e for e in (it.get("evidence") or []) if isinstance(e, dict)],
            }
            g["matches"].append(detail)
            for e in detail["evidence"]:
                key = (e.get("kind"), e.get("id"))
                if key not in g["seen"]:
                    g["seen"].add(key)
                    g["evidence"].append(e)
            if g["best"] is None or score > g["best"]["fitScore"]:
                g["best"] = detail

    for pid in order:
        g = grouped[pid]
        best = g["best"] or {}
        title = truncate(str(g["title"] or best.get("serviceName") or ""), 255)
        if not title:
            continue
        session.add(
            CustomerSolution(
                id=CustomerSolution.make_id(),
                customer_id=customer.id,
                product_id=pid,
                service_id=best.get("serviceId"),
                service_matches=sorted(
                    g["matches"], key=lambda m: -(m.get("fitScore") or 0)
                ),
                title=title,
                rationale=best.get("rationale"),
                painpoint=best.get("painpoint"),
                opportunity=best.get("opportunity"),
                fit_score=float(best.get("fitScore") or 0),
                expected_impact=best.get("expectedImpact"),
                pricing_note=best.get("pricingNote"),
                package=best.get("package"),
                evidence=g["evidence"],
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
# Số URL nguồn tối đa cho một sản phẩm (source_url + documents) và tổng số
# trang đưa vào prompt. Chặn để corpus không phình khi Hub có nhiều tài liệu.
MAX_SOURCE_URLS = 5
MAX_TOTAL_PAGES = 6
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

    # source_url là nguồn chính; documents là tài liệu chính thức được thêm vào
    # Hub. Trước đây chỉ đọc source_url nên doc thêm vào không vào tri thức.
    doc_urls = [
        str(d.get("url") or d.get("href") or d.get("link")).strip()
        for d in (product.documents or [])
        if isinstance(d, dict) and (d.get("url") or d.get("href") or d.get("link"))
    ]
    seen_urls: set[str] = set()
    source_urls: list[str] = []
    for candidate in ([product.source_url] if product.source_url else []) + doc_urls:
        if candidate and candidate not in seen_urls:
            seen_urls.add(candidate)
            source_urls.append(candidate)

    for candidate in source_urls[:MAX_SOURCE_URLS]:
        if len(pages) >= MAX_TOTAL_PAGES:
            break
        fetched = await fetch_pages(candidate)
        pages.extend(fetched[: MAX_TOTAL_PAGES - len(pages)])

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
    log.info(
        "product_crawl_source",
        product=product.id,
        mode=source,
        pages=len(pages),
        urls=[u for u, _ in pages],
    )

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
