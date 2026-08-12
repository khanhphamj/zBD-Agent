from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class UserStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    LOCKED = "locked"


class CustomerStatus(StrEnum):
    NEW = "Mới tạo"
    RESEARCHING = "Đang Deep Research"
    RESEARCHED = "Hoàn tất nghiên cứu"
    BUILDING_PROPOSAL = "Đang xây dựng proposal"
    PROPOSAL_SENT = "Đã gửi proposal"
    IN_TALKS = "Đang trao đổi"


class ProposalStatus(StrEnum):
    DRAFT = "Draft"
    DONE = "Hoàn tất"
    PENDING_APPROVAL = "Chờ duyệt"
    APPROVED = "Đã duyệt"
    REJECTED = "Từ chối"


class FactVerdict(StrEnum):
    AI = "ai"
    OK = "ok"
    REJECTED = "rejected"


class SourceStatus(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    PENDING = "pending"


class Scope(StrEnum):
    MINE = "Mine"
    SHARED = "Shared"


class Permission(StrEnum):
    VIEW = "view"
    COMMENT = "comment"
    EDIT = "edit"
    NONE = "none"


class ResourceType(StrEnum):
    CUSTOMER = "customer"
    PROPOSAL = "proposal"
    KNOWLEDGE_BASE = "knowledge_base"
    SKILL = "skill"
    TEMPLATE = "template"


class LinkMode(StrEnum):
    RESTRICTED = "restricted"
    COMPANY = "company"


class JobKind(StrEnum):
    RESEARCH = "research"
    GENERATE = "generate"
    EXPORT = "export"
    KB_SYNC = "kb_sync"
    PRODUCT_MATCHING = "product_matching"
    PRODUCT_CRAWL = "product_crawl"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobEventType(StrEnum):
    STEP = "step"
    SOURCE = "source"
    FACT = "fact"
    SLIDE = "slide"
    DONE = "done"
    ERROR = "error"


class ExportFormat(StrEnum):
    PDF = "pdf"


class ExportStage(StrEnum):
    PREPARE = "prepare"
    RENDER = "render"
    EMBED = "embed"
    PACKAGE = "package"


class ExportStatus(StrEnum):
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class FileStatus(StrEnum):
    UPLOADED = "uploaded"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class NotificationType(StrEnum):
    RESEARCH_DONE = "research_done"
    PROPOSAL_DONE = "proposal_done"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESULT = "approval_result"
    COMMENT = "comment"
    SHARE = "share"
    HANDOVER = "handover"
    ACCESS_REQUEST = "access_request"
    INVITE = "invite"


class AccessRequestType(StrEnum):
    ACCOUNT = "account"
    RESOURCE_ACCESS = "resource_access"
    ROLE_UPGRADE = "role_upgrade"
    PUBLISH_SHARED = "publish_shared"
    COMPANY_LINK = "company_link"


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditCategory(StrEnum):
    CUSTOMER_DATA = "Dữ liệu KH"
    PROPOSAL = "Proposal"
    PERMISSIONS = "Phân quyền"
    KNOWLEDGE = "Tri thức"
    SYSTEM = "Hệ thống"


class ActivityKind(StrEnum):
    CREATED = "created"
    RESEARCH_STARTED = "research_started"
    RESEARCH_DONE = "research_done"
    RESEARCH_FAILED = "research_failed"
    FACT_EDITED = "fact_edited"
    PROFILE_UPDATED = "profile_updated"
    SOLUTION_SELECTED = "solution_selected"
    PROPOSAL_CREATED = "proposal_created"
    PROPOSAL_EXPORTED = "proposal_exported"
    SHARED = "shared"
    HANDOVER = "handover"
    ARCHIVED = "archived"
    COMMENT = "comment"


class ResearchDepth(StrEnum):
    STANDARD = "standard"
    DEEP = "deep"


class SkillStage(StrEnum):
    RESEARCH = "Research"
    PROFILE = "Hồ sơ"
    # Đổi "Phương án" -> "Matching" (01/08/2026) cho thống nhất với nhãn stage
    # tiếng Anh trên UI (Research/Proposal). Data migrate cùng đợt.
    MATCHING = "Matching"
    PROPOSAL = "Proposal"
    REVIEW = "Kiểm duyệt"
