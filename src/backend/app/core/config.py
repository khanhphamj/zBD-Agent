from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


def _csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    app_name: str = "zBD Agent"
    api_prefix: str = "/api/v1"
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "change-me-openssl-rand-hex-32"

    database_url: str = "postgresql+asyncpg://zbd:zbd_dev_password@localhost:5432/zbd"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "zbd_minio"
    minio_secret_key: str = "zbd_minio_dev_password"
    minio_secure: bool = False
    bucket_knowledge: str = "zbd-knowledge"
    bucket_exports: str = "zbd-exports"
    bucket_templates: str = "zbd-templates"
    bucket_avatars: str = "zbd-avatars"
    bucket_skills: str = "zbd-skills"

    presigned_url_ttl_seconds: int = 3600

    allowed_email_domains: CsvList = Field(default_factory=lambda: ["vng.com.vn"])
    bootstrap_admin_emails: CsvList = Field(default_factory=list)
    otp_bypass_emails: CsvList = Field(default_factory=list)
    # Email điền sẵn ở màn đăng nhập, và các email luôn chạy lại luồng lần đầu
    # (onboarding + hướng dẫn) mỗi lần đăng nhập — để xem/demo được luồng đó.
    # Admin có thấy mọi resource của người khác không. Mặc định KHÔNG: khách hàng,
    # skill, KB, template chỉ hiện với người sở hữu, người được share, hoặc khi đã
    # publish thành Shared. Các mục Quản trị (user, audit, policy) không bị ảnh hưởng.
    admin_sees_all_resources: bool = False
    login_default_email: str = "zaloadmin@vng.com.vn"
    always_onboarding_emails: CsvList = Field(default_factory=lambda: ["zaloadmin@vng.com.vn"])
    allow_otp_bypass_in_production: bool = False
    otp_dev_code: str = "123456"
    allow_otp_dev_code_in_production: bool = False
    otp_ttl_seconds: int = 300
    otp_resend_after_seconds: int = 30
    otp_max_attempts: int = 5
    otp_lock_minutes: int = 15
    otp_length: int = 6
    access_token_ttl_minutes: int = 360
    refresh_token_ttl_days: int = 30
    jwt_algorithm: str = "HS256"

    resend_api_key: str = ""
    resend_base_url: str = "https://api.resend.com"
    resend_from_email: str = "zBD Agent <no-reply@example.com>"
    resend_reply_to: str = ""
    email_dry_run: bool = True
    email_sandbox_to: str = ""

    ai_platform_api_key: str = ""
    # MaaS = GreenNode Self-host (hạ tầng AI của VNG) — không phải API công khai của OpenAI/Google.
    # Vì là cụm self-host: tên model phải khớp danh mục cụm đang chạy, và pool có
    # thể hết worker (503 no_available_workers) -> maas_fallback_models +
    # maas_retry_delays bên dưới là để đỡ đúng trường hợp đó.
    maas_base_url: str = "https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1"
    maas_reasoning_model: str = "z-ai/glm-5.2"
    maas_vision_model: str = "google/gemma-4-31b-it"
    maas_max_tokens: int = 4096
    maas_temperature: float = 1.0
    maas_top_p: float = 0.95
    maas_timeout_seconds: int = 180
    # Pool của một model có thể sập (503 no_available_workers) trong khi model khác
    # vẫn chạy — đo được ngày 29/07/2026: glm-5.2 chết, qwen3.7-plus và gemini-3-flash
    # trả JSON bình thường. Thứ tự dưới đây là thứ tự thử khi model chính lỗi tạm thời.
    maas_fallback_models: CsvList = Field(
        default_factory=lambda: ["qwen/qwen3.7-plus", "gemini/gemini-3-flash-preview"]
    )
    maas_retry_delays: CsvList = Field(default_factory=lambda: ["2", "8"])

    search_providers: CsvList = Field(default_factory=lambda: ["langsearch", "tavily"])
    langsearch_api_key: str = ""
    langsearch_base_url: str = "https://api.langsearch.com/v1"
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    search_timeout_seconds: int = 60
    research_max_sources: int = 80

    research_market: str = "VN"
    research_locale: str = "vi"
    research_market_label: str = "Việt Nam"

    # ── Nhóm ngành đang tập trung ────────────────────────────────────────────
    # ƯU TIÊN MỀM, không phải bộ lọc: khách ngoài ngành vẫn chạy research và
    # matching bình thường. Focus chỉ tác động 3 chỗ: bối cảnh thêm vào prompt,
    # điểm cộng khi xếp hạng nguồn, và 3/6 slot của truy vấn site:.
    # Đặt focus_industry="" là tắt hẳn, mọi ngành về lại trung tính.
    focus_industry: str = "BFSI"
    focus_industry_label: str = (
        "BFSI — ngân hàng, tài chính tiêu dùng, bảo hiểm, chứng khoán, "
        "trung gian thanh toán / ví điện tử, fintech"
    )
    focus_industry_brief: str = """\
- Tuân thủ là điều kiện tiên quyết, không phải điểm cộng: xác thực eKYC / sinh
  trắc học, hạn mức giao dịch, lưu trữ và bảo mật dữ liệu khách hàng theo quy
  định của Ngân hàng Nhà nước. Nếu một đề xuất phụ thuộc điều kiện tuân thủ thì
  phải nói rõ, đừng để người đọc tự suy ra.
- Use case đặc thù đáng tìm và đáng đề xuất: thông báo biến động số dư và xác
  nhận giao dịch, OTP / xác thực, nhắc kỳ hạn và nhắc nợ, mở tài khoản và
  onboarding trực tuyến, tra cứu hạn mức / khoản vay, chăm sóc và thu hồi nợ sớm.
- Số liệu nên bắt khi có: quy mô tệp khách hàng, số phòng giao dịch / điểm bán,
  sản lượng tin nhắn hoặc giao dịch mỗi tháng, tỷ lệ số hoá theo kênh.
- Các phân khúc trong ngành khác nhau rõ rệt — ngân hàng, tài chính tiêu dùng,
  bảo hiểm, chứng khoán, trung gian thanh toán. Không gộp chung, vì nhu cầu và
  rào cản triển khai của từng phân khúc khác nhau."""
    focus_preferred_domains: CsvList = Field(
        default_factory=lambda: [
            "sbv.gov.vn",
            "vnba.org.vn",
            "thitruongtaichinhtiente.vn",
            "tinnhanhchungkhoan.vn",
            "iav.vn",
            "fiingroup.vn",
            "napas.com.vn",
        ]
    )
    # Số slot mà domain focus được chiếm trong truy vấn site: (tổng 6 slot).
    focus_press_slots: int = 3

    research_preferred_domains: CsvList = Field(
        default_factory=lambda: [
            "cafef.vn",
            "cafebiz.vn",
            "vnexpress.net",
            "vietnamnet.vn",
            "tuoitre.vn",
            "thanhnien.vn",
            "dantri.com.vn",
            "vietstock.vn",
            "brandsvietnam.com",
            "theleader.vn",
            "nhipcaudautu.vn",
            "vneconomy.vn",
            "baodautu.vn",
            "masothue.com",
            "vietnamworks.com",
            "itviec.com",
            "topdev.vn",
            # hồ sơ pháp nhân & nguồn chính thống
            "dangkykinhdoanh.gov.vn",
            "hosocongty.vn",
            "thongtindoanhnghiep.co",
            "infodoanhnghiep.com",
            "gov.vn",
            "cbonline.ssc.gov.vn",
            "hsx.vn",
            "hnx.vn",
            # kênh của chính doanh nghiệp
            "linkedin.com",
            "topcv.vn",
        ]
    )

    research_excluded_domains: CsvList = Field(
        default_factory=lambda: [
            "crunchbase.com",
            "pitchbook.com",
            "cbinsights.com",
            "tracxn.com",
            "crustdata.com",
            "growjo.com",
            "owler.com",
            "theorg.com",
            "zoominfo.com",
            "rocketreach.co",
            "apollo.io",
            "lusha.com",
            "leadiq.com",
            "dnb.com",
            "glassdoor.com",
            "indeed.com",
            "zippia.com",
            # content farm gặp thật trong log Deep Research: bài SEO về giấy phép /
            # dịch vụ SMS, không nói gì về doanh nghiệp đang nghiên cứu
            "congtyluatacc.vn",
            "giaminhcantho.com",
            "phanmemcamdo.com",
            "zalominiapps.com",
            "smsboosting.com",
            "cremyx.app",
            "cartboss.io",
            "sakari.io",
        ]
    )

    max_upload_mb: int = 50
    allowed_upload_extensions: CsvList = Field(
        default_factory=lambda: ["pdf", "docx", "xlsx", "pptx"]
    )

    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"]
    )

    default_page_size: int = 5
    max_page_size: int = 100

    @field_validator(
        "allowed_email_domains",
        "bootstrap_admin_emails",
        "otp_bypass_emails",
        "always_onboarding_emails",
        "maas_fallback_models",
        "maas_retry_delays",
        "search_providers",
        "allowed_upload_extensions",
        "cors_origins",
        "research_preferred_domains",
        "research_excluded_domains",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: str | list[str]) -> list[str]:
        return _csv(v)

    @field_validator("allowed_email_domains", "allowed_upload_extensions", mode="after")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [item.lower().lstrip("@.") for item in v]

    @field_validator("bootstrap_admin_emails", "otp_bypass_emails", mode="after")
    @classmethod
    def _lower_emails(cls, v: list[str]) -> list[str]:
        return [item.lower() for item in v]

    @property
    def press_domains(self) -> list[str]:
        """Domain cho truy vấn `site:` khi tìm báo chí. Domain của nhóm ngành đang
        focus được xếp trước, phần còn lại lấy từ danh sách chung — tổng vẫn 6 để
        query không quá dài. Không focus ngành nào thì trả về đúng 6 domain chung."""
        focus = (
            self.focus_preferred_domains[: self.focus_press_slots]
            if self.focus_industry
            else []
        )
        rest = [d for d in self.research_preferred_domains if d not in focus]
        return focus + rest[: max(0, 6 - len(focus))]

    @property
    def focus_prompt_block(self) -> str:
        """Khối bối cảnh nhóm ngành chèn vào prompt. Rỗng khi không focus ngành nào,
        nên prompt trở lại đúng như trước."""
        if not self.focus_industry:
            return ""
        return (
            f"TRỌNG TÂM NGÀNH — {self.focus_industry_label}\n\n"
            "Đây là ưu tiên của đội, KHÔNG phải bộ lọc. Nếu doanh nghiệp đang xử lý "
            "không thuộc nhóm ngành này thì bỏ qua toàn bộ phần dưới và làm việc "
            "bình thường. Nếu có thuộc, chú ý thêm:\n"
            f"{self.focus_industry_brief}\n"
        )

    @property
    def otp_bypass_active(self) -> bool:
        if not self.otp_bypass_emails:
            return False
        if self.is_production and not self.allow_otp_bypass_in_production:
            return False
        return True

    def is_otp_bypass(self, email: str) -> bool:
        return self.otp_bypass_active and email.strip().lower() in self.otp_bypass_emails

    @property
    def otp_dev_code_active(self) -> bool:
        if not self.otp_dev_code:
            return False
        if self.is_production and not self.allow_otp_dev_code_in_production:
            return False
        return True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def maas_configured(self) -> bool:
        return bool(self.ai_platform_api_key)

    @property
    def search_configured(self) -> bool:
        return bool(self.langsearch_api_key or self.tavily_api_key)

    @property
    def resend_configured(self) -> bool:
        return bool(self.resend_api_key)

    @property
    def email_sandbox_active(self) -> bool:
        return bool(self.email_sandbox_to) and not self.email_dry_run

    @property
    def email_live(self) -> bool:
        return self.resend_configured and not self.email_dry_run


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
