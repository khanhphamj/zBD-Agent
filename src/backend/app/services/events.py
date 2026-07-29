import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.enums import JobEventType
from app.db.models.job import JobEvent

log = get_logger(__name__)


_CLOSE = "__close__"


def job_channel(job_id: str) -> str:
    return f"zbd:job:{job_id}"


def user_channel(user_id: str) -> str:
    return f"zbd:user:{user_id}"


def cancel_key(job_id: str) -> str:
    return f"zbd:cancel:{job_id}"


@dataclass(slots=True)
class Event:
    event: JobEventType | str
    data: dict[str, Any]
    seq: int = 0
    message: str | None = None
    created_at: datetime | None = None

    def to_sse(self) -> dict[str, str]:
        return {
            "event": str(self.event),
            "data": json.dumps(self.data, ensure_ascii=False, default=str),
            "id": str(self.seq),
        }


class EventBus:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self.url, encoding="utf-8", decode_responses=True, health_check_interval=30
            )
        return self._redis

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def health(self) -> bool:
        try:
            client = await self.redis()
            return bool(await client.ping())
        except Exception as exc:
            log.warning("redis_unhealthy", error=str(exc))
            return False

    async def publish(
        self,
        session: AsyncSession,
        job_id: str,
        event: JobEventType | str,
        data: dict[str, Any],
        message: str | None = None,
    ) -> Event:
        seq = (
            int(
                (
                    await session.execute(
                        select(func.coalesce(func.max(JobEvent.seq), 0)).where(
                            JobEvent.job_id == job_id
                        )
                    )
                ).scalar_one()
                or 0
            )
            + 1
        )

        row = JobEvent(
            id=JobEvent.make_id(),
            job_id=job_id,
            seq=seq,
            event=JobEventType(event) if not isinstance(event, JobEventType) else event,
            data=data,
            message=message,
        )
        session.add(row)
        await session.commit()

        payload = Event(event=event, data=data, seq=seq)
        try:
            client = await self.redis()
            await client.publish(
                job_channel(job_id),
                json.dumps(
                    {"event": str(event), "data": data, "seq": seq}, ensure_ascii=False, default=str
                ),
            )
            if str(event) in (JobEventType.DONE, JobEventType.ERROR):
                await client.publish(job_channel(job_id), _CLOSE)
        except Exception as exc:
            log.warning("event_publish_failed", job_id=job_id, error=str(exc))

        return payload

    async def notify_user(self, user_id: str, payload: dict[str, Any]) -> None:
        try:
            client = await self.redis()
            await client.publish(
                user_channel(user_id),
                json.dumps(
                    {"event": "notification", "data": payload}, ensure_ascii=False, default=str
                ),
            )
        except Exception as exc:
            log.warning("notify_publish_failed", user_id=user_id, error=str(exc))

    async def replay(self, session: AsyncSession, job_id: str, after_seq: int = 0) -> list[Event]:
        rows = (
            (
                await session.execute(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.seq > after_seq)
                    .order_by(JobEvent.seq)
                )
            )
            .scalars()
            .all()
        )
        return [
            Event(
                event=r.event,
                data=r.data,
                seq=r.seq,
                message=r.message,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def subscribe(
        self, job_id: str, stop: asyncio.Event | None = None
    ) -> AsyncIterator[Event]:
        client = await self.redis()
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(job_channel(job_id))
        try:
            while not (stop and stop.is_set()):
                message = await pubsub.get_message(timeout=1.0)
                if message is None:
                    continue
                raw = message.get("data")
                if raw == _CLOSE:
                    break
                try:
                    parsed = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                yield Event(
                    event=parsed.get("event", "step"),
                    data=parsed.get("data") or {},
                    seq=int(parsed.get("seq") or 0),
                )
        finally:
            await pubsub.unsubscribe(job_channel(job_id))
            await pubsub.aclose()

    async def request_cancel(self, job_id: str, ttl: int = 3600) -> None:
        client = await self.redis()
        await client.set(cancel_key(job_id), "1", ex=ttl)

    async def is_cancelled(self, job_id: str) -> bool:
        try:
            client = await self.redis()
            return bool(await client.exists(cancel_key(job_id)))
        except Exception:
            return False

    async def clear_cancel(self, job_id: str) -> None:
        try:
            client = await self.redis()
            await client.delete(cancel_key(job_id))
        except Exception:
            pass


bus = EventBus()
