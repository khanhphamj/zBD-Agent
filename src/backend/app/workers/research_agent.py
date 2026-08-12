import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.clock import today_context
from app.core.config import settings
from app.core.logging import get_logger
from app.core.text import clean_website, truncate
from app.integrations.harness import profile
from app.integrations.maas import MaasError, maas
from app.integrations.search import SearchHit, search_client

log = get_logger(__name__)

MARKET = settings.research_market_label
FOCUS = settings.focus_prompt_block

ANGLES = [
    ("profile", "Tổng quan pháp nhân, năm thành lập, trụ sở, cổ đông/tập đoàn mẹ"),
    ("legal", "Hồ sơ đăng ký: mã số thuế, người đại diện, vốn điều lệ, ngành nghề"),
    ("social", "Kênh chính chủ: website, fanpage Facebook, LinkedIn, Zalo OA, TikTok"),
    ("products", "Sản phẩm, dịch vụ, giá, phân khúc khách hàng"),
    ("scale", "Quy mô: doanh thu, số điểm bán, số nhân sự, tăng trưởng"),
    ("market", "Thị trường, thị phần, đối thủ trực tiếp"),
    ("customers", "Tệp khách hàng cuối, hành vi, kênh tiếp cận"),
    ("digital", "Hiện trạng số: app, website, CRM, kênh CSKH, công nghệ đang dùng"),
    ("hiring", "Tuyển dụng — dấu hiệu đầu tư vào digital/data/CX"),
    ("news", "Tin tức mới nhất, thay đổi chiến lược, gọi vốn, mở rộng"),
    ("regulation", "Quy định ngành ảnh hưởng tới kênh liên lạc/dữ liệu khách hàng"),
    ("painpoints", "Điểm đau về kênh liên lạc, chi phí SMS, loyalty, tự động hoá CSKH"),
    ("zalofoot", "Hiện diện Zalo CHÍNH CHỦ của doanh nghiệp: Zalo OA (tên, có dấu "
                 "xác minh), Mini App, ZBS Template (tên cũ ZNS) đang dùng, link Zalo "
                 "gắn trên website/fanpage"),
    ("journey", "Hành trình khách cuối & chi phí kênh: thông báo giao dịch/OTP đang đi "
                "SMS hay app, tổng đài/CSKH, remarketing, loyalty/tích điểm — kênh nào, "
                "quy mô, chi phí nếu nguồn có"),
    ("rivalzalo", "Ngành & đối thủ dùng Zalo thế nào: use case Zalo OA/ZNS/Mini App đã "
                  "có trong ngành; đối thủ trực tiếp nào đã mở OA/Mini App"),
]

PLAN_SYSTEM = f"""\
Bạn là trưởng nhóm nghiên cứu thị trường {MARKET} của đội Business Development Zalo.

Nhiệm vụ: điều phối việc TÌM KIẾM để hiểu doanh nghiệp càng đầy đủ càng tốt.
Bạn không tự trả lời bằng kiến thức có sẵn — bạn ra lệnh tìm kiếm, đọc kết quả,
rồi quyết định cần tìm thêm gì.

Phạm vi bắt buộc: chỉ quan tâm hoạt động của doanh nghiệp **tại {MARKET}**.
Bỏ qua công ty trùng tên ở nước khác và hoạt động của công ty mẹ ở thị trường
ngoài {MARKET}.

{FOCUS}
Nguyên tắc ra truy vấn:
- Truy vấn tiếng Việt là chính; thêm truy vấn tiếng Anh khi cần số liệu ngành.
- Mỗi truy vấn nhắm MỘT góc cụ thể, đừng chung chung.
- Neo vào {MARKET}: thêm "Việt Nam", hoặc dùng `site:` tới domain trong nước.
- Ưu tiên theo thứ tự: kênh chính chủ (website, fanpage Facebook, LinkedIn, Zalo OA)
  → cổng hồ sơ pháp nhân (masothue.com, hosocongty.vn, dangkykinhdoanh.gov.vn,
  công bố thông tin HSX/HNX/SSC) → báo kinh tế trong nước → trang tuyển dụng.
- Nguồn chính chủ và hồ sơ pháp nhân quan trọng hơn bài phân tích của bên thứ ba:
  nếu còn thiếu, hãy ra truy vấn `site:` tới đúng domain đó.
- Bỏ qua bài SEO dịch vụ (thành lập công ty, xin giấy phép, bán phần mềm/SMS) —
  chúng dùng tên doanh nghiệp làm từ khoá chứ không có dữ kiện về doanh nghiệp.
- Đừng lặp lại truy vấn đã chạy, đừng tìm lại góc đã đủ dữ liệu.
- Góc thị trường/đối thủ là BẮT BUỘC: nếu hồ sơ chưa ghi ngành, tự suy ngành từ
  kết quả đã có rồi vẫn phải ra truy vấn thị trường + đối thủ.
- Góc `rivalzalo`: khi kết quả đã lộ tên đối thủ trực tiếp, ra truy vấn
  `"<tên đối thủ>" Zalo OA` hoặc `"<tên đối thủ>" Zalo Mini App` cho 2-3 đối thủ
  lớn nhất — đây là dữ kiện social proof để tư vấn giải pháp Zalo.

Trả về DUY NHẤT JSON:
{{
  "assessment": "<1-2 câu: đang thiếu gì>",
  "covered": ["<mã góc đã đủ>"],
  "searches": [
    {{"query": "<truy vấn>", "angle": "<mã góc>", "recent": <true nếu cần tin mới>}}
  ],
  "done": <true nếu đã đủ để dựng hồ sơ>
}}

Tối đa 6 truy vấn mỗi vòng. Nếu `done` là true thì `searches` để rỗng.
"""


# linkedin.com phục vụ cùng một trang company qua hàng chục subdomain quốc gia
# (vn., cn., tw., bo., sv.…). Không gộp thì một trang chiếm cả chục slot nguồn.
MIRRORED_HOSTS = ("linkedin.com", "facebook.com", "youtube.com", "tiktok.com", "cafef.vn")


def dedupe_key(url: str) -> str:
    raw = re.sub(r"^https?://", "", (url or "").strip().lower()).rstrip("/")
    host, _, path = raw.partition("/")
    host = host.removeprefix("www.").removeprefix("m.")
    for base in MIRRORED_HOSTS:
        if host == base or host.endswith(f".{base}"):
            host = base
            break
    return f"{host}/{path}"


@dataclass(slots=True)
class AgentState:
    hits: list[SearchHit] = field(default_factory=list)
    seen_urls: set[str] = field(default_factory=set)
    queries_run: list[str] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    rounds: int = 0
    assessments: list[str] = field(default_factory=list)

    def add(self, hit: SearchHit) -> bool:
        key = dedupe_key(hit.url)
        if not key or (key.endswith("/") and len(key) < 3) or key in self.seen_urls:
            return False
        self.seen_urls.add(key)
        self.hits.append(hit)
        return True


def seed_queries(name: str, website: str | None, industry: str | None) -> list[tuple[str, str]]:
    """Truy vấn mồi. Mỗi kênh chính chủ (website, LinkedIn, fanpage) và mỗi cổng hồ sơ
    pháp nhân đi một truy vấn `site:` riêng thay vì gộp bằng OR — provider tìm kiếm
    xử lý toán tử OR không đều nhau, gộp lại thường mất hẳn nguồn."""
    site = clean_website(website or "")
    out: list[tuple[str, str]] = []
    if site:
        out.append((f"site:{site}", "profile"))
        out.append((f"site:{site} sản phẩm dịch vụ", "products"))
        out.append((f"site:{site} liên hệ hotline chăm sóc khách hàng", "digital"))
    else:
        out.append((f'"{name}" {MARKET} website chính thức', "profile"))

    out.append((f'site:linkedin.com/company "{name}"', "social"))
    out.append((f'site:facebook.com "{name}"', "social"))
    for registry in ("masothue.com", "hosocongty.vn", "thongtindoanhnghiep.co"):
        out.append((f'site:{registry} "{name}"', "legal"))

    out.append((f"{name} {MARKET} giới thiệu công ty", "profile"))
    out.append((f'"{name}" {MARKET} doanh thu quy mô', "scale"))
    out.append((f"{name} {MARKET} tin tức mới nhất", "news"))
    out.append((f"{name} {MARKET} tuyển dụng", "hiring"))
    out.append((f'"{name}" {MARKET} báo cáo thường niên công bố thông tin', "scale"))
    press = " OR ".join(f"site:{d}" for d in settings.press_domains)
    out.append((f"{name} ({press})", "news"))
    if industry:
        out.append((f"{industry} {MARKET} thị trường đối thủ", "market"))
        out.append((f"{industry} {MARKET} Zalo OA ZNS Mini App", "rivalzalo"))
    # Hiện diện Zalo chính chủ: zalo.me là host trang OA công khai; query thứ hai
    # bắt bài PR/hướng dẫn "X ra mắt Zalo OA/Mini App" khi trang OA không được index.
    out.append((f'site:zalo.me "{name}"', "zalofoot"))
    out.append((f'"{name}" Zalo OA Mini App ZNS', "zalofoot"))
    # Kênh tương tác khách cuối hiện tại — nền cho bài toán ROI chuyển kênh.
    out.append((f'"{name}" chăm sóc khách hàng tổng đài app tích điểm SMS', "journey"))
    return out


def _evidence_digest(state: AgentState, budget: int) -> str:
    lines: list[str] = []
    used = 0
    for i, hit in enumerate(state.hits, start=1):
        block = (
            f"[{i}] {hit.url}\n"
            f"    {truncate(hit.title, 140)}\n"
            f"    {truncate(hit.snippet or hit.content, 320)}"
        )
        if used + len(block) > budget:
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines)


async def run_agent(
    name: str,
    website: str | None,
    industry: str | None,
    *,
    max_rounds: int = 4,
    max_sources: int = 60,
    on_hit: Callable[[SearchHit], Awaitable[None]] | None = None,
    on_step: Callable[[str, float], Awaitable[None]] | None = None,
    admin: Any | None = None,
    guidance: str = "",
) -> AgentState:
    state = AgentState()
    plan_profile = profile("research.plan")

    async def emit(label: str, progress: float) -> None:
        if on_step:
            await on_step(label, progress)

    await emit(f"Đang tìm nguồn công khai về {name} tại {MARKET}", 0.10)
    for query, angle in seed_queries(name, website, industry):
        outcome = await search_client.search(query, limit=6)
        state.queries_run.append(query)
        added = 0
        for hit in outcome.hits:
            if state.add(hit):
                added += 1
                if on_hit:
                    await on_hit(hit)
        if added:
            state.covered.add(angle)

    if not state.hits:
        return state

    hard_ceiling = int(max_sources * 1.5)

    for round_no in range(1, max_rounds + 1):
        missing_now = [code for code, _ in ANGLES if code not in state.covered]
        if not missing_now or len(state.hits) >= hard_ceiling:
            break

        state.rounds = round_no
        await emit(
            f"Vòng {round_no}: model đang rà soát {len(state.hits)} nguồn để tìm thêm góc thiếu",
            0.15 + 0.30 * round_no / max_rounds,
        )

        gaps = [f"{code}: {desc}" for code, desc in ANGLES if code not in state.covered]
        prompt = (
            f"{today_context()}\n\n"
            f"DOANH NGHIỆP: {name}\n"
            f"WEBSITE: {website or 'chưa rõ'}\n"
            f"NGÀNH: {industry or 'chưa rõ'}\n\n"
            f"ĐÃ CHẠY {len(state.queries_run)} TRUY VẤN:\n"
            + "\n".join(f"  - {q}" for q in state.queries_run[-24:])
            + "\n\nGÓC CÒN THIẾU:\n"
            + ("\n".join(f"  - {g}" for g in gaps) or "  (không)")
            + f"\n\nĐÃ THU ĐƯỢC {len(state.hits)} NGUỒN:\n"
            + _evidence_digest(state, plan_profile.input_budget_chars)
        )

        try:
            completion = await maas.run(
                "research.plan", PLAN_SYSTEM + guidance, prompt, admin=admin
            )
            plan = completion.as_json()
        except MaasError as exc:
            log.warning("research_plan_failed", round=round_no, error=str(exc))
            break
        except Exception as exc:
            log.warning("research_plan_unparsable", round=round_no, error=str(exc))
            break

        if not isinstance(plan, dict):
            break

        if plan.get("assessment"):
            state.assessments.append(str(plan["assessment"]))
        for code in plan.get("covered") or []:
            state.covered.add(str(code))

        searches = [s for s in (plan.get("searches") or []) if isinstance(s, dict)]
        if plan.get("done") or not searches:
            break

        new_hits = 0
        for item in searches[:6]:
            query = str(item.get("query") or "").strip()
            if not query or query in state.queries_run:
                continue
            recency = "oneYear" if item.get("recent") else None
            outcome = await search_client.search(query, limit=6, recency=recency)
            state.queries_run.append(query)
            for hit in outcome.hits:
                if len(state.hits) >= hard_ceiling:
                    break
                if state.add(hit):
                    new_hits += 1
                    if on_hit:
                        await on_hit(hit)
            angle = str(item.get("angle") or "").strip()
            if angle and outcome.hits:
                state.covered.add(angle)

        log.info(
            "research_round",
            round=round_no,
            queries=len(searches),
            new_sources=new_hits,
            total_sources=len(state.hits),
        )
        if new_hits == 0:
            break

    return state


def build_corpus(state: AgentState, budget: int) -> str:
    blocks: list[str] = []
    used = 0
    for i, hit in enumerate(state.hits, start=1):
        body = hit.content or hit.snippet
        block = (
            f"[NGUỒN {i}] {hit.url}\n"
            f"TIÊU ĐỀ: {hit.title}\n"
            f"NGÀY: {hit.published_at or 'không rõ'}\n"
            f"{truncate(body, 8000)}\n"
        )
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


def plan_trace(state: AgentState) -> dict[str, Any]:
    return {
        "rounds": state.rounds,
        "queries": state.queries_run,
        "sourceCount": len(state.hits),
        "coveredAngles": sorted(state.covered),
        "missingAngles": sorted({c for c, _ in ANGLES} - state.covered),
        "assessments": state.assessments,
    }


__all__ = [
    "ANGLES",
    "AgentState",
    "build_corpus",
    "dedupe_key",
    "plan_trace",
    "run_agent",
    "seed_queries",
]
