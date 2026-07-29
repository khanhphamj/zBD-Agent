from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, SessionDep
from app.core import synonyms
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.pagination import PageParams, page_params, paginate, parse_sort
from app.core.security import now
from app.core.text import clean_website, normalize
from app.core.text import initials as make_initials
from app.db.models.customer import (
    Customer,
    CustomerActivity,
    CustomerFact,
    CustomerHandover,
    CustomerSolution,
    CustomerSource,
)
from app.db.models.enums import (
    ActivityKind,
    CustomerStatus,
    FactVerdict,
    JobKind,
    JobStatus,
    NotificationType,
    Permission,
    ResourceType,
)
from app.db.models.job import Job
from app.db.models.proposal import Proposal
from app.db.models.user import User
from app.schemas.requests import (
    BulkAction,
    CustomerCreate,
    CustomerPatch,
    FactCreate,
    FactPatch,
    HandoverBody,
    ResearchStart,
)
from app.schemas.serializers import (
    activity as serialize_activity,
)
from app.schemas.serializers import (
    customer_detail,
    customer_row,
    job_ref,
)
from app.schemas.serializers import (
    fact as serialize_fact,
)
from app.schemas.serializers import (
    solution as serialize_solution,
)
from app.schemas.serializers import (
    source as serialize_source,
)
from app.services import audit
from app.services.notifications import email_if_opted_in, push
from app.services.queue import enqueue
from app.services.shares import apply_visibility, purge_links, require

router = APIRouter(tags=["customers"])

SORTABLE = {
    "name": Customer.name_norm,
    "updatedAt": Customer.updated_at,
    "createdAt": Customer.created_at,
    "status": Customer.status,
    "industry": Customer.industry,
}


@router.get("/customers", summary="Danh sách khách hàng")
async def list_customers(
    user: CurrentUser,
    session: SessionDep,
    params: Annotated[PageParams, Depends(page_params)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    industry: Annotated[str | None, Query()] = None,
    owner: Annotated[str | None, Query(description="user id, handle, or 'me'")] = None,
    archived: Annotated[bool | None, Query()] = False,
) -> dict[str, Any]:
    stmt = select(Customer).options(selectinload(Customer.owner))
    stmt = apply_visibility(stmt, ResourceType.CUSTOMER, user)

    if archived is not None:
        stmt = stmt.where(Customer.archived.is_(archived))
    if status_filter:
        stmt = stmt.where(Customer.status == status_filter)
    if industry:
        stmt = stmt.where(Customer.industry == industry)
    if owner:
        if owner == "me":
            stmt = stmt.where(Customer.owner_id == user.id)
        else:
            stmt = stmt.where(
                or_(
                    Customer.owner_id == owner,
                    Customer.owner_id.in_(select(User.id).where(User.handle == owner)),
                )
            )
    if params.q:
        clauses = [
            or_(Customer.name_norm.ilike(term), Customer.website_norm.ilike(term))
            for term in synonyms.patterns(params.q)
        ]
        if clauses:
            stmt = stmt.where(or_(*clauses))

    stmt = stmt.order_by(parse_sort(params.sort, SORTABLE, Customer.updated_at.desc()))
    return await paginate(session, stmt, params, customer_row)


@router.post(
    "/customers", status_code=status.HTTP_201_CREATED, summary="Tạo khách hàng + chạy Deep Research"
)
async def create_customer(
    body: CustomerCreate, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    website = clean_website(body.website) if body.website else None
    customer = Customer(
        id=Customer.make_id(),
        name=body.name,
        name_norm=normalize(body.name),
        short_name=body.shortName or make_initials(body.name, 3).upper(),
        website=website,
        website_norm=normalize(website) if website else None,
        industry=body.industry,
        status=CustomerStatus.NEW,
        owner_id=user.id,
        created_by_id=user.id,
    )
    session.add(customer)
    await session.flush()

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer.id,
            actor_id=user.id,
            kind=ActivityKind.CREATED,
            title="Tạo hồ sơ khách hàng",
            body=f"{user.name} tạo hồ sơ {customer.name}.",
        )
    )

    job_id: str | None = None
    if body.startResearch:
        job = Job(
            id=Job.make_id(),
            kind=JobKind.RESEARCH,
            status=JobStatus.QUEUED,
            customer_id=customer.id,
            created_by_id=user.id,
            params={
                "knowledgeBaseIds": body.knowledgeBaseIds,
                "depth": str(body.depth),
            },
        )
        session.add(job)
        await session.flush()
        job_id = job.id

        customer.status = CustomerStatus.RESEARCHING
        customer.last_research_job_id = job.id
        session.add(
            CustomerActivity(
                id=CustomerActivity.make_id(),
                customer_id=customer.id,
                actor_id=user.id,
                kind=ActivityKind.RESEARCH_STARTED,
                title="Bắt đầu Deep Research",
                meta={"jobId": job.id, "depth": str(body.depth)},
            )
        )

    await audit.record(
        session,
        action="customer.create",
        summary=f"{user.name} tạo khách hàng {customer.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer.id,
        resource_label=customer.name,
        request=request,
    )
    await session.commit()

    if job_id:
        await enqueue("run_research", job_id)

    await session.refresh(customer)
    return {"customer": customer_row(customer), "researchJobId": job_id}


@router.get("/customers/{customer_id}", summary="Chi tiết khách hàng — đủ 5 tab")
async def get_customer(customer_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    customer, access = await require(
        session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW
    )

    facts = (
        (
            await session.execute(
                select(CustomerFact)
                .where(CustomerFact.customer_id == customer_id)
                .options(selectinload(CustomerFact.verified_by))
                .order_by(CustomerFact.is_opportunity.desc(), CustomerFact.confidence.desc())
            )
        )
        .scalars()
        .all()
    )
    sources = (
        (
            await session.execute(
                select(CustomerSource)
                .where(CustomerSource.customer_id == customer_id)
                .order_by(CustomerSource.created_at)
            )
        )
        .scalars()
        .all()
    )
    solutions = (
        (
            await session.execute(
                select(CustomerSolution)
                .where(CustomerSolution.customer_id == customer_id)
                .options(selectinload(CustomerSolution.product))
                .order_by(CustomerSolution.fit_score.desc())
            )
        )
        .scalars()
        .all()
    )
    proposals = (
        (
            await session.execute(
                select(Proposal)
                .where(Proposal.customer_id == customer_id, Proposal.archived.is_(False))
                .options(selectinload(Proposal.owner), selectinload(Proposal.customer))
                .order_by(Proposal.updated_at.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    activities = (
        (
            await session.execute(
                select(CustomerActivity)
                .where(CustomerActivity.customer_id == customer_id)
                .options(selectinload(CustomerActivity.actor))
                .order_by(CustomerActivity.created_at.desc())
                .limit(20)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    active_job = await _active_job(session, customer_id)

    return customer_detail(
        customer,
        facts=list(facts),
        sources=list(sources),
        solutions=list(solutions),
        proposals=list(proposals),
        activities=list(activities),
        permission=access.permission,
        active_job=active_job,
    )


async def _active_job(session: SessionDep, customer_id: str) -> Job | None:
    return (
        await session.execute(
            select(Job)
            .where(
                Job.customer_id == customer_id,
                Job.kind == JobKind.RESEARCH,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.patch("/customers/{customer_id}", summary="Cập nhật khách hàng")
async def update_customer(
    customer_id: str,
    body: CustomerPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)

    if body.name is not None:
        customer.name = body.name
        customer.name_norm = normalize(body.name)
    if body.website is not None:
        cleaned = clean_website(body.website)
        customer.website = cleaned
        customer.website_norm = normalize(cleaned)
    if body.status is not None:
        if body.status not in {s.value for s in CustomerStatus}:
            raise ValidationError(f"Trạng thái không hợp lệ: {body.status}", field="status")
        customer.status = CustomerStatus(body.status)
    if body.ownerId is not None and body.ownerId != customer.owner_id:
        raise ValidationError(
            "Dùng POST /customers/{id}/handover để đổi người phụ trách.", field="ownerId"
        )
    for field, value in (
        ("industry", body.industry),
        ("short_name", body.shortName),
        ("notes", body.notes),
        ("profile", body.profile),
        ("logo_url", body.logoUrl),
    ):
        if value is not None:
            setattr(customer, field, value)

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer.id,
            actor_id=user.id,
            kind=ActivityKind.PROFILE_UPDATED,
            title="Cập nhật hồ sơ",
        )
    )
    await audit.record(
        session,
        action="customer.update",
        summary=f"{user.name} cập nhật khách hàng {customer.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer.id,
        resource_label=customer.name,
        request=request,
    )
    await session.commit()
    await session.refresh(customer)
    return customer_row(customer)


@router.delete(
    "/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Xoá khách hàng"
)
async def delete_customer(
    customer_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> None:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    label = customer.name
    proposal_ids = (
        (await session.execute(select(Proposal.id).where(Proposal.customer_id == customer_id)))
        .scalars()
        .all()
    )
    purged = await purge_links(session, ResourceType.CUSTOMER, customer_id)
    for pid in proposal_ids:
        child = await purge_links(session, ResourceType.PROPOSAL, pid)
        for key, count in child.items():
            purged[key] = purged.get(key, 0) + count
    await audit.record(
        session,
        action="customer.delete",
        summary=f"{user.name} xoá khách hàng {label}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=label,
        meta={"purged": purged, "proposals": len(proposal_ids)},
        request=request,
    )
    await session.delete(customer)
    await session.commit()


@router.post("/customers/{customer_id}/archive", summary="Lưu trữ / bỏ lưu trữ")
async def archive_customer(
    customer_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    archived: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    customer.archived = archived
    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer.id,
            actor_id=user.id,
            kind=ActivityKind.ARCHIVED,
            title="Lưu trữ hồ sơ" if archived else "Bỏ lưu trữ hồ sơ",
        )
    )
    await audit.record(
        session,
        action="customer.archive",
        summary=f"{user.name} {'lưu trữ' if archived else 'bỏ lưu trữ'} {customer.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        request=request,
    )
    await session.commit()
    await session.refresh(customer)
    return customer_row(customer)


@router.post("/customers/bulk", summary="Thao tác hàng loạt")
async def bulk_customers(
    body: BulkAction, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    done: list[str] = []
    skipped: list[dict[str, str]] = []

    for customer_id in body.ids:
        try:
            customer, _ = await require(
                session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT
            )
        except (NotFoundError, ForbiddenError) as exc:
            skipped.append({"id": customer_id, "reason": exc.code})
            continue

        if body.action == "delete":
            await session.delete(customer)
        elif body.action == "archive":
            customer.archived = True
        elif body.action == "unarchive":
            customer.archived = False
        else:
            skipped.append({"id": customer_id, "reason": "UNSUPPORTED"})
            continue
        done.append(customer_id)

    await audit.record(
        session,
        action=f"customer.bulk_{body.action}",
        summary=f"{user.name} {body.action} {len(done)} khách hàng",
        actor=user,
        resource_type="customer",
        meta={"ids": done, "skipped": skipped},
        request=request,
    )
    await session.commit()
    return {"action": body.action, "okCount": len(done), "ids": done, "skipped": skipped}


@router.post("/customers/{customer_id}/research", summary="Chạy hoặc chạy lại Deep Research")
async def start_research(
    customer_id: str,
    body: ResearchStart,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, str]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)

    existing = await _active_job(session, customer_id)
    if existing:
        return {"jobId": existing.id}

    job = Job(
        id=Job.make_id(),
        kind=JobKind.RESEARCH,
        status=JobStatus.QUEUED,
        customer_id=customer_id,
        created_by_id=user.id,
        params={"knowledgeBaseIds": body.knowledgeBaseIds, "depth": str(body.depth)},
    )
    session.add(job)
    customer.status = CustomerStatus.RESEARCHING
    customer.last_research_job_id = job.id
    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer_id,
            actor_id=user.id,
            kind=ActivityKind.RESEARCH_STARTED,
            title="Chạy lại Deep Research",
            meta={"jobId": job.id},
        )
    )
    await audit.record(
        session,
        action="research.start",
        summary=f"{user.name} chạy Deep Research cho {customer.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        meta={"jobId": job.id, "depth": str(body.depth)},
        request=request,
    )
    await session.commit()

    await enqueue("run_research", job.id)
    return {"jobId": job.id}


@router.get("/customers/{customer_id}/facts", summary="Dữ kiện đã nghiên cứu")
async def list_facts(customer_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(CustomerFact)
                .where(CustomerFact.customer_id == customer_id)
                .options(selectinload(CustomerFact.verified_by))
                .order_by(CustomerFact.confidence.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [serialize_fact(f) for f in rows], "total": len(rows)}


@router.post(
    "/customers/{customer_id}/facts",
    status_code=status.HTTP_201_CREATED,
    summary="Thêm dữ kiện thủ công",
)
async def create_fact(
    customer_id: str,
    body: FactCreate,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    item = CustomerFact(
        id=CustomerFact.make_id(),
        customer_id=customer_id,
        topic=body.topic,
        summary=body.summary,
        confidence=body.confidence,
        verdict=FactVerdict.OK,
        source_url=body.sourceUrl,
        origin="manual",
        is_opportunity=body.isOpportunity,
        verified_by_id=user.id,
        verified_at=now(),
    )
    session.add(item)
    customer.fact_count += 1
    if body.isOpportunity:
        customer.opportunity_count += 1
    await session.commit()
    await session.refresh(item)
    return serialize_fact(item)


@router.patch("/customers/{customer_id}/facts/{fact_id}", summary="Xác nhận / loại bỏ dữ kiện")
async def update_fact(
    customer_id: str,
    fact_id: str,
    body: FactPatch,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    customer, _ = await require(
        session, user, ResourceType.CUSTOMER, customer_id, Permission.COMMENT
    )
    item = await session.get(CustomerFact, fact_id)
    if item is None or item.customer_id != customer_id:
        raise NotFoundError("Dữ kiện")

    item.verdict = FactVerdict(body.verdict)
    item.verified_by_id = user.id if body.verdict != "ai" else None
    item.verified_at = now() if body.verdict != "ai" else None

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer_id,
            actor_id=user.id,
            kind=ActivityKind.FACT_EDITED,
            title=("Xác minh dữ kiện" if body.verdict == "ok" else "Loại bỏ dữ kiện"),
            body=item.topic,
            meta={"factId": fact_id, "verdict": body.verdict},
        )
    )
    await audit.record(
        session,
        action="fact.verdict",
        summary=f"{user.name} đánh dấu dữ kiện “{item.topic}” là {body.verdict}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        meta={"factId": fact_id, "verdict": body.verdict},
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return serialize_fact(item)


@router.post("/customers/{customer_id}/handover", summary="Bàn giao khách hàng cho BD khác")
async def handover(
    customer_id: str,
    body: HandoverBody,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    from app.db.models.collab import Share

    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    recipient = await session.get(User, body.toUserId)
    if recipient is None:
        raise NotFoundError("Người nhận")
    if recipient.id == customer.owner_id:
        raise ValidationError("Người nhận đã là người phụ trách.", field="toUserId")

    previous_owner_id = customer.owner_id
    customer.owner_id = recipient.id

    session.add(
        CustomerHandover(
            id=CustomerHandover.make_id(),
            customer_id=customer_id,
            from_user_id=previous_owner_id,
            to_user_id=recipient.id,
            keep_permission=Permission(body.keepPermission),
            note=body.note,
        )
    )

    existing = (
        await session.execute(
            select(Share).where(
                Share.resource_type == ResourceType.CUSTOMER,
                Share.resource_id == customer_id,
                Share.user_id == previous_owner_id,
            )
        )
    ).scalar_one_or_none()
    if body.keepPermission == "none":
        if existing:
            await session.delete(existing)
    elif existing:
        existing.permission = Permission(body.keepPermission)
    else:
        session.add(
            Share(
                id=Share.make_id(),
                resource_type=ResourceType.CUSTOMER,
                resource_id=customer_id,
                user_id=previous_owner_id,
                permission=Permission(body.keepPermission),
                created_by_id=user.id,
            )
        )

    session.add(
        CustomerActivity(
            id=CustomerActivity.make_id(),
            customer_id=customer_id,
            actor_id=user.id,
            kind=ActivityKind.HANDOVER,
            title="Bàn giao khách hàng",
            body=f"{user.name} bàn giao cho {recipient.name}.",
            meta={"toUserId": recipient.id, "keepPermission": body.keepPermission},
        )
    )

    await push(
        session,
        user_id=recipient.id,
        kind=NotificationType.HANDOVER,
        title="Bạn được bàn giao khách hàng",
        body=f"{user.name} bàn giao {customer.name} cho bạn.",
        target_url=f"/customers/{customer_id}",
        actor_id=user.id,
        meta={"customerId": customer_id},
    )
    await email_if_opted_in(
        session,
        recipient,
        NotificationType.HANDOVER,
        lambda: __import__(
            "app.integrations.resend_client", fromlist=["resend"]
        ).resend.send_handover(
            recipient.email,
            from_name=user.name,
            customer_name=customer.name,
            note=body.note,
            url=f"/customers/{customer_id}",
        ),
    )

    await audit.record(
        session,
        action="handover.transfer",
        summary=f"{user.name} bàn giao {customer.name} cho {recipient.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        meta={
            "fromUserId": previous_owner_id,
            "toUserId": recipient.id,
            "keepPermission": body.keepPermission,
        },
        request=request,
    )
    await session.commit()
    await session.refresh(customer)
    return customer_row(customer)


@router.get("/customers/{customer_id}/activity", summary="Dòng thời gian hồ sơ")
async def list_activity(
    customer_id: str,
    user: CurrentUser,
    session: SessionDep,
    params: Annotated[PageParams, Depends(page_params)],
) -> dict[str, Any]:
    await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW)
    stmt = (
        select(CustomerActivity)
        .where(CustomerActivity.customer_id == customer_id)
        .options(selectinload(CustomerActivity.actor))
        .order_by(CustomerActivity.created_at.desc())
    )
    return await paginate(session, stmt, params, serialize_activity)


@router.post("/customers/{customer_id}/product-matching", summary="Khớp sản phẩm với nhu cầu")
async def product_matching(
    customer_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, str]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    job = Job(
        id=Job.make_id(),
        kind=JobKind.PRODUCT_MATCHING,
        status=JobStatus.QUEUED,
        customer_id=customer_id,
        created_by_id=user.id,
    )
    session.add(job)
    await audit.record(
        session,
        action="solution.match",
        summary=f"{user.name} chạy khớp sản phẩm cho {customer.name}",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        request=request,
    )
    await session.commit()
    await enqueue("run_product_matching", job.id)
    return {"jobId": job.id}


@router.get("/customers/{customer_id}/solutions", summary="Danh sách phương án đã khớp")
async def list_solutions(
    customer_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(CustomerSolution)
                .where(CustomerSolution.customer_id == customer_id)
                .options(selectinload(CustomerSolution.product))
                .order_by(CustomerSolution.selected.desc(), CustomerSolution.fit_score.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    return {"items": [serialize_solution(s) for s in rows], "total": len(rows)}


@router.post("/customers/{customer_id}/solutions/{solution_id}/select", summary="Chọn phương án")
async def select_solution(
    customer_id: str,
    solution_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    selected: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    customer, _ = await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.EDIT)
    item = await session.get(CustomerSolution, solution_id)
    if item is None or item.customer_id != customer_id:
        raise NotFoundError("Phương án")

    item.selected = selected
    item.selected_at = now() if selected else None
    if selected:
        session.add(
            CustomerActivity(
                id=CustomerActivity.make_id(),
                customer_id=customer_id,
                actor_id=user.id,
                kind=ActivityKind.SOLUTION_SELECTED,
                title="Chọn phương án giải pháp",
                body=item.title,
                meta={"solutionId": solution_id},
            )
        )
    await audit.record(
        session,
        action="solution.select",
        summary=f"{user.name} {'chọn' if selected else 'bỏ chọn'} phương án “{item.title}”",
        actor=user,
        resource_type="customer",
        resource_id=customer_id,
        resource_label=customer.name,
        request=request,
    )
    await session.commit()
    await session.refresh(item)
    return serialize_solution(item)


@router.get("/customers/{customer_id}/sources", summary="Nguồn nghiên cứu")
async def list_sources(customer_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(CustomerSource)
                .where(CustomerSource.customer_id == customer_id)
                .order_by(CustomerSource.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [serialize_source(s) for s in rows], "total": len(rows)}


@router.get("/customers/{customer_id}/jobs", summary="Lịch sử job của khách hàng")
async def list_customer_jobs(
    customer_id: str, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    await require(session, user, ResourceType.CUSTOMER, customer_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(Job)
                .where(Job.customer_id == customer_id)
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [job_ref(j) for j in rows], "total": len(rows)}
