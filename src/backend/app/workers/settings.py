from typing import Any, ClassVar

from app.core.logging import get_logger, setup_logging
from app.services.queue import redis_settings
from app.workers.export import run_export
from app.workers.generate import run_generate
from app.workers.knowledge import index_document, run_kb_sync
from app.workers.matching import run_product_crawl, run_product_matching
from app.workers.research import run_research

log = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    setup_logging()
    from app.services.storage import storage

    try:
        await storage.ensure_buckets()
    except Exception as exc:
        log.warning("worker_bucket_init_failed", error=str(exc))
    log.info("worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.db.session import dispose_engine
    from app.integrations.maas import maas
    from app.integrations.resend_client import resend
    from app.integrations.search import search_client
    from app.services.events import bus

    await bus.aclose()
    await maas.aclose()
    await resend.aclose()
    await search_client.aclose()
    await dispose_engine()
    log.info("worker_stopped")


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        run_research,
        run_generate,
        run_export,
        run_product_matching,
        run_product_crawl,
        run_kb_sync,
        index_document,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()

    max_jobs = 4
    job_timeout = 1800
    keep_result = 3600
    max_tries = 1
    health_check_interval = 60
