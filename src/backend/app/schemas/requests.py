from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models.enums import (
    ExportFormat,
    LinkMode,
    Permission,
    ResearchDepth,
    ResourceType,
    Role,
    Scope,
    SkillStage,
)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OtpRequest(Payload):
    email: EmailStr


class OtpVerify(Payload):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)

    @field_validator("code")
    @classmethod
    def digits_only(cls, v: str) -> str:
        cleaned = "".join(ch for ch in v if ch.isdigit())
        if not cleaned:
            raise ValueError("Mã xác thực chỉ gồm chữ số")
        return cleaned


class RefreshRequest(Payload):
    refreshToken: str


class AccessRequestCreate(Payload):
    email: EmailStr | None = None
    name: str | None = None
    resourceType: ResourceType | None = None
    resourceId: str | None = None
    permission: Permission = Permission.VIEW
    requestedRole: Role | None = None
    reason: str | None = Field(default=None, max_length=2000)


class DecisionBody(Payload):
    note: str | None = Field(default=None, max_length=2000)


class RejectBody(Payload):
    reason: str = Field(min_length=1, max_length=2000)


class OnboardingPatch(Payload):
    completedSteps: list[str] | None = None
    tourCompleted: bool | None = None
    interests: list[str] | None = None


class CustomerCreate(Payload):
    name: str = Field(min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    shortName: str | None = Field(default=None, max_length=32)
    knowledgeBaseIds: list[str] = Field(default_factory=list)

    startResearch: bool = True
    depth: ResearchDepth = ResearchDepth.STANDARD


class CustomerPatch(Payload):
    name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    shortName: str | None = Field(default=None, max_length=32)
    status: str | None = None
    notes: str | None = None
    profile: dict[str, Any] | None = None
    logoUrl: str | None = None
    ownerId: str | None = None


class BulkAction(Payload):
    ids: list[str] = Field(min_length=1)
    action: Literal["delete", "archive", "unarchive", "duplicate"]


class ResearchStart(Payload):
    knowledgeBaseIds: list[str] = Field(default_factory=list)
    depth: ResearchDepth = ResearchDepth.STANDARD


class FactPatch(Payload):
    verdict: Literal["ok", "rejected", "ai"]


class FactCreate(Payload):
    topic: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1)
    confidence: int = Field(default=100, ge=0, le=100)
    sourceUrl: str | None = None
    isOpportunity: bool = False


class HandoverBody(Payload):
    toUserId: str
    keepPermission: Literal["view", "comment", "none"] = "view"
    note: str | None = Field(default=None, max_length=2000)


class ProductUpsert(Payload):
    name: str = Field(min_length=1, max_length=255)
    family: str | None = None
    group: str | None = None
    description: str | None = None
    packages: list[str] = Field(default_factory=list)
    status: str = "active"
    iconUrl: str | None = None
    positioning: str | None = None
    useCases: list[dict[str, Any]] = Field(default_factory=list)
    pricing: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    targetIndustries: list[str] = Field(default_factory=list)
    sourceUrl: str | None = None


class ProductPatch(Payload):
    name: str | None = None
    family: str | None = None
    group: str | None = None
    description: str | None = None
    packages: list[str] | None = None
    status: str | None = None
    iconUrl: str | None = None
    positioning: str | None = None
    useCases: list[dict[str, Any]] | None = None
    pricing: list[dict[str, Any]] | None = None
    documents: list[dict[str, Any]] | None = None
    capabilities: list[str] | None = None
    targetIndustries: list[str] | None = None
    sourceUrl: str | None = None


class ProposalCreate(Payload):
    customerId: str
    templateId: str | None = None
    title: str | None = None
    solutionIds: list[str] = Field(default_factory=list)
    knowledgeBaseIds: list[str] = Field(default_factory=list)
    skillIds: list[str] = Field(default_factory=list)
    tone: str = "professional"
    slideCount: int = Field(default=12, ge=3, le=40)


class ProposalPatch(Payload):
    title: str | None = None
    status: str | None = None
    tone: str | None = None
    summary: str | None = None
    outline: list[dict[str, Any]] | None = None
    templateId: str | None = None

    createVersion: bool = True
    versionNote: str | None = None


class SlidePatch(Payload):
    title: str | None = None
    layout: str | None = None
    blocks: list[dict[str, Any]] | None = None
    speakerNotes: str | None = None
    status: str | None = None


class SlideCreate(Payload):
    title: str = Field(min_length=1, max_length=255)
    layout: str = "content"
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    speakerNotes: str | None = None
    index: int | None = None


class ReorderBody(Payload):
    order: list[str] = Field(min_length=1)


class AiChatBody(Payload):
    message: str = Field(min_length=1)
    slideId: str | None = None
    context: list[str] = Field(default_factory=lambda: ["research", "products"])
    stream: bool = True


class AiApplyBody(Payload):
    changeId: str


class CommentCreate(Payload):
    slideId: str | None = None
    anchor: str | None = None
    body: str = Field(min_length=1)


class ReplyCreate(Payload):
    body: str = Field(min_length=1)


class CommentPatch(Payload):
    resolved: bool | None = None
    body: str | None = None


class SubmitApproval(Payload):
    approverIds: list[str] = Field(min_length=1)
    note: str | None = None


class RestoreBody(Payload):
    note: str | None = None


class ExportRequest(Payload):
    format: ExportFormat = ExportFormat.PDF
    templateId: str | None = None
    includeAppendix: bool = True


class KnowledgeBaseUpsert(Payload):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scope: Scope = Scope.MINE
    tags: list[str] = Field(default_factory=list)
    restricted: bool = False
    icon: str | None = None


class KnowledgeBasePatch(Payload):
    name: str | None = None
    description: str | None = None
    scope: Scope | None = None
    tags: list[str] | None = None
    restricted: bool | None = None
    icon: str | None = None


class SourceCreate(Payload):
    url: str = Field(min_length=4, max_length=1024)
    label: str | None = None


class SkillUpsert(Payload):
    tools: list[str] = Field(default_factory=list)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    stage: SkillStage = SkillStage.RESEARCH
    scope: Scope = Scope.MINE
    prompt: str = ""
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    inputSchema: list[dict[str, Any]] = Field(default_factory=list)


class SkillPatch(Payload):
    tools: list[str] | None = None
    name: str | None = None
    description: str | None = None
    stage: SkillStage | None = None
    scope: Scope | None = None
    prompt: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    inputSchema: list[dict[str, Any]] | None = None
    note: str | None = None


class SkillTest(Payload):
    input: dict[str, Any] = Field(default_factory=dict)


class TemplateUpsert(Payload):
    name: str = Field(min_length=1, max_length=255)
    subtitle: str | None = None
    scope: Scope = Scope.MINE
    tone: str | None = None
    requiredSlides: list[str] = Field(default_factory=list)
    slideCount: int = Field(default=0, ge=0, le=80)
    coverUrl: str | None = None
    theme: dict[str, Any] = Field(default_factory=dict)
    slides: list[dict[str, Any]] = Field(default_factory=list)


class TemplatePatch(Payload):
    name: str | None = None
    subtitle: str | None = None
    scope: Scope | None = None
    tone: str | None = None
    requiredSlides: list[str] | None = None
    slideCount: int | None = Field(default=None, ge=0, le=80)
    coverUrl: str | None = None
    theme: dict[str, Any] | None = None
    slides: list[dict[str, Any]] | None = None
    note: str | None = None


class ShareCreate(Payload):
    resourceType: ResourceType
    resourceId: str
    userIds: list[str] = Field(default_factory=list)
    teamIds: list[str] = Field(default_factory=list)
    permission: Permission = Permission.VIEW
    notify: bool = True


class SharePatch(Payload):
    permission: Permission


class ShareLinkPatch(Payload):
    resourceType: ResourceType
    resourceId: str
    mode: LinkMode
    permission: Permission = Permission.VIEW


class ProfilePatch(Payload):
    name: str | None = Field(default=None, max_length=180)
    title: str | None = Field(default=None, max_length=180)
    phone: str | None = Field(default=None, max_length=40)
    teamId: str | None = None
    avatarColor: str | None = Field(default=None, max_length=9)
    interests: list[str] | None = None


class NotificationSettingsPatch(Payload):
    emailOnResearchDone: bool | None = None
    emailOnApprovalRequest: bool | None = None
    emailOnComment: bool | None = None
    emailOnShare: bool | None = None


class NotificationPatch(Payload):
    read: bool


class InviteCreate(Payload):
    email: EmailStr
    role: Role = Role.MEMBER
    teamId: str | None = None


class AdminUserPatch(Payload):
    role: Role | None = None
    teamId: str | None = None
    title: str | None = None


class RolePermissionsPatch(Payload):
    permissions: list[str]


class AiConfigPatch(Payload):
    model: str | None = None
    visionModel: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    maxTokens: int | None = Field(default=None, ge=256, le=200_000)
    researchDepth: ResearchDepth | None = None
    autoApproveThreshold: int | None = Field(default=None, ge=0, le=100)
    monthlyBudgetUsd: float | None = Field(default=None, ge=0)


class PolicyPatch(Payload):
    allowCompanyLink: bool | None = None
    requireAdminApprovalPublish: bool | None = None
    blockExternalShare: bool | None = None
