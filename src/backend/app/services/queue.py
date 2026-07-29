from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue(task: str, *args: Any, **kwargs: Any) -> str | None:
    try:
        pool = await get_pool()
        job = await pool.enqueue_job(task, *args, **kwargs)
    except Exception as exc:
        log.error("enqueue_failed", task=task, args=args, error=str(exc))
        return None
    else:
        log.info("enqueued", task=task, job=getattr(job, "job_id", None))
        return getattr(job, "job_id", None)
