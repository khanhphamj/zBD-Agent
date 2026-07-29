from typing import Any

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models.enums import (
    ExportStage,
    ExportStatus,
    JobKind,
    JobStatus,
    Permission,
    ResourceType,
)
from app.db.models.job import Job
from app.db.models.proposal import ProposalExport
from app.schemas.requests import ExportRequest
from app.schemas.serializers import export as serialize_export
from app.services import audit
from app.services.queue import enqueue
from app.services.shares import require
from app.services.storage import storage

router = APIRouter(tags=["exports"])


@router.post(
    "/proposals/{proposal_id}/export",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Kết xuất PDF",
)
async def create_export(
    proposal_id: str,
    body: ExportRequest,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
) -> dict[str, Any]:
    proposal, _ = await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    if not proposal.slide_count:
        raise ValidationError("Proposal chưa có slide nào để kết xuất.")

    record = ProposalExport(
        id=ProposalExport.make_id(),
        proposal_id=proposal_id,
        format=body.format,
        template_id=body.templateId or proposal.template_id,
        include_appendix=body.includeAppendix,
        status=ExportStatus.PROCESSING,
        stage=ExportStage.PREPARE,
        created_by_id=user.id,
    )
    session.add(record)
    await session.flush()

    job = Job(
        id=Job.make_id(),
        kind=JobKind.EXPORT,
        status=JobStatus.QUEUED,
        proposal_id=proposal_id,
        export_id=record.id,
        created_by_id=user.id,
        params={
            "format": str(body.format),
            "templateId": record.template_id,
            "includeAppendix": body.includeAppendix,
        },
    )
    session.add(job)

    await audit.record(
        session,
        action="export.create",
        summary=f"{user.name} kết xuất “{proposal.title}” sang {body.format.value.upper()}",
        actor=user,
        resource_type="proposal",
        resource_id=proposal_id,
        resource_label=proposal.title,
        meta={"exportId": record.id, "format": str(body.format)},
        request=request,
    )
    await session.commit()

    await enqueue("run_export", job.id)
    return {"exportId": record.id, "status": str(record.status), "jobId": job.id}


@router.get("/exports/{export_id}", summary="Trạng thái kết xuất (poll)")
async def get_export(export_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    record = await session.get(ProposalExport, export_id)
    if record is None:
        raise NotFoundError("Bản kết xuất")
    await require(session, user, ResourceType.PROPOSAL, record.proposal_id, Permission.VIEW)

    download_url: str | None = None
    if record.status == ExportStatus.DONE and record.object_key:
        download_url = await storage.presigned_url(
            settings.bucket_exports, record.object_key, download_name=record.filename
        )
    return serialize_export(record, download_url)


@router.post("/exports/{export_id}/retry", summary="Thử lại — tiếp tục từ bước đang dở")
async def retry_export(
    export_id: str, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    record = await session.get(ProposalExport, export_id)
    if record is None:
        raise NotFoundError("Bản kết xuất")
    proposal, _ = await require(
        session, user, ResourceType.PROPOSAL, record.proposal_id, Permission.VIEW
    )
    if record.status == ExportStatus.DONE:
        raise ValidationError("Bản kết xuất này đã hoàn tất.")

    resume_from = record.stage
    record.status = ExportStatus.PROCESSING
    record.error = None

    job = Job(
        id=Job.make_id(),
        kind=JobKind.EXPORT,
        status=JobStatus.QUEUED,
        proposal_id=record.proposal_id,
        export_id=record.id,
        created_by_id=user.id,
        params={
            "format": str(record.format),
            "templateId": record.template_id,
            "includeAppendix": record.include_appendix,
            "resumeFrom": str(resume_from),
        },
    )
    session.add(job)
    await audit.record(
        session,
        action="export.retry",
        summary=f"{user.name} thử lại kết xuất “{proposal.title}” từ bước {resume_from}",
        actor=user,
        resource_type="proposal",
        resource_id=record.proposal_id,
        resource_label=proposal.title,
        meta={"exportId": record.id, "resumeFrom": str(resume_from)},
        request=request,
    )
    await session.commit()

    await enqueue("run_export", job.id)
    return {"exportId": record.id, "status": str(record.status), "resumeFrom": str(resume_from)}


@router.get("/proposals/{proposal_id}/exports", summary="Lịch sử xuất file")
async def list_exports(proposal_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    await require(session, user, ResourceType.PROPOSAL, proposal_id, Permission.VIEW)
    rows = (
        (
            await session.execute(
                select(ProposalExport)
                .where(ProposalExport.proposal_id == proposal_id)
                .order_by(ProposalExport.created_at.desc())
                .limit(30)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        url = None
        if row.status == ExportStatus.DONE and row.object_key:
            url = await storage.presigned_url(
                settings.bucket_exports, row.object_key, download_name=row.filename
            )
        items.append(serialize_export(row, url))
    return {"items": items, "total": len(items)}
