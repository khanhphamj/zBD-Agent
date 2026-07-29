from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core import synonyms
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import PageParams, page_params, paginate, parse_sort
from app.core.security import now
from app.core.text import next_version, normalize
from app.db.models.customer import Customer, CustomerActivity
from app.db.models.enums import (
    ActivityKind,
    CustomerStatus,
    JobKind,
    JobStatus,
    NotificationType,
    Permission,
    ProposalStatus,
    RequestStatus,
    ResourceType,
)
from app.db.models.job import Job
from app.db.models.library import ProposalTemplate
from app.db.models.proposal import (
    AiSuggestedChange,
    Proposal,
    ProposalApproval,
    ProposalComment,
    ProposalSlide,
    ProposalVersion,
)
from app.db.models.user import User
from app.integrations.resend_client import resend
from app.schemas.requests import (
    BulkAction,
    CommentCreate,
    CommentPatch,
    ProposalCreate,
    ProposalPatch,
    ReorderBody,
    ReplyCreate,
    RestoreBody,
    SlideCreate,
    SlidePatch,
    SubmitApproval,
)
from app.schemas.serializers import (
    approval as serialize_approval,
)
from app.schemas.serializers import (
    comment as serialize_comment,
)
from app.schemas.serializers import (
    job_ref,
    proposal_detail,
    proposal_row,
)
from app.schemas.serializers import (
    proposal_version as serialize_version,
)
from app.schemas.serializers import (
    slide as serialize_slide,
)
from app.services import audit
from app.services.notifications import email_if_opted_in, push
from app.services.queue import enqueue
from app.services.shares import apply_visibility, purge_links, require

router = APIRouter(tags=["proposals"])

SORTABLE = {
    "title": Proposal.title_norm,
    "updatedAt": Proposal.updated_at,
    "createdAt": Proposal.created_at,
    "status": Proposal.status,
    "slideCount": Proposal.slide_count,
}


@router.get("/proposals", summary="Danh sách proposal")
async def list_proposals(
    user: CurrentUser,
    session: SessionDep,
    params: Annotated[PageParams, Depends(page_params)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    customerId: Annotated[str | None, Query()] = None,
    owner: Annotated[str | None, Query()] = None,
    archived: Annotated[bool | None, Query()] = False,
) -> dict[str, Any]:
    stmt = select(Proposal).options(selectinload(Proposal.owner), selectinload(Proposal.customer))
    stmt = apply_visibility(stmt, ResourceType.PROPOSAL, user)

    if archived is not None:
        stmt = stmt.where(Proposal.archived.is_(archived))
    if status_filter:
        stmt = stmt.where(Proposal.status == status_filter)
    if customerId:
        stmt = stmt.where(Proposal.customer_id == customerId)
    if owner:
        if owner == "me":
            stmt = stmt.where(Proposal.owner_id == user.id)
        else:
            stmt = stmt.where(
                or_(
                    Proposal.owner_id == owner,
                    Proposal.owner_id.in_(select(User.id).where(User.handle == owner)),
                )
            )
    if params.q:
        clauses = [
            or_(
                Proposal.title_norm.ilike(term),
                Proposal.customer_id.in_(select(Customer.id).where(Customer.name_norm.ilike(term))),
            )
            for term in synonyms.patterns(params.q)
        ]
        if clauses:
            stmt = stmt.where(or_(*clauses))

    stmt = stmt.order_by(parse_sort(params.sort, SORTABLE, Proposal.updated_at.desc()))
    return await paginate(session, stmt, params, proposal_row)


@router.post(
    "/proposals", status_code=status.HTTP_201_CREATED, summary="Tạo proposal + chạy generate"
)
async def create_proposal(
    body: ProposalCreate, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    customer, _ = await require(
        session, user, ResourceType.CUSTOMER, body.customerId, Permission.VIEW
    )

    template: ProposalTemplate | None = None
    if body.templateId:
        template = await session.get(ProposalTemplate, body.templateId)
        if template is None:
            raise NotFoundError("Proposal template")

    title = body.title or f"{customer.short_name or customer.name} – Đề xuất giải pháp"
    proposal = Proposal(
        id=Proposal.make_id(),
        title=title,
        title_norm=normalize(title),
        customer_id=customer.id,
        template_id=body.templateId,
        owner_id=user.id,
        status=ProposalStatus.DRAFT,
        version="v1.0",
        tone=body.tone,
        slide_count=0,
        generation_params={
            "solutionIds": body.solutionIds,
            "knowledgeBaseIds": body.knowledgeBaseIds,
            "skillIds": body.skillIds,
            "tone": body.tone,
            "slideCount": body.slideCount,
            "templateId": body.templateId,
        },
    )
    session.add(proposal)
    await session.flush()

    job = Job(
        id=Job.make_id(),
        kind=JobKind.GENERATE,
        status=JobStatus.QUEUED,
        proposal_id=proposal.id,
        customer_id=customer.id,
        created_by_id=user.id,
        params=dict(proposal.generation_params),
    )
    session.add(job)
    await session.flush()
    proposal.last_generate_job_id = job.id

    if template:
        template.used_count += 1

    if customer.status in (CustomerStatus.NEW, CustomerStatus.RESEARCHED):
        customer.status = CustomerStatus.BUILDING_PROPOSAL
    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer.id,
            actor_id=user.id,
            kind=ActivityKind.PROPOSAL_CREATED,
            title="Tạo proposal",
            body=title,
            meta={"proposalId": proposal.id, "jobId": job.id},
        )
    )
    await audit.record(
        session,
        action="proposal.create",
        summary=f"{user.name} tạo proposal “{title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal.id,
        resource_label=title,
        request=request,
    )
    await session.commit()

    await enqueue("run_generate", job.id)

    await session.refresh(proposal)
    return {"proposal": proposal_row(proposal), "generateJobId": job.id}


@router.get("/proposals/{proposal_id}", summary="Nội dung proposal đầy đủ")
async def get_proposal(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    proposal, access = await require(
        session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW
    )
    slides = await _slides(session, proposal_id)
    active = (
        await session.execute(
            select(Job)
            .where(
                Job.proposal_id == proposal_id,
                Job.kind == JobKind.GENERATE,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return proposal_detail(proposal, slides=slides, permission=access.permission, active_job=active)


async def _slides(session: SessionDep, proposal_id: str) -> list[ProposalSlide]:
    return list(
        (
            await session.execute(
                select(ProposalSlide)
                .where(ProposalSlide.proposal_id == proposal_id)
                .order_by(ProposalSlide.index)
            )
        )
        .scalars()
        .all()
    )


async def _snapshot(session: SessionDep, proposal: Proposal) -> dict[str, Any]:
    slides = await _slides(session, proposal.id)
    return {
        "proposal": {
            "title": proposal.title,
            "status": str(proposal.status),
            "tone": proposal.tone,
            "outline": proposal.outline,
            "summary": proposal.summary,
            "templateId": proposal.template_id,
        },
        "slides": [
            {
                "id": s.id,
                "index": s.index,
                "title": s.title,
                "layout": s.layout,
                "blocks": s.blocks,
                "speakerNotes": s.speaker_notes,
                "citations": s.citations,
            }
            for s in slides
        ],
    }


async def _record_version(
    session: SessionDep, proposal: Proposal, author: User, note: str | None
) -> ProposalVersion:
    snapshot = await _snapshot(session, proposal)
    existing = (
        await session.execute(
            select(ProposalVersion).where(
                ProposalVersion.proposal_id == proposal.id,
                ProposalVersion.version == proposal.version,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.snapshot = snapshot
        existing.author_id = author.id
        if note:
            existing.note = note
        return existing

    row = ProposalVersion(
        id=ProposalVersion.make_id(),
        proposal_id=proposal.id,
        version=proposal.version,
        author_id=author.id,
        note=note,
        snapshot=snapshot,
    )
    session.add(row)
    return row


async def _bump_and_record(
    session: SessionDep, proposal: Proposal, author: User, note: str | None
) -> ProposalVersion:
    proposal.version = next_version(proposal.version)
    await session.flush()
    return await _record_version(session, proposal, author, note)


@router.patch("/proposals/{proposal_id}", summary="Cập nhật proposal")
async def update_proposal(
    proposal_id: str,
    body: ProposalPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)

    if body.status is not None:
        if body.status not in {s.value for s in ProposalStatus}:
            raise ValidationError(f"Trạng thái không hợp lệ: {body.status}", field="status")
        new_status = ProposalStatus(body.status)

        if new_status in (
            ProposalStatus.PENDING_APPROVAL,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
        ):
            raise ValidationError(
                "Trạng thái duyệt chỉ thay đổi qua các endpoint submit/approve/reject.",
                field="status",
            )
        proposal.status = new_status

    content_changed = any(
        v is not None for v in (body.title, body.outline, body.summary, body.tone)
    )

    if body.title is not None:
        proposal.title = body.title
        proposal.title_norm = normalize(body.title)
    for field, value in (
        ("tone", body.tone),
        ("summary", body.summary),
        ("outline", body.outline),
        ("template_id", body.templateId),
    ):
        if value is not None:
            setattr(proposal, field, value)

    if body.createVersion and content_changed:
        await _bump_and_record(session, proposal, user, body.versionNote)

    await audit.record(
        session,
        action="proposal.update",
        summary=f"{user.name} cập nhật proposal “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return proposal_row(proposal)


@router.delete(
    "/proposals/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá proposal"
)
async def delete_proposal(
    proposal_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    label = proposal.title
    purged = await purge_links(session, ResourceType.PROPOSAL, proposal_id)
    await audit.record(
        session,
        action="proposal.delete",
        summary=f"{user.name} xoá proposal “{label}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=label,
        meta={"purged": purged},
        request=request,
    )
    await session.delete(proposal)
    await session.commit()


@router.post("/proposals/{proposal_id}/archive", summary="Lưu trữ / bỏ lưu trữ")
async def archive_proposal(
    proposal_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    archived: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    proposal.archived = archived
    await audit.record(
        session,
        action="proposal.archive",
        summary=f"{user.name} {'lưu trữ' if archived else 'bỏ lưu trữ'} “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return proposal_row(proposal)


@router.post("/proposals/{proposal_id}/duplicate", summary="Nhân bản proposal")
async def duplicate_proposal(
    proposal_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    slides = await _slides(session, proposal_id)

    title = f"{proposal.title} (bản sao)"
    clone = Proposal(
        id=Proposal.make_id(),
        title=title,
        title_norm=normalize(title),
        customer_id=proposal.customer_id,
        template_id=proposal.template_id,
        owner_id=user.id,
        status=ProposalStatus.DRAFT,
        version="v1.0",
        tone=proposal.tone,
        outline=proposal.outline,
        summary=proposal.summary,
        generation_params=proposal.generation_params,
        slide_count=len(slides),
    )
    session.add(clone)
    await session.flush()

    for s in slides:
        session.add(
            ProposalSlide(
                id=ProposalSlide.make_id(),
                proposal_id=clone.id,
                index=s.index,
                title=s.title,
                layout=s.layout,
                blocks=s.blocks,
                speaker_notes=s.speaker_notes,
                citations=s.citations,
                status=s.status,
            )
        )

    await audit.record(
        session,
        action="proposal.duplicate",
        summary=f"{user.name} nhân bản proposal “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=clone.id,
        resource_label=title,
        meta={"sourceId": proposal_id},
        request=request,
    )
    await session.commit()
    await session.refresh(clone)
    return proposal_row(clone)


@router.post("/proposals/bulk", summary="Thao tác hàng loạt")
async def bulk_proposals(
    body: BulkAction, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    done: list[str] = []
    skipped: list[dict[str, str]] = []

    for pid in body.ids:
        try:
            proposal, _ = await require(session, user, ResourceType.PROPOSAL, pid, Permission.EDIT)
        except (NotFoundError, ForbiddenError) as exc:
            skipped.append({"id": pid, "reason": exc.code})
            continue

        if body.action == "delete":
            await session.delete(proposal)
        elif body.action == "archive":
            proposal.archived = True
        elif body.action == "unarchive":
            proposal.archived = False
        elif body.action == "duplicate":
            slides = await _slides(session, pid)
            title = f"{proposal.title} (bản sao)"
            clone = Proposal(
                id=Proposal.make_id(),
                title=title,
                title_norm=normalize(title),
                customer_id=proposal.customer_id,
                template_id=proposal.template_id,
                owner_id=user.id,
                status=ProposalStatus.DRAFT,
                version="v1.0",
                tone=proposal.tone,
                outline=proposal.outline,
                generation_params=proposal.generation_params,
                slide_count=len(slides),
            )
            session.add(clone)
            await session.flush()
            for s in slides:
                session.add(
                    ProposalSlide(
                        id=ProposalSlide.make_id(),
                        proposal_id=clone.id,
                        index=s.index,
                        title=s.title,
                        layout=s.layout,
                        blocks=s.blocks,
                        speaker_notes=s.speaker_notes,
                        citations=s.citations,
                    )
                )
        done.append(pid)

    await audit.record(
        session,
        action=f"proposal.bulk_{body.action}",
        summary=f"{user.name} {body.action} {len(done)} proposal",
        actor=user,
        resource_type="proposal",
        meta={"ids": done, "skipped": skipped},
        request=request,
    )
    await session.commit()
    return {"action": body.action, "okCount": len(done), "ids": done, "skipped": skipped}


@router.post("/proposals/{proposal_id}/regenerate", summary="Tạo lại nội dung")
async def regenerate(
    proposal_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, str]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)

    if proposal.slide_count:
        await _record_version(session, proposal, user, "Trước khi tạo lại nội dung")

    job = Job(
        id=Job.make_id(),
        kind=JobKind.GENERATE,
        status=JobStatus.QUEUED,
        proposal_id=proposal_id,
        customer_id=proposal.customer_id,
        created_by_id=user.id,
        params=dict(proposal.generation_params or {}),
    )
    session.add(job)
    await session.flush()
    proposal.last_generate_job_id = job.id

    await audit.record(
        session,
        action="proposal.regenerate",
        summary=f"{user.name} tạo lại nội dung “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        request=request,
    )
    await session.commit()
    await enqueue("run_generate", job.id)
    return {"jobId": job.id, "generateJobId": job.id}


@router.get("/proposals/{proposal_id}/slides", summary="Danh sách slide")
async def list_slides(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    slides = await _slides(session, proposal_id)
    return {"items": [serialize_slide(s) for s in slides], "total": len(slides)}


@router.post(
    "/proposals/{proposal_id}/slides",
    status_code=status.HTTP_201_CREATED,
    summary="Thêm slide",
)
async def create_slide(
    proposal_id: str,
    body: SlideCreate,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    highest = int(
        (
            await session.execute(
                select(func.coalesce(func.max(ProposalSlide.index), 0)).where(
                    ProposalSlide.proposal_id == proposal_id
                )
            )
        ).scalar_one()
        or 0
    )
    target = body.index if body.index is not None else highest + 1

    if body.index is not None and body.index <= highest:
        for s in reversed(await _slides(session, proposal_id)):
            if s.index >= target:
                s.index += 1
        await session.flush()

    item = ProposalSlide(
        id=ProposalSlide.make_id(),
        proposal_id=proposal_id,
        index=target,
        title=body.title,
        layout=body.layout,
        blocks=body.blocks,
        speaker_notes=body.speakerNotes,
    )
    session.add(item)
    proposal.slide_count = highest + 1
    await session.commit()
    return serialize_slide(item)


@router.patch("/proposals/{proposal_id}/slides/{slide_id}", summary="Sửa nội dung slide")
async def update_slide(
    proposal_id: str,
    slide_id: str,
    body: SlidePatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    item = await session.get(ProposalSlide, slide_id)
    if item is None or item.proposal_id != proposal_id:
        raise NotFoundError("Slide")

    for field, value in (
        ("title", body.title),
        ("layout", body.layout),
        ("blocks", body.blocks),
        ("speaker_notes", body.speakerNotes),
        ("status", body.status),
    ):
        if value is not None:
            setattr(item, field, value)

    await audit.record(
        session,
        action="slide.update",
        summary=f"{user.name} sửa slide {item.index} của “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"slideId": slide_id},
        request=request,
    )
    await session.commit()
    return serialize_slide(item)


@router.post("/proposals/{proposal_id}/slides/reorder", summary="Sắp xếp lại slide")
async def reorder_slides(
    proposal_id: str, body: ReorderBody, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    slides = {s.id: s for s in await _slides(session, proposal_id)}

    unknown = [sid for sid in body.order if sid not in slides]
    if unknown:
        raise ValidationError(
            f"Slide không thuộc proposal này: {', '.join(unknown)}", field="order"
        )
    if len(body.order) != len(slides):
        raise ValidationError(
            f"Cần đủ {len(slides)} slide trong danh sách sắp xếp, nhận được {len(body.order)}.",
            field="order",
        )

    for offset, sid in enumerate(body.order, start=1):
        slides[sid].index = -offset
    await session.flush()
    for offset, sid in enumerate(body.order, start=1):
        slides[sid].index = offset
    await session.commit()

    return {"items": [serialize_slide(s) for s in await _slides(session, proposal_id)]}


@router.delete(
    "/proposals/{proposal_id}/slides/{slide_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Xoá slide",
)
async def delete_slide(
    proposal_id: str, slide_id: str, user: CurrentUser, session: SessionDep
) -> None:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    item = await session.get(ProposalSlide, slide_id)
    if item is None or item.proposal_id != proposal_id:
        raise NotFoundError("Slide")

    await session.delete(item)
    await session.flush()

    for offset, s in enumerate(await _slides(session, proposal_id), start=1):
        s.index = offset
    proposal.slide_count = max(0, proposal.slide_count - 1)
    await session.commit()


@router.get("/proposals/{proposal_id}/comments", summary="Bình luận")
async def list_comments(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(ProposalComment)
                .where(
                    ProposalComment.proposal_id == proposal_id,
                    ProposalComment.parent_id.is_(None),
                )
                .options(
                    selectinload(ProposalComment.author),
                    selectinload(ProposalComment.replies).selectinload(ProposalComment.author),
                )
                .order_by(ProposalComment.created_at)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {"items": [serialize_comment(c) for c in rows], "total": len(rows)}


@router.post(
    "/proposals/{proposal_id}/comments",
    status_code=status.HTTP_201_CREATED,
    summary="Thêm bình luận",
)
async def create_comment(
    proposal_id: str,
    body: CommentCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(
        session, user, ResourceType.PROPOSAL, proposal_id, Permission.COMMENT
    )
    item = ProposalComment(
        id=ProposalComment.make_id(),
        proposal_id=proposal_id,
        slide_id=body.slideId,
        anchor=body.anchor,
        body=body.body,
        author_id=user.id,
    )
    session.add(item)
    await session.flush()

    if proposal.owner_id != user.id:
        owner = await session.get(User, proposal.owner_id)
        if owner:
            await push(
                session,
                user_id=owner.id,
                kind=NotificationType.COMMENT,
                title="Bình luận mới",
                body=f"{user.name}: {body.body[:120]}",
                target_url=f"/proposals/{proposal_id}",
                actor_id=user.id,
            )
            await email_if_opted_in(
                session,
                owner,
                NotificationType.COMMENT,
                lambda: resend.send_comment(
                    owner.email,
                    actor_name=user.name,
                    proposal_title=proposal.title,
                    body_text=body.body,
                    url=f"/proposals/{proposal_id}",
                ),
            )

    await audit.record(
        session,
        action="comment.create",
        summary=f"{user.name} bình luận trên “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return serialize_comment(item, replies=[])


@router.post(
    "/comments/{comment_id}/replies",
    status_code=status.HTTP_201_CREATED,
    summary="Trả lời bình luận",
)
async def reply_comment(
    comment_id: str, body: ReplyCreate, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    parent = await session.get(ProposalComment, comment_id)
    if parent is None:
        raise NotFoundError("Bình luận")
    await require(session, user, ResourceType.PROPOSAL, parent.proposal_id, Permission.COMMENT)

    item = ProposalComment(
        id=ProposalComment.make_id(),
        proposal_id=parent.proposal_id,
        slide_id=parent.slide_id,
        parent_id=parent.id,
        body=body.body,
        author_id=user.id,
    )
    session.add(item)
    await session.flush()

    if parent.author_id and parent.author_id != user.id:
        await push(
            session,
            user_id=parent.author_id,
            kind=NotificationType.COMMENT,
            title="Trả lời bình luận",
            body=f"{user.name}: {body.body[:120]}",
            target_url=f"/proposals/{parent.proposal_id}",
            actor_id=user.id,
        )
    await session.commit()
    await session.refresh(item)
    return serialize_comment(item, replies=[])


@router.patch("/comments/{comment_id}", summary="Sửa / đánh dấu đã xử lý")
async def patch_comment(
    comment_id: str, body: CommentPatch, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    item = await session.get(ProposalComment, comment_id)
    if item is None:
        raise NotFoundError("Bình luận")
    await require(session, user, ResourceType.PROPOSAL, item.proposal_id, Permission.COMMENT)

    if body.body is not None:
        if item.author_id != user.id:
            raise ForbiddenError("Chỉ tác giả sửa được nội dung bình luận.")
        item.body = body.body
    if body.resolved is not None:
        item.resolved = body.resolved
        item.resolved_by_id = user.id if body.resolved else None
        item.resolved_at = now() if body.resolved else None

    await session.commit()
    await session.refresh(item)
    return serialize_comment(item)


@router.delete(
    "/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá bình luận"
)
async def delete_comment(comment_id: str, user: CurrentUser, session: SessionDep) -> None:
    item = await session.get(ProposalComment, comment_id)
    if item is None:
        raise NotFoundError("Bình luận")
    _, access = await require(
        session, user, ResourceType.PROPOSAL, item.proposal_id, Permission.COMMENT
    )
    if item.author_id != user.id and not access.can_edit and not user.is_admin:
        raise ForbiddenError("Chỉ tác giả hoặc chủ proposal xoá được bình luận.")
    await session.delete(item)
    await session.commit()


@router.get("/proposals/{proposal_id}/versions", summary="Lịch sử phiên bản")
async def list_versions(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(ProposalVersion)
                .where(ProposalVersion.proposal_id == proposal_id)
                .options(selectinload(ProposalVersion.author))
                .order_by(ProposalVersion.created_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {
        "items": [serialize_version(v) for v in rows],
        "current": proposal.version,
        "total": len(rows),
    }


@router.get("/proposals/{proposal_id}/versions/{version}", summary="Xem nội dung một phiên bản")
async def get_version(
    proposal_id: str, version: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    row = await _find_version(session, proposal_id, version)
    return {**serialize_version(row), "snapshot": row.snapshot}


async def _find_version(session: SessionDep, proposal_id: str, version: str) -> ProposalVersion:
    row = (
        await session.execute(
            select(ProposalVersion)
            .where(
                ProposalVersion.proposal_id == proposal_id,
                ProposalVersion.version == version,
            )
            .options(selectinload(ProposalVersion.author))
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Phiên bản {version}")
    return row


@router.get(
    "/proposals/{proposal_id}/versions/{version_a}/diff/{version_b}",
    summary="So sánh hai phiên bản",
)
async def diff_versions(
    proposal_id: str,
    version_a: str,
    version_b: str,
    user: CurrentUser,
    session: SessionDep,
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    a = await _find_version(session, proposal_id, version_a)
    b = await _find_version(session, proposal_id, version_b)

    slides_a = {s["index"]: s for s in (a.snapshot.get("slides") or [])}
    slides_b = {s["index"]: s for s in (b.snapshot.get("slides") or [])}

    changes: list[dict[str, Any]] = []
    for index in sorted(set(slides_a) | set(slides_b)):
        left, right = slides_a.get(index), slides_b.get(index)
        if left is None:
            changes.append({"index": index, "change": "added", "title": right["title"]})
        elif right is None:
            changes.append({"index": index, "change": "removed", "title": left["title"]})
        elif left.get("title") != right.get("title") or left.get("blocks") != right.get("blocks"):
            changes.append(
                {
                    "index": index,
                    "change": "modified",
                    "title": right["title"],
                    "titleChanged": left.get("title") != right.get("title"),
                    "before": {"title": left.get("title"), "blocks": left.get("blocks")},
                    "after": {"title": right.get("title"), "blocks": right.get("blocks")},
                }
            )
        else:
            changes.append({"index": index, "change": "unchanged", "title": right["title"]})

    return {
        "from": version_a,
        "to": version_b,
        "slides": changes,
        "summary": {
            "added": sum(1 for c in changes if c["change"] == "added"),
            "removed": sum(1 for c in changes if c["change"] == "removed"),
            "modified": sum(1 for c in changes if c["change"] == "modified"),
            "unchanged": sum(1 for c in changes if c["change"] == "unchanged"),
        },
    }


@router.post("/proposals/{proposal_id}/versions/{version}/restore", summary="Khôi phục phiên bản")
async def restore_version(
    proposal_id: str,
    version: str,
    body: RestoreBody,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    target = await _find_version(session, proposal_id, version)

    snapshot = target.snapshot or {}
    meta = snapshot.get("proposal") or {}
    if meta.get("title"):
        proposal.title = meta["title"]
        proposal.title_norm = normalize(meta["title"])
    proposal.tone = meta.get("tone", proposal.tone)
    proposal.outline = meta.get("outline", proposal.outline)
    proposal.summary = meta.get("summary", proposal.summary)

    for existing in await _slides(session, proposal_id):
        await session.delete(existing)
    await session.flush()

    restored = snapshot.get("slides") or []
    for entry in restored:
        session.add(
            ProposalSlide(
                id=ProposalSlide.make_id(),
                proposal_id=proposal_id,
                index=entry["index"],
                title=entry["title"],
                layout=entry.get("layout", "content"),
                blocks=entry.get("blocks") or [],
                speaker_notes=entry.get("speakerNotes"),
                citations=entry.get("citations") or [],
            )
        )
    proposal.slide_count = len(restored)

    await _bump_and_record(session, proposal, user, body.note or f"Khôi phục từ {version}")

    await audit.record(
        session,
        action="proposal.restore",
        summary=f"{user.name} khôi phục “{proposal.title}” về {version}",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"restoredFrom": version, "newVersion": proposal.version},
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return {**proposal_row(proposal), "restoredFrom": version}


@router.post("/proposals/{proposal_id}/submit", summary="Gửi duyệt")
async def submit_for_approval(
    proposal_id: str,
    body: SubmitApproval,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    if proposal.status == ProposalStatus.PENDING_APPROVAL:
        raise ValidationError("Proposal đang chờ duyệt.")

    current_round = (
        int(
            (
                await session.execute(
                    select(func.coalesce(func.max(ProposalApproval.round), 0)).where(
                        ProposalApproval.proposal_id == proposal_id
                    )
                )
            ).scalar_one()
            or 0
        )
        + 1
    )

    approvers: list[User] = []
    for approver_id in dict.fromkeys(body.approverIds):
        approver = await session.get(User, approver_id)
        if approver is None:
            raise NotFoundError(f"Người duyệt {approver_id}")
        approvers.append(approver)
        session.add(
            ProposalApproval(
                id=ProposalApproval.make_id(),
                proposal_id=proposal_id,
                approver_id=approver.id,
                requested_by_id=user.id,
                status=RequestStatus.PENDING,
                round=current_round,
                note=body.note,
                version=proposal.version,
            )
        )

    proposal.status = ProposalStatus.PENDING_APPROVAL

    for approver in approvers:
        await push(
            session,
            user_id=approver.id,
            kind=NotificationType.APPROVAL_REQUEST,
            title="Đề nghị duyệt proposal",
            body=f"{user.name} đề nghị bạn duyệt “{proposal.title}”.",
            target_url=f"/proposals/{proposal_id}",
            actor_id=user.id,
            meta={"round": current_round},
        )
        await email_if_opted_in(
            session,
            approver,
            NotificationType.APPROVAL_REQUEST,
            lambda a=approver: resend.send_approval_request(
                a.email,
                requester_name=user.name,
                proposal_title=proposal.title,
                url=f"/proposals/{proposal_id}",
                note=body.note,
            ),
        )

    await audit.record(
        session,
        action="approval.submit",
        summary=f"{user.name} gửi duyệt “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"approverIds": body.approverIds, "round": current_round},
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return {**proposal_row(proposal), "round": current_round}


@router.post("/proposals/{proposal_id}/withdraw", summary="Thu hồi về Draft")
async def withdraw(
    proposal_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.EDIT)
    if proposal.status != ProposalStatus.PENDING_APPROVAL:
        raise ValidationError("Chỉ thu hồi được proposal đang chờ duyệt.")

    pending = (
        (
            await session.execute(
                select(ProposalApproval).where(
                    ProposalApproval.proposal_id == proposal_id,
                    ProposalApproval.status == RequestStatus.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in pending:
        await session.delete(row)

    proposal.status = ProposalStatus.DRAFT
    await audit.record(
        session,
        action="approval.withdraw",
        summary=f"{user.name} thu hồi đề nghị duyệt “{proposal.title}”",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return proposal_row(proposal)


async def _decide(
    session: SessionDep,
    proposal_id: str,
    user: User,
    approved: bool,
    note: str | None,
    reason: str | None,
    request: Request,
) -> dict[str, Any]:
    proposal = await session.get(Proposal, proposal_id)
    if proposal is None:
        raise NotFoundError("Proposal")

    row = (
        await session.execute(
            select(ProposalApproval)
            .where(
                ProposalApproval.proposal_id == proposal_id,
                ProposalApproval.approver_id == user.id,
                ProposalApproval.status == RequestStatus.PENDING,
            )
            .order_by(ProposalApproval.round.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ForbiddenError("Bạn không nằm trong danh sách người duyệt của proposal này.")

    row.status = RequestStatus.APPROVED if approved else RequestStatus.REJECTED
    row.note = note
    row.reason = reason
    row.decided_at = now()

    if approved:
        outstanding = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ProposalApproval)
                    .where(
                        ProposalApproval.proposal_id == proposal_id,
                        ProposalApproval.round == row.round,
                        ProposalApproval.status == RequestStatus.PENDING,
                    )
                )
            ).scalar_one()
            or 0
        )
        if outstanding == 0:
            proposal.status = ProposalStatus.APPROVED
    else:
        proposal.status = ProposalStatus.REJECTED

    owner = await session.get(User, proposal.owner_id)
    if owner and owner.id != user.id:
        await push(
            session,
            user_id=owner.id,
            kind=NotificationType.APPROVAL_RESULT,
            title="Proposal đã được duyệt" if approved else "Proposal bị từ chối",
            body=f"{user.name}: {reason or note or proposal.title}",
            target_url=f"/proposals/{proposal_id}",
            actor_id=user.id,
        )
        await email_if_opted_in(
            session,
            owner,
            NotificationType.APPROVAL_RESULT,
            lambda: resend.send_approval_result(
                owner.email,
                proposal_title=proposal.title,
                approved=approved,
                approver_name=user.name,
                url=f"/proposals/{proposal_id}",
                reason=reason,
            ),
        )

    await audit.record(
        session,
        action="approval.approve" if approved else "approval.reject",
        summary=(
            f"{user.name} duyệt “{proposal.title}”"
            if approved
            else f"{user.name} từ chối “{proposal.title}”"
        ),
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"round": row.round, "reason": reason},
        request=request,
    )
    await session.commit()
    await session.refresh(proposal)
    return proposal_row(proposal)


@router.post("/proposals/{proposal_id}/approve", summary="Duyệt proposal")
async def approve(
    proposal_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    body: Annotated[dict[str, Any] | None, None] = None,
) -> dict[str, Any]:
    note = (body or {}).get("note") if isinstance(body, dict) else None
    return await _decide(session, proposal_id, user, True, note, None, request)


@router.post("/proposals/{proposal_id}/reject", summary="Từ chối proposal — bắt buộc có lý do")
async def reject(
    proposal_id: str,
    body: dict[str, Any],
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    reason = (body or {}).get("reason")
    if not reason or not str(reason).strip():
        raise ValidationError("Cần nêu lý do từ chối.", field="reason")
    return await _decide(session, proposal_id, user, False, None, str(reason).strip(), request)


@router.get("/proposals/{proposal_id}/approvals", summary="Lịch sử duyệt")
async def list_approvals(
    proposal_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(ProposalApproval)
                .where(ProposalApproval.proposal_id == proposal_id)
                .options(selectinload(ProposalApproval.approver))
                .order_by(ProposalApproval.round.desc(), ProposalApproval.created_at)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {"items": [serialize_approval(a) for a in rows], "total": len(rows)}


@router.get("/proposals/{proposal_id}/jobs", summary="Lịch sử job của proposal")
async def list_proposal_jobs(
    proposal_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(Job)
                .where(Job.proposal_id == proposal_id)
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [job_ref(j) for j in rows], "total": len(rows)}


@router.get("/proposals/{proposal_id}/ai/changes", summary="Đề xuất sửa từ AI chưa áp dụng")
async def list_suggested_changes(
    proposal_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(AiSuggestedChange)
                .where(
                    AiSuggestedChange.proposal_id == proposal_id,
                    AiSuggestedChange.applied.is_(False),
                )
                .order_by(AiSuggestedChange.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": c.id,
                "kind": c.kind,
                "label": c.label,
                "slideId": c.slide_id,
                "patch": c.patch,
                "createdAt": c.created_at,
            }
            for c in rows
        ],
        "total": len(rows),
    }
