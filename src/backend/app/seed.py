import asyncio
import sys
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select

from app.core.logging import get_logger, setup_logging
from app.core.security import now
from app.core.text import clean_website, initials, normalize
from app.db.models.admin import AiConfig, SystemPolicy
from app.db.models.customer import Customer, CustomerActivity, CustomerFact
from app.db.models.enums import (
    ActivityKind,
    CustomerStatus,
    FactVerdict,
    ProposalStatus,
    Role,
    Scope,
    SkillStage,
    UserStatus,
)
from app.db.models.knowledge import KnowledgeBase, KnowledgeSource
from app.db.models.library import (
    ProposalTemplate,
    Skill,
    SkillVersion,
    TemplateSlide,
)
from app.db.models.product import Product
from app.db.models.proposal import Proposal, ProposalSlide
from app.db.models.user import Team, User, UserNotificationSettings
from app.db.session import session_scope
from app.services.provisioning import pick_avatar_color

log = get_logger(__name__)

DOMAIN = "vng.com.vn"

TEAMS = [
    ("BD Enterprise", "Đội phát triển kinh doanh khối doanh nghiệp lớn"),
    ("BD SMB", "Đội phát triển kinh doanh khối vừa và nhỏ"),
    ("Solution Consulting", "Đội tư vấn giải pháp"),
]

USERS = [
    ("khanhpp", Role.MEMBER, "BD Enterprise"),
    ("anhtuan", Role.MEMBER, "BD Enterprise"),
    ("minhthu", Role.MEMBER, "BD SMB"),
    ("quochuy", Role.MEMBER, "BD SMB"),
    ("thanhha", Role.MEMBER, "Solution Consulting"),
]


CUSTOMERS: list[dict[str, Any]] = [
    {
        "name": "The Coffee House",
        "short": "TCH",
        "website": "thecoffeehouse.com",
        "industry": "F&B",
        "status": CustomerStatus.RESEARCHED,
        "owner": "khanhpp",
        "sources": 14,
        "facts": [
            (
                "Quy mô chuỗi",
                "Chuỗi vận hành hơn 150 cửa hàng tại các thành phố lớn ở Việt Nam, "
                "tập trung TP.HCM và Hà Nội.",
                92,
                False,
            ),
            (
                "Ứng dụng đặt hàng",
                "Có app riêng cho đặt hàng và tích điểm, đồng thời bán qua các sàn "
                "giao đồ ăn trong nước.",
                88,
                False,
            ),
            (
                "Chương trình khách hàng thân thiết",
                "Chương trình tích điểm hiện chạy trong app; tỷ lệ khách quay lại là "
                "chỉ số được nhắc tới nhiều trong truyền thông.",
                80,
                True,
            ),
            (
                "Thông báo giao dịch",
                "Xác nhận đơn và khuyến mãi đang gửi qua push app và SMS — chi phí SMS "
                "là điểm có thể tối ưu bằng ZNS.",
                85,
                True,
            ),
        ],
    },
    {
        "name": "TPBank",
        "short": "TPB",
        "website": "tpb.vn",
        "industry": "Ngân hàng",
        "status": CustomerStatus.IN_TALKS,
        "owner": "anhtuan",
        "sources": 12,
        "facts": [
            (
                "Định vị số",
                "Định vị ngân hàng số tại Việt Nam, đẩy mạnh eKYC và mở tài khoản trực tuyến.",
                90,
                False,
            ),
            (
                "Kênh tự phục vụ",
                "Hệ thống điểm giao dịch tự động hoạt động ngoài giờ hành chính.",
                86,
                False,
            ),
            (
                "Thông báo biến động số dư",
                "Nhu cầu gửi thông báo giao dịch số lượng lớn, yêu cầu tin cậy và "
                "đúng quy định của Ngân hàng Nhà nước.",
                88,
                True,
            ),
        ],
    },
    {
        "name": "Bách Hoá Xanh",
        "short": "BHX",
        "website": "bachhoaxanh.com",
        "industry": "Bán lẻ",
        "status": CustomerStatus.BUILDING_PROPOSAL,
        "owner": "minhthu",
        "sources": 11,
        "facts": [
            (
                "Mạng lưới cửa hàng",
                "Chuỗi bán lẻ thực phẩm với hàng nghìn điểm bán tại miền Nam và miền Trung.",
                91,
                False,
            ),
            (
                "Thương mại điện tử",
                "Website và app cho phép đặt hàng online, giao trong ngày ở khu vực nội thành.",
                84,
                True,
            ),
        ],
    },
    {
        "name": "Napas",
        "short": "NAPAS",
        "website": "napas.com.vn",
        "industry": "Trung gian thanh toán",
        "status": CustomerStatus.NEW,
        "owner": "khanhpp",
        "sources": 0,
        "facts": [],
    },
    {
        "name": "Long Châu",
        "short": "LC",
        "website": "nhathuoclongchau.com.vn",
        "industry": "Dược phẩm",
        "status": CustomerStatus.RESEARCHED,
        "owner": "quochuy",
        "sources": 9,
        "facts": [
            (
                "Chuỗi nhà thuốc",
                "Chuỗi nhà thuốc bán lẻ mở rộng nhanh trên toàn quốc.",
                89,
                False,
            ),
            (
                "Tư vấn dược sĩ",
                "Có kênh tư vấn dược sĩ trực tuyến — phù hợp để đưa lên Zalo OA.",
                82,
                True,
            ),
        ],
    },
    {
        "name": "Vietjet Air",
        "short": "VJ",
        "website": "vietjetair.com",
        "industry": "Hàng không",
        "status": CustomerStatus.PROPOSAL_SENT,
        "owner": "anhtuan",
        "sources": 13,
        "facts": [
            (
                "Kênh bán trực tuyến",
                "Phần lớn vé bán qua web và app; nhu cầu nhắc lịch bay và thay đổi chuyến rất cao.",
                90,
                True,
            ),
        ],
    },
    {
        "name": "FPT Shop",
        "short": "FRT",
        "website": "fptshop.com.vn",
        "industry": "Bán lẻ",
        "status": CustomerStatus.RESEARCHED,
        "owner": "minhthu",
        "sources": 10,
        "facts": [
            (
                "Bảo hành và CSKH",
                "Nhu cầu thông báo trạng thái bảo hành, đơn hàng cho khách tại Việt Nam.",
                83,
                True,
            ),
        ],
    },
    {
        "name": "Highlands Coffee",
        "short": "HLC",
        "website": "highlandscoffee.com.vn",
        "industry": "F&B",
        "status": CustomerStatus.RESEARCHING,
        "owner": "thanhha",
        "sources": 0,
        "facts": [],
    },
]

PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "ZNS – Zalo Notification Service",
        "family": "Zalo Business Service",
        "group": "Messaging",
        "description": (
            "Gửi thông báo giao dịch tới người dùng Zalo bằng mẫu tin đã được duyệt: "
            "xác nhận đơn, biến động số dư, nhắc lịch."
        ),
        "packages": ["Basic", "Pro", "Enterprise"],
        "capabilities": [
            "Thông báo giao dịch theo mẫu",
            "Báo cáo tỷ lệ gửi và đọc",
            "Chi phí thấp hơn SMS",
            "Xác thực OTP",
        ],
        "industries": ["Ngân hàng", "Bán lẻ", "F&B", "Hàng không", "Dược phẩm"],
        "positioning": "Thay thế SMS cho thông báo giao dịch, chi phí thấp hơn và có báo cáo.",
    },
    {
        "name": "Zalo OA – Official Account",
        "family": "Zalo Business Service",
        "group": "Engagement",
        "description": (
            "Tài khoản chính thức của doanh nghiệp trên Zalo: broadcast, chat CSKH, "
            "quản lý người theo dõi."
        ),
        "packages": ["Free", "Premium"],
        "capabilities": [
            "Broadcast tới người theo dõi",
            "Chat CSKH nhiều nhân viên",
            "Phân nhóm người theo dõi",
            "Menu tương tác",
        ],
        "industries": ["F&B", "Bán lẻ", "Dược phẩm", "Giáo dục"],
        "positioning": (
            "Kênh CSKH và truyền thông trực tiếp trên nền tảng người Việt dùng hằng ngày."
        ),
    },
    {
        "name": "Zalo Mini App",
        "family": "Zalo Business Service",
        "group": "Platform",
        "description": (
            "Ứng dụng chạy trong Zalo, không cần cài đặt: đặt hàng, tích điểm, tra cứu, thanh toán."
        ),
        "packages": ["Standard", "Enterprise"],
        "capabilities": [
            "Không cần cài app riêng",
            "Đăng nhập bằng Zalo ID",
            "Tích hợp thanh toán",
            "Chương trình loyalty",
        ],
        "industries": ["F&B", "Bán lẻ", "Dược phẩm", "Hàng không"],
        "positioning": "Giảm rào cản tải app, tận dụng sẵn tệp người dùng Zalo.",
    },
    {
        "name": "Zalo Ads",
        "family": "Zalo Business Service",
        "group": "Growth",
        "description": (
            "Quảng cáo trên Zalo theo mục tiêu: tăng người theo dõi OA, cài Mini App, tin nhắn."
        ),
        "packages": ["Self-serve", "Managed"],
        "capabilities": ["Nhắm theo khu vực", "Nhắm theo nhân khẩu", "Tối ưu theo chuyển đổi"],
        "industries": ["Bán lẻ", "F&B", "Giáo dục", "Bất động sản"],
        "positioning": "Tiếp cận người dùng Việt Nam ngay trong ứng dụng nhắn tin phổ biến nhất.",
    },
    {
        "name": "ZaloPay Gateway",
        "family": "Zalo Business Service",
        "group": "Payment",
        "description": "Cổng thanh toán tích hợp trong Zalo và Mini App: ví, thẻ, chuyển khoản.",
        "packages": ["Standard", "Enterprise"],
        "capabilities": ["Thanh toán trong Mini App", "Hoàn tiền", "Đối soát giao dịch"],
        "industries": ["Bán lẻ", "F&B", "Trung gian thanh toán", "Hàng không"],
        "positioning": "Thanh toán liền mạch ngay trong luồng Zalo, không rời ứng dụng.",
    },
    {
        "name": "Zalo Cloud Account",
        "family": "Zalo Cloud",
        "group": "Platform",
        "description": "Tài khoản quản trị tập trung cho các dịch vụ Zalo Business: hạn mức, "
        "khoá API, báo cáo.",
        "packages": ["Standard"],
        "capabilities": ["Quản lý khoá API", "Theo dõi hạn mức", "Báo cáo sử dụng"],
        "industries": ["Ngân hàng", "Bán lẻ", "Trung gian thanh toán"],
        "positioning": "Lớp quản trị dùng chung cho toàn bộ dịch vụ Zalo Business.",
    },
]

SKILLS: list[dict[str, Any]] = [
    {
        "name": "Deep Research doanh nghiệp Việt Nam",
        "stage": SkillStage.RESEARCH,
        "description": "Thu thập và rút dữ kiện về doanh nghiệp trong phạm vi thị trường Việt Nam.",
        "prompt": (
            "Bạn là chuyên viên phân tích doanh nghiệp, phụ trách thị trường Việt Nam.\n"
            "Chỉ lấy dữ kiện về hoạt động của doanh nghiệp TẠI VIỆT NAM. Bỏ qua công ty "
            "trùng tên ở nước khác và hoạt động của công ty mẹ ở thị trường ngoài Việt Nam.\n"
            "Ưu tiên nguồn .vn và báo chí kinh tế trong nước.\n"
            "Mỗi dữ kiện phải truy được về một nguồn cụ thể. Không suy diễn, không bịa số liệu.\n"
            "Trả lời bằng tiếng Việt, mỗi dữ kiện 1-3 câu."
        ),
    },
    {
        "name": "Dựng hồ sơ khách hàng",
        "stage": SkillStage.PROFILE,
        "description": "Chuẩn hoá dữ kiện rời thành hồ sơ firmographic dùng được cho bán hàng.",
        "prompt": (
            "Từ các dữ kiện đã xác minh, dựng hồ sơ doanh nghiệp gồm: ngành, quy mô, "
            "trụ sở tại Việt Nam, kênh bán, kênh CSKH, hiện trạng công nghệ, vị thế thị trường.\n"
            "Chỉ điền trường nào có dữ kiện chống lưng. Trường không rõ thì để trống, "
            "không phỏng đoán."
        ),
    },
    {
        "name": "Khớp sản phẩm Zalo",
        "stage": SkillStage.MATCHING,
        "description": (
            "Đối chiếu nhu cầu khách hàng với danh mục sản phẩm Zalo và cho điểm phù hợp."
        ),
        "prompt": (
            "Bạn là Solution Consultant của Zalo tại Việt Nam.\n"
            "Với mỗi nhu cầu tìm được, đề xuất sản phẩm Zalo phù hợp nhất và nêu rõ dựa "
            "trên dữ kiện nào.\n"
            "Cho điểm fit 0-100 theo mức phù hợp thực tế, không theo mong muốn bán hàng.\n"
            "Cân nhắc bối cảnh Việt Nam: hành vi người dùng Zalo, quy định trong nước, "
            "kênh phân phối nội địa."
        ),
    },
    {
        "name": "Viết proposal B2B",
        "stage": SkillStage.PROPOSAL,
        "description": "Viết nội dung slide đề xuất giải pháp, văn phong doanh nghiệp Việt Nam.",
        "prompt": (
            "Viết nội dung slide proposal bằng tiếng Việt, văn phong doanh nghiệp, "
            "hướng kết quả.\n"
            "Bullet tối đa 18 từ, tối đa 5 bullet mỗi khối.\n"
            "Chỉ dùng số liệu có trong dữ kiện; không có thì viết định tính.\n"
            "Mỗi slide phải có một thông điệp chính rõ ràng."
        ),
    },
    {
        "name": "Kiểm duyệt nội dung proposal",
        "stage": SkillStage.REVIEW,
        "description": "Soát lỗi số liệu, cam kết quá mức và nội dung không có nguồn.",
        "prompt": (
            "Đọc bản proposal và chỉ ra:\n"
            "1. Số liệu không có nguồn chống lưng.\n"
            "2. Cam kết quá mức về kết quả hoặc SLA.\n"
            "3. Nội dung sai về sản phẩm Zalo.\n"
            "4. Câu tối nghĩa hoặc sáo rỗng.\n"
            "Với mỗi vấn đề, nêu slide và đề xuất cách sửa."
        ),
    },
]

TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "Retail Executive Deck",
        "subtitle": "Bộ slide cho lãnh đạo khối bán lẻ — ngắn, tập trung con số",
        "tone": "executive",
        "required": ["Bối cảnh", "Vấn đề", "Giải pháp", "Lộ trình", "Chi phí"],
        "slides": [
            ("Trang bìa", "cover", "Tên khách hàng, ngày, người trình bày"),
            ("Bối cảnh khách hàng", "content", "Quy mô, kênh bán, tệp khách tại Việt Nam"),
            ("Vấn đề đang gặp", "content", "3 vấn đề chính, có dữ kiện chống lưng"),
            ("Giải pháp đề xuất", "content", "Sản phẩm Zalo tương ứng từng vấn đề"),
            ("Cách hoạt động", "content", "Luồng nghiệp vụ end-to-end"),
            ("Kết quả kỳ vọng", "chart", "Chỉ số cải thiện, nêu rõ là kỳ vọng"),
            ("Lộ trình triển khai", "table", "Giai đoạn, thời gian, việc cần làm"),
            ("Chi phí", "table", "Gói, đơn giá, ước tính theo lưu lượng"),
            ("Bước tiếp theo", "closing", "Việc cần chốt và ai chịu trách nhiệm"),
        ],
    },
    {
        "name": "Banking Solution Deck",
        "subtitle": "Dành cho khối ngân hàng — nhấn tuân thủ và bảo mật",
        "tone": "formal",
        "required": ["Bối cảnh", "Tuân thủ", "Giải pháp", "Bảo mật", "Chi phí"],
        "slides": [
            ("Trang bìa", "cover", None),
            ("Bối cảnh ngân hàng số", "content", "Hiện trạng số hoá tại Việt Nam"),
            ("Yêu cầu tuân thủ", "content", "Quy định liên quan của Ngân hàng Nhà nước"),
            ("Giải pháp ZNS cho thông báo giao dịch", "content", None),
            ("Kiến trúc tích hợp", "content", "Luồng API, hạn mức, dự phòng"),
            ("Bảo mật và dữ liệu", "content", "Mã hoá, lưu trữ, phân quyền"),
            ("SLA và vận hành", "table", None),
            ("Chi phí", "table", None),
            ("Bước tiếp theo", "closing", None),
        ],
    },
    {
        "name": "F&B Loyalty Mini App",
        "subtitle": "Đề xuất Mini App tích điểm cho chuỗi F&B",
        "tone": "friendly",
        "required": ["Bối cảnh", "Giải pháp", "Trải nghiệm", "Chi phí"],
        "slides": [
            ("Trang bìa", "cover", None),
            ("Bối cảnh chuỗi F&B", "content", None),
            ("Hành trình khách hàng hiện tại", "content", None),
            ("Mini App tích điểm", "content", None),
            ("Trải nghiệm người dùng", "content", "Các bước từ quét tới tích điểm"),
            ("Kết hợp ZNS và OA", "content", None),
            ("Chi phí và lộ trình", "table", None),
            ("Bước tiếp theo", "closing", None),
        ],
    },
]

KNOWLEDGE_BASES: list[dict[str, Any]] = [
    {
        "name": "Zalo Product Playbook",
        "description": "Tài liệu sản phẩm, bảng giá và FAQ chính thức của Zalo Business Service.",
        "scope": Scope.SHARED,
        "tags": ["Sản phẩm", "FAQ"],
        "docs": 86,
        "sources": ["zalo.solutions", "business.zalo.me"],
        "restricted": False,
    },
    {
        "name": "Case study khách hàng Việt Nam",
        "description": "Các case đã triển khai tại thị trường Việt Nam, phân theo ngành.",
        "scope": Scope.SHARED,
        "tags": ["Case study", "Bán hàng"],
        "docs": 34,
        "sources": [],
        "restricted": False,
    },
    {
        "name": "Bảng giá nội bộ 2026",
        "description": "Bảng giá và biên độ chiết khấu — chỉ dành cho đội BD được cấp quyền.",
        "scope": Scope.MINE,
        "tags": ["Giá", "Nội bộ"],
        "docs": 12,
        "sources": [],
        "restricted": True,
    },
    {
        "name": "Quy định ngành ngân hàng",
        "description": "Tổng hợp quy định liên quan tới thông báo giao dịch và dữ liệu khách hàng.",
        "scope": Scope.SHARED,
        "tags": ["Tuân thủ", "Ngân hàng"],
        "docs": 21,
        "sources": ["sbv.gov.vn"],
        "restricted": False,
    },
]


async def seed(reset: bool = False) -> None:
    setup_logging()

    async with session_scope() as session:
        if reset:
            log.warning("seed_reset — deleting existing demo rows")
            for model in (
                ProposalSlide,
                Proposal,
                CustomerFact,
                CustomerActivity,
                Customer,
                TemplateSlide,
                ProposalTemplate,
                SkillVersion,
                Skill,
                KnowledgeSource,
                KnowledgeBase,
                Product,
                UserNotificationSettings,
                User,
                Team,
            ):
                await session.execute(delete(model))
            await session.flush()

        teams: dict[str, Team] = {}
        for name, description in TEAMS:
            existing = (
                await session.execute(select(Team).where(Team.name == name))
            ).scalar_one_or_none()
            if existing is None:
                existing = Team(
                    id=Team.make_id(),
                    name=name,
                    name_norm=normalize(name),
                    description=description,
                )
                session.add(existing)
                await session.flush()
            teams[name] = existing

        users: dict[str, User] = {}
        for handle, role, team_name in USERS:
            display = handle
            email = f"{handle}@{DOMAIN}"
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing is None:
                existing = User(
                    id=User.make_id(),
                    email=email,
                    handle=handle,
                    name=display,
                    name_norm=normalize(display),
                    initials=initials(handle),
                    avatar_color=pick_avatar_color(email),
                    role=role,
                    status=UserStatus.ACTIVE,
                    team_id=teams[team_name].id,
                    title="Business Development" if role == Role.MEMBER else "BD Lead",
                    is_first_login=False,
                    tour_completed=True,
                    onboarding_steps=["profile", "team", "interests"],
                )
                session.add(existing)
                await session.flush()
                session.add(UserNotificationSettings(user_id=existing.id))
            users[handle] = existing

        from app.users import ensure_bootstrap_admins

        promoted = await ensure_bootstrap_admins(session)
        if promoted:
            log.info("seed_bootstrap_admins", changed=promoted)

        products: dict[str, Product] = {}
        for order, entry in enumerate(PRODUCTS):
            existing = (
                await session.execute(select(Product).where(Product.name == entry["name"]))
            ).scalar_one_or_none()
            if existing is None:
                existing = Product(
                    id=Product.make_id(),
                    name=entry["name"],
                    name_norm=normalize(entry["name"]),
                    family=entry["family"],
                    group_name=entry["group"],
                    description=entry["description"],
                    packages=entry["packages"],
                    capabilities=entry["capabilities"],
                    target_industries=entry["industries"],
                    positioning=entry["positioning"],
                    status="active",
                    sort_order=order,
                    source_url="https://zalo.solutions",
                )
                session.add(existing)
                await session.flush()
            products[entry["name"]] = existing

        customers: dict[str, Customer] = {}
        for entry in CUSTOMERS:
            existing = (
                await session.execute(select(Customer).where(Customer.name == entry["name"]))
            ).scalar_one_or_none()
            if existing is None:
                owner = users[entry["owner"]]
                website = clean_website(entry["website"])
                facts = entry["facts"]
                opportunities = sum(1 for f in facts if f[3])

                existing = Customer(
                    id=Customer.make_id(),
                    name=entry["name"],
                    name_norm=normalize(entry["name"]),
                    short_name=entry["short"],
                    website=website,
                    website_norm=normalize(website),
                    industry=entry["industry"],
                    status=entry["status"],
                    owner_id=owner.id,
                    created_by_id=owner.id,
                    source_count=entry["sources"],
                    fact_count=len(facts),
                    opportunity_count=opportunities,
                    researched_at=now() - timedelta(days=2) if facts else None,
                    profile=(
                        {
                            "market": "Việt Nam",
                            "industry": entry["industry"],
                            "hq": "Việt Nam",
                        }
                        if facts
                        else {}
                    ),
                )
                session.add(existing)
                await session.flush()

                for topic, summary, confidence, is_opportunity in facts:
                    session.add(
                        CustomerFact(
                            id=CustomerFact.make_id(),
                            customer_id=existing.id,
                            topic=topic,
                            summary=summary,
                            confidence=confidence,
                            verdict=FactVerdict.OK if confidence >= 85 else FactVerdict.AI,
                            origin="research",
                            is_opportunity=is_opportunity,
                        )
                    )

                session.add(
                    CustomerActivity(
                        id=CustomerActivity.make_id(),
                        customer_id=existing.id,
                        actor_id=owner.id,
                        kind=ActivityKind.CREATED,
                        title="Tạo hồ sơ khách hàng",
                        body=f"{owner.name} tạo hồ sơ {entry['name']}.",
                    )
                )
                if facts:
                    session.add(
                        CustomerActivity(
                            id=CustomerActivity.make_id(),
                            customer_id=existing.id,
                            actor_id=owner.id,
                            kind=ActivityKind.RESEARCH_DONE,
                            title="Deep Research hoàn tất",
                            body=(
                                f"{entry['sources']} nguồn, {len(facts)} dữ kiện, "
                                f"{opportunities} cơ hội (thị trường Việt Nam)."
                            ),
                        )
                    )
            customers[entry["name"]] = existing

        for entry in SKILLS:
            existing = (
                await session.execute(select(Skill).where(Skill.name == entry["name"]))
            ).scalar_one_or_none()
            if existing is None:
                skill = Skill(
                    id=Skill.make_id(),
                    name=entry["name"],
                    name_norm=normalize(entry["name"]),
                    description=entry["description"],
                    stage=entry["stage"],
                    scope=Scope.SHARED,
                    version="v1.0",
                    prompt=entry["prompt"],
                    owner_id=users["khanhpp"].id,
                    is_builtin=True,
                )
                session.add(skill)
                await session.flush()
                session.add(
                    SkillVersion(
                        id=SkillVersion.make_id(),
                        skill_id=skill.id,
                        version="v1.0",
                        prompt=entry["prompt"],
                        note="Phiên bản khởi tạo",
                        author_id=users["khanhpp"].id,
                    )
                )

        templates: dict[str, ProposalTemplate] = {}
        for entry in TEMPLATES:
            existing = (
                await session.execute(
                    select(ProposalTemplate).where(ProposalTemplate.name == entry["name"])
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = ProposalTemplate(
                    id=ProposalTemplate.make_id(),
                    name=entry["name"],
                    name_norm=normalize(entry["name"]),
                    subtitle=entry["subtitle"],
                    scope=Scope.SHARED,
                    version="v1.0",
                    tone=entry["tone"],
                    required_slides=entry["required"],
                    slide_count=len(entry["slides"]),
                    owner_id=users["khanhpp"].id,
                    is_builtin=True,
                    theme={"accent": "#0068FF", "heading": "#021F4E"},
                )
                session.add(existing)
                await session.flush()
                for index, (title, layout, guidance) in enumerate(entry["slides"], start=1):
                    session.add(
                        TemplateSlide(
                            id=TemplateSlide.make_id(),
                            template_id=existing.id,
                            index=index,
                            title=title,
                            layout=layout,
                            guidance=guidance,
                        )
                    )
            templates[entry["name"]] = existing

        for entry in KNOWLEDGE_BASES:
            existing = (
                await session.execute(
                    select(KnowledgeBase).where(KnowledgeBase.name == entry["name"])
                )
            ).scalar_one_or_none()
            if existing is None:
                kb = KnowledgeBase(
                    id=KnowledgeBase.make_id(),
                    name=entry["name"],
                    name_norm=normalize(entry["name"]),
                    description=entry["description"],
                    scope=entry["scope"],
                    tags=entry["tags"],
                    doc_count=entry["docs"],
                    restricted=entry["restricted"],
                    owner_id=users["khanhpp"].id,
                )
                session.add(kb)
                await session.flush()
                for url in entry["sources"]:
                    session.add(
                        KnowledgeSource(
                            id=KnowledgeSource.make_id(), kb_id=kb.id, url=url, status="ok"
                        )
                    )

        tch = customers["The Coffee House"]
        existing_proposal = (
            await session.execute(select(Proposal).where(Proposal.customer_id == tch.id))
        ).scalar_one_or_none()
        if existing_proposal is None:
            title = "TCH – Loyalty trên Mini App"
            proposal = Proposal(
                id=Proposal.make_id(),
                title=title,
                title_norm=normalize(title),
                customer_id=tch.id,
                template_id=templates["F&B Loyalty Mini App"].id,
                owner_id=users["khanhpp"].id,
                status=ProposalStatus.DRAFT,
                version="v1.2",
                tone="friendly",
                slide_count=4,
                summary=(
                    "Đề xuất đưa chương trình tích điểm của The Coffee House lên Zalo "
                    "Mini App, kết hợp ZNS cho thông báo giao dịch."
                ),
            )
            session.add(proposal)
            await session.flush()

            demo_slides = [
                (
                    1,
                    "The Coffee House trên Zalo",
                    "cover",
                    [{"type": "text", "text": "Đề xuất giải pháp loyalty và thông báo giao dịch"}],
                ),
                (
                    2,
                    "Bối cảnh khách hàng",
                    "content",
                    [
                        {
                            "type": "bullets",
                            "items": [
                                "Hơn 150 cửa hàng tại các thành phố lớn",
                                "App riêng cho đặt hàng và tích điểm",
                                "Thông báo đơn hàng đang dùng push và SMS",
                            ],
                        }
                    ],
                ),
                (
                    3,
                    "Giải pháp đề xuất",
                    "content",
                    [
                        {
                            "type": "bullets",
                            "items": [
                                "Mini App tích điểm — không cần tải app riêng",
                                "ZNS cho xác nhận đơn và nhắc ưu đãi",
                                "Zalo OA làm kênh CSKH chính",
                            ],
                        }
                    ],
                ),
                (
                    4,
                    "Bước tiếp theo",
                    "closing",
                    [
                        {
                            "type": "bullets",
                            "items": [
                                "Chốt phạm vi giai đoạn 1",
                                "Thống nhất mẫu tin ZNS",
                                "Lên lịch demo Mini App",
                            ],
                        }
                    ],
                ),
            ]
            for index, slide_title, layout, blocks in demo_slides:
                session.add(
                    ProposalSlide(
                        id=ProposalSlide.make_id(),
                        proposal_id=proposal.id,
                        index=index,
                        title=slide_title,
                        layout=layout,
                        blocks=blocks,
                    )
                )

        if await session.get(AiConfig, 1) is None:
            session.add(AiConfig(id=1, updated_by_id=users["khanhpp"].id))
        if await session.get(SystemPolicy, 1) is None:
            session.add(SystemPolicy(id=1, updated_by_id=users["khanhpp"].id))

    log.info(
        "seed_done",
        teams=len(TEAMS),
        users=len(USERS),
        customers=len(CUSTOMERS),
        products=len(PRODUCTS),
        skills=len(SKILLS),
        templates=len(TEMPLATES),
        knowledge_bases=len(KNOWLEDGE_BASES),
    )
    async with session_scope() as session:
        rows = (await session.execute(select(User).order_by(User.role, User.email))).scalars().all()

    print("\nUser trong DB (OTP in ra log khi EMAIL_DRY_RUN=true):")
    for u in rows:
        mark = "  ← ADMIN" if u.role == Role.ADMIN else ""
        print(f"  {u.email:<24} {u.name:<22} {u.role.value}{mark}")
    print(
        "\nCấp/hạ quyền admin (ghi trực tiếp vào DB):"
        "\n  uv run python -m app.users grant-admin <email> [...]"
        "\n  uv run python -m app.users revoke-admin <email>"
        "\n  uv run python -m app.users list"
    )


if __name__ == "__main__":
    asyncio.run(seed(reset="--reset" in sys.argv))
