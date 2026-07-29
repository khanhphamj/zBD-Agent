import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request, status
from sse_starlette.sse import EventSourceResponse

from app.api.deps import CurrentUser, LastEventId, SessionDep
from app.core.errors import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.security import now
from app.db.models.enums import JobEventType, JobKind, JobStatus, ResourceType
from app.db.models.job import Job
from app.schemas.serializers import job_ref
from app.services.events import bus
from app.services.shares import resolve

log = get_logger(__name__)
router = APIRouter(tags=["jobs"])


PING_SECONDS = 15


async def _load_job(
    session: SessionDep, job_id: str, user: Any, kind: JobKind | None = None
) -> Job:
    job = await session.get(Job, job_id)
    if job is None or (kind and job.kind != kind):
        raise NotFoundError("Job")

    if job.customer_id:
        access = await resolve(session, user, ResourceType.CUSTOMER, job.customer_id)
    elif job.proposal_id:
        access = await resolve(session, user, ResourceType.PROPOSAL, job.proposal_id)
    elif job.kb_id:
        access = await resolve(session, user, ResourceType.KNOWLEDGE_BASE, job.kb_id)
    else:
        return job

    if not access.allowed:
        raise ForbiddenError("Bạn không có quyền xem tiến trình này.")
    return job


async def _stream(
    session: SessionDep, job: Job, after_seq: int, request: Request
) -> AsyncIterator[dict[str, str]]:
    highest = after_seq
    for event in await bus.replay(session, job.id, after_seq):
        highest = max(highest, event.seq)
        yield event.to_sse()

    if job.is_terminal:
        return

    stop = asyncio.Event()
    queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in bus.subscribe(job.id, stop):
                if event.seq <= highest:
                    continue
                await queue.put(event.to_sse())
                if str(event.event) in (JobEventType.DONE, JobEventType.ERROR):
                    break
        finally:
            await queue.put(None)

    task = asyncio.create_task(pump())
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
            except TimeoutError:
                yield {"event": "ping", "data": "{}"}
                continue
            if item is None:
                break
            yield item
    finally:
        stop.set()
        task.cancel()


def _sse_response(
    session: SessionDep, job: Job, after_seq: int, request: Request
) -> EventSourceResponse:
    return EventSourceResponse(
        _stream(session, job, after_seq, request),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/research-jobs/{job_id}/stream", summary="SSE tiến độ Deep Research")
async def stream_research(
    job_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    after: LastEventId,
) -> EventSourceResponse:
    job = await _load_job(session, job_id, user, JobKind.RESEARCH)
    return _sse_response(session, job, after, request)


@router.get("/generate-jobs/{job_id}/stream", summary="SSE tiến độ Generate Proposal")
async def stream_generate(
    job_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    after: LastEventId,
) -> EventSourceResponse:
    job = await _load_job(session, job_id, user, JobKind.GENERATE)
    return _sse_response(session, job, after, request)


@router.get("/jobs/{job_id}/stream", summary="SSE tiến độ job bất kỳ")
async def stream_any(
    job_id: str,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    after: LastEventId,
) -> EventSourceResponse:
    job = await _load_job(session, job_id, user)
    return _sse_response(session, job, after, request)


@router.get("/jobs/{job_id}", summary="Trạng thái job (polling thay cho SSE)")
async def get_job(job_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    job = await _load_job(session, job_id, user)
    events = await bus.replay(session, job_id, 0)
    return {
        **job_ref(job),
        "events": [
            {
                "seq": e.seq,
                "event": str(e.event),
                "data": e.data,
                "message": e.message,
                "createdAt": e.created_at,
            }
            for e in events
        ],
    }


async def _cancel(session: SessionDep, job: Job, user: Any) -> dict[str, Any]:
    if job.is_terminal:
        return job_ref(job)

    job.cancel_requested = True

    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = now()
    await session.commit()

    await bus.request_cancel(job.id)
    log.info("job_cancel_requested", job=job.id, kind=str(job.kind), by=user.id)
    return job_ref(job)


@router.post("/research-jobs/{job_id}/cancel", summary="Dừng Deep Research")
async def cancel_research(job_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    job = await _load_job(session, job_id, user, JobKind.RESEARCH)
    return await _cancel(session, job, user)


@router.post("/generate-jobs/{job_id}/cancel", summary="Dừng Generate Proposal")
async def cancel_generate(job_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    job = await _load_job(session, job_id, user, JobKind.GENERATE)
    return await _cancel(session, job, user)


@router.post(
    "/jobs/{job_id}/cancel",
    status_code=status.HTTP_200_OK,
    summary="Dừng job bất kỳ",
)
async def cancel_any(job_id: str, user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    job = await _load_job(session, job_id, user)
    return await _cancel(session, job, user)
