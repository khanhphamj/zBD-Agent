from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import now
from app.db.models.enums import JobEventType, JobStatus
from app.db.models.job import Job
from app.services.events import bus

log = get_logger(__name__)


class JobCancelled(Exception):
    pass


async def claim(session: AsyncSession, job_id: str) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        log.warning("job_missing", job=job_id)
        return None
    if job.is_terminal:
        log.info("job_already_terminal", job=job_id, status=str(job.status))
        return None

    job.status = JobStatus.RUNNING
    job.started_at = job.started_at or now()
    job.progress = 0.0
    await session.commit()
    return job


async def check_cancel(session: AsyncSession, job: Job) -> None:
    if job.cancel_requested or await bus.is_cancelled(job.id):
        job.status = JobStatus.CANCELLED
        job.finished_at = now()
        await session.commit()
        await bus.publish(
            session,
            job.id,
            JobEventType.ERROR,
            {"code": "CANCELLED", "message": "Đã dừng theo yêu cầu."},
            message="Người dùng dừng tiến trình.",
        )
        await bus.clear_cancel(job.id)
        raise JobCancelled(job.id)


async def step(
    session: AsyncSession,
    job: Job,
    key: str,
    label: str,
    progress: float,
    status: str = "running",
    **extra: Any,
) -> None:
    job.stage = key
    job.stage_label = label
    job.progress = max(0.0, min(1.0, progress))
    await session.commit()

    await bus.publish(
        session,
        job.id,
        JobEventType.STEP,
        {"key": key, "label": label, "status": status, "progress": round(job.progress, 3), **extra},
        message=label,
    )


async def finish(session: AsyncSession, job: Job, result: dict[str, Any]) -> None:
    job.status = JobStatus.DONE
    job.progress = 1.0
    job.result = result
    job.finished_at = now()
    await session.commit()
    await bus.publish(session, job.id, JobEventType.DONE, result, message="Hoàn tất.")
    await bus.clear_cancel(job.id)


async def fail(
    session: AsyncSession,
    job: Job,
    code: str,
    message: str,
    **extra: Any,
) -> None:
    payload = {"code": code, "message": message, **extra}
    job.status = JobStatus.FAILED
    job.error = payload
    job.finished_at = now()
    await session.commit()
    await bus.publish(session, job.id, JobEventType.ERROR, payload, message=message)
    await bus.clear_cancel(job.id)
    log.warning("job_failed", job=job.id, kind=str(job.kind), code=code)
