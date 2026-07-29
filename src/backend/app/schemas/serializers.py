from typing import Any

from sqlalchemy import inspect as sa_inspect

from app.db.models.collab import AuditLog, Notification
from app.db.models.customer import (
    Customer,
    CustomerActivity,
    CustomerFact,
    CustomerSolution,
    CustomerSource,
)
from app.db.models.enums import Permission
from app.db.models.job import Job
from app.db.models.knowledge import KnowledgeBase, KnowledgeFile, KnowledgeSource
from app.db.models.library import (
    ProposalTemplate,
    Skill,
    SkillVersion,
    TemplateSlide,
    TemplateVersion,
)
from app.db.models.product import Product
from app.db.models.proposal import (
    Proposal,
    ProposalApproval,
    ProposalComment,
    ProposalExport,
    ProposalSlide,
    ProposalVersion,
)
from app.db.models.user import AccessRequest, Team, User


def _loaded(obj: Any, attr: str) -> list[Any]:
    state = sa_inspect(obj)
    if attr in state.unloaded:
        return []
    return list(getattr(obj, attr) or [])


def user_ref(user: User | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {"id": user.id, "name": user.handle, "initials": user.initials}


def directory_entry(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "handle": user.handle,
        "email": user.email,
        "name": user.name,
        "initials": user.initials,
        "team": user.display_team,
        "avatarColor": user.avatar_color,
        "type": "user",
    }


def team_entry(team: Team, member_count: int = 0) -> dict[str, Any]:
    return {
        "id": team.id,
        "handle": team.name,
        "name": team.name,
        "initials": (team.name[:2] or "?").upper(),
        "team": team.name,
        "memberCount": member_count,
        "type": "team",
    }


def me(user: User, permissions: list[str]) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "handle": user.handle,
        "name": user.name,
        "initials": user.initials,
        "role": str(user.role),
        "team": user.display_team,
        "teamId": user.team_id,
        "title": user.title,
        "avatarColor": user.avatar_color,
        "avatarUrl": user.avatar_url,
        "status": str(user.status),
        "isFirstLogin": user.is_first_login,
        "needsOnboarding": user.needs_onboarding,
        "needsTour": user.needs_tour,
        "permissions": permissions,
    }


def profile(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "handle": user.handle,
        "initials": user.initials,
        "title": user.title,
        "phone": user.phone,
        "team": user.display_team,
        "teamId": user.team_id,
        "role": str(user.role),
        "avatarColor": user.avatar_color,
        "avatarUrl": user.avatar_url,
        "interests": user.interests,
        "createdAt": user.created_at,
        "lastLoginAt": user.last_login_at,
    }


def notification_settings(user: User) -> dict[str, Any]:
    prefs = user.notification_settings
    return {
        "emailOnResearchDone": prefs.email_on_research_done if prefs else True,
        "emailOnApprovalRequest": prefs.email_on_approval_request if prefs else True,
        "emailOnComment": prefs.email_on_comment if prefs else False,
        "emailOnShare": prefs.email_on_share if prefs else True,
    }


def admin_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "handle": user.handle,
        "name": user.name,
        "initials": user.initials,
        "role": str(user.role),
        "team": user.display_team,
        "teamId": user.team_id,
        "status": str(user.status),
        "avatarColor": user.avatar_color,
        "lastLoginAt": user.last_login_at,
        "createdAt": user.created_at,
        "lockedUntil": user.locked_until,
    }


def customer_row(customer: Customer) -> dict[str, Any]:
    return {
        "id": customer.id,
        "name": customer.name,
        "shortName": customer.short_name,
        "website": customer.website,
        "industry": customer.industry,
        "status": str(customer.status),
        "owner": user_ref(customer.owner),
        "updatedAt": customer.updated_at,
        "logoUrl": customer.logo_url,
        "archived": customer.archived,
    }


def customer_detail(
    customer: Customer,
    *,
    facts: list[CustomerFact] | None = None,
    sources: list[CustomerSource] | None = None,
    solutions: list[CustomerSolution] | None = None,
    proposals: list[Proposal] | None = None,
    activities: list[CustomerActivity] | None = None,
    permission: Permission = Permission.VIEW,
    active_job: Job | None = None,
) -> dict[str, Any]:
    return {
        **customer_row(customer),
        "profile": customer.profile,
        "notes": customer.notes,
        "research": {
            "sourceCount": customer.source_count,
            "factCount": customer.fact_count,
            "opportunityCount": customer.opportunity_count,
            "researchedAt": customer.researched_at,
            "jobId": customer.last_research_job_id,
            "activeJob": job_ref(active_job) if active_job else None,
        },
        "facts": [fact(f) for f in (facts or [])],
        "sources": [source(s) for s in (sources or [])],
        "solutions": [solution(s) for s in (solutions or [])],
        "proposals": [proposal_row(p) for p in (proposals or [])],
        "activity": [activity(a) for a in (activities or [])],
        "createdAt": customer.created_at,
        "myPermission": str(permission),
    }


def fact(item: CustomerFact) -> dict[str, Any]:
    return {
        "id": item.id,
        "topic": item.topic,
        "summary": item.summary,
        "confidence": item.confidence,
        "verdict": str(item.verdict),
        "sourceUrl": item.source_url,
        "origin": item.origin,
        "isOpportunity": item.is_opportunity,
        "verifiedBy": user_ref(item.verified_by),
        "verifiedAt": item.verified_at,
        "createdAt": item.created_at,
    }


def source(item: CustomerSource) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "url": item.url,
        "title": item.title,
        "status": str(item.status),
        "http": item.http_status,
        "reason": item.reason,
        "fetchedAt": item.fetched_at,
    }


def solution(item: CustomerSolution) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "rationale": item.rationale,
        "fitScore": round(item.fit_score, 1),
        "expectedImpact": item.expected_impact,
        "pricingNote": item.pricing_note,
        "package": item.package,
        "selected": item.selected,
        "evidence": item.evidence,
        "product": (
            {
                "id": item.product.id,
                "name": item.product.name,
                "family": item.product.family,
                "iconUrl": item.product.icon_url,
            }
            if item.product
            else None
        ),
    }


def activity(item: CustomerActivity) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": str(item.kind),
        "title": item.title,
        "body": item.body,
        "actor": user_ref(item.actor),
        "meta": item.meta,
        "createdAt": item.created_at,
    }


def product_row(item: Product) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "family": item.family,
        "group": item.group_name,
        "description": item.description,
        "packages": item.packages,
        "status": item.status,
        "iconUrl": item.icon_url,
        "updatedAt": item.updated_at,
    }


def product_detail(item: Product) -> dict[str, Any]:
    return {
        **product_row(item),
        "positioning": item.positioning,
        "useCases": item.use_cases,
        "pricing": item.pricing,
        "documents": item.documents,
        "capabilities": item.capabilities,
        "targetIndustries": item.target_industries,
        "sourceUrl": item.source_url,
        "lastCrawledAt": item.last_crawled_at,
        "createdAt": item.created_at,
    }


def proposal_row(item: Proposal) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "customer": (
            {"id": item.customer.id, "name": item.customer.name} if item.customer else None
        ),
        "version": item.version,
        "status": str(item.status),
        "templateId": item.template_id,
        "slideCount": item.slide_count,
        "owner": user_ref(item.owner),
        "updatedAt": item.updated_at,
        "archived": item.archived,
    }


def proposal_detail(
    item: Proposal,
    *,
    slides: list[ProposalSlide] | None = None,
    permission: Permission = Permission.VIEW,
    active_job: Job | None = None,
) -> dict[str, Any]:
    return {
        **proposal_row(item),
        "tone": item.tone,
        "outline": item.outline,
        "summary": item.summary,
        "generationParams": item.generation_params,
        "template": (
            {
                "id": item.template.id,
                "name": item.template.name,
                "version": item.template.version,
                "requiredSlides": item.template.required_slides,
            }
            if item.template
            else None
        ),
        "slides": [slide(s) for s in (slides if slides is not None else _loaded(item, "slides"))],
        "activeJob": job_ref(active_job) if active_job else None,
        "createdAt": item.created_at,
        "myPermission": str(permission),
    }


def slide(item: ProposalSlide) -> dict[str, Any]:
    return {
        "id": item.id,
        "index": item.index,
        "title": item.title,
        "layout": item.layout,
        "blocks": item.blocks,
        "speakerNotes": item.speaker_notes,
        "status": item.status,
        "citations": item.citations,
        "updatedAt": item.updated_at,
    }


def comment(item: ProposalComment, replies: list[ProposalComment] | None = None) -> dict[str, Any]:
    return {
        "id": item.id,
        "slideId": item.slide_id,
        "anchor": item.anchor,
        "body": item.body,
        "author": user_ref(item.author),
        "resolved": item.resolved,
        "createdAt": item.created_at,
        "replies": [
            comment(r, replies=[])
            for r in (replies if replies is not None else _loaded(item, "replies"))
        ],
    }


def proposal_version(item: ProposalVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "version": item.version,
        "author": user_ref(item.author),
        "note": item.note,
        "createdAt": item.created_at,
    }


def approval(item: ProposalApproval) -> dict[str, Any]:
    return {
        "id": item.id,
        "approver": user_ref(item.approver),
        "status": str(item.status),
        "round": item.round,
        "note": item.note,
        "reason": item.reason,
        "version": item.version,
        "decidedAt": item.decided_at,
        "createdAt": item.created_at,
    }


def export(item: ProposalExport, download_url: str | None = None) -> dict[str, Any]:
    return {
        "id": item.id,
        "proposalId": item.proposal_id,
        "format": str(item.format),
        "status": str(item.status),
        "stage": str(item.stage),
        "progress": round(item.progress, 2),
        "downloadUrl": download_url,
        "filename": item.filename,
        "sizeBytes": item.size_bytes,
        "error": item.error,
        "includeAppendix": item.include_appendix,
        "createdBy": user_ref(item.created_by),
        "createdAt": item.created_at,
        "completedAt": item.completed_at,
    }


def knowledge_base(
    item: KnowledgeBase, *, locked: bool = False, sources: list[KnowledgeSource] | None = None
) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "scope": str(item.scope),
        "owner": item.owner.name if item.owner else "Admin",
        "ownerId": item.owner_id,
        "tags": item.tags,
        "docCount": item.doc_count,
        "sources": [s.url for s in (sources if sources is not None else _loaded(item, "sources"))],
        "locked": locked,
        "icon": item.icon,
        "updatedAt": item.updated_at,
    }


def knowledge_file(item: KnowledgeFile) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "ext": item.ext,
        "sizeBytes": item.size_bytes,
        "status": str(item.status),
        "pageCount": item.page_count,
        "summary": item.summary,
        "indexError": item.index_error,
        "uploadedBy": user_ref(item.uploaded_by),
        "indexedAt": item.indexed_at,
        "createdAt": item.created_at,
    }


def knowledge_source(item: KnowledgeSource) -> dict[str, Any]:
    return {
        "id": item.id,
        "url": item.url,
        "label": item.label,
        "status": item.status,
        "docCount": item.doc_count,
        "lastSyncedAt": item.last_synced_at,
        "lastError": item.last_error,
    }


def skill_row(item: Skill, *, locked: bool = False) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "stage": str(item.stage),
        "scope": str(item.scope),
        "version": item.version,
        "owner": user_ref(item.owner),
        "isBuiltin": item.is_builtin,
        "locked": locked,
        "updatedAt": item.updated_at,
    }


def skill_detail(item: Skill) -> dict[str, Any]:
    return {
        **skill_row(item),
        "prompt": item.prompt,
        "tools": item.tools,
        "model": item.model,
        "temperature": item.temperature,
        "inputSchema": item.input_schema,
        "createdAt": item.created_at,
    }


def skill_version(item: SkillVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "version": item.version,
        "note": item.note,
        "author": user_ref(item.author),
        "createdAt": item.created_at,
    }


def template_row(item: ProposalTemplate, *, locked: bool = False) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "subtitle": item.subtitle,
        "scope": str(item.scope),
        "version": item.version,
        "slideCount": item.slide_count,
        "requiredSlides": item.required_slides,
        "tone": item.tone,
        "coverUrl": item.cover_url,
        "theme": item.theme,
        "sourceFilename": item.source_filename,
        "sizeBytes": item.size_bytes,
        "usedCount": item.used_count,
        "owner": user_ref(item.owner),
        "isBuiltin": item.is_builtin,
        "locked": locked,
        "updatedAt": item.updated_at,
    }


def template_detail(
    item: ProposalTemplate, slides: list[TemplateSlide] | None = None
) -> dict[str, Any]:
    return {
        **template_row(item),
        "theme": item.theme,
        "sourceFilename": item.source_filename,
        "sizeBytes": item.size_bytes,
        "slides": [
            template_slide(s) for s in (slides if slides is not None else _loaded(item, "slides"))
        ],
        "createdAt": item.created_at,
    }


def template_slide(item: TemplateSlide) -> dict[str, Any]:
    return {
        "id": item.id,
        "index": item.index,
        "title": item.title,
        "layout": item.layout,
        "imageUrl": item.image_url,
        "placeholders": item.placeholders,
        "guidance": item.guidance,
        "optional": item.optional,
    }


def template_version(item: TemplateVersion) -> dict[str, Any]:
    return {
        "id": item.id,
        "version": item.version,
        "note": item.note,
        "author": user_ref(item.author),
        "createdAt": item.created_at,
    }


def notification(item: Notification) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": str(item.type),
        "title": item.title,
        "body": item.body,
        "targetUrl": item.target_url,
        "read": item.read,
        "actor": user_ref(item.actor),
        "createdAt": item.created_at,
    }


def audit_row(item: AuditLog) -> dict[str, Any]:
    return {
        "id": item.id,
        "actor": user_ref(item.actor) or {"name": item.actor_label},
        "category": str(item.category),
        "action": item.action,
        "resourceType": item.resource_type,
        "resourceId": item.resource_id,
        "resourceLabel": item.resource_label,
        "summary": item.summary,
        "createdAt": item.created_at,
    }


def audit_detail(item: AuditLog) -> dict[str, Any]:
    return {**audit_row(item), "meta": item.meta, "ip": item.ip, "userAgent": item.user_agent}


def access_request(item: AccessRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": str(item.type),
        "status": str(item.status),
        "requester": (
            directory_entry(item.requester)
            if item.requester
            else {
                "email": item.requester_email,
                "name": item.requester_name or item.requester_email,
            }
        ),
        "target": {
            "resourceType": item.resource_type,
            "resourceId": item.resource_id,
            "label": item.resource_label,
        },
        "permission": str(item.permission) if item.permission else None,
        "requestedRole": str(item.requested_role) if item.requested_role else None,
        "reason": item.reason,
        "decisionNote": item.decision_note,
        "decidedAt": item.decided_at,
        "createdAt": item.created_at,
    }


def job_ref(item: Job) -> dict[str, Any]:
    return {
        "jobId": item.id,
        "kind": str(item.kind),
        "status": str(item.status),
        "stage": item.stage,
        "stageLabel": item.stage_label,
        "progress": round(item.progress, 2),
        "error": item.error,
        "result": item.result,
        "createdAt": item.created_at,
        "startedAt": item.started_at,
        "finishedAt": item.finished_at,
    }
