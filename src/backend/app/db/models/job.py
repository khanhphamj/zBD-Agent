from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, EnumStr, JSONType, PublicIdMixin, TimestampMixin
from app.db.models.enums import JobEventType, JobKind, JobStatus

if TYPE_CHECKING:
    from app.db.models.user import User


class Job(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "jobs"
    __id_prefix__ = "job"

    kind: Mapped[JobKind] = mapped_column(EnumStr(JobKind, 24), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        EnumStr(JobStatus, 16), nullable=False, default=JobStatus.QUEUED, index=True
    )

    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    export_id: Mapped[str | None] = mapped_column(String(32), index=True)

    stage: Mapped[str | None] = mapped_column(String(32))
    stage_label: Mapped[str | None] = mapped_column(String(255))
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped["User | None"] = relationship(lazy="joined")
    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobEvent.seq"
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)

    __table_args__ = (Index("ix_jobs_kind_status", "kind", "status", "created_at"),)


class JobEvent(Base, PublicIdMixin, TimestampMixin):
    __tablename__ = "job_events"
    __id_prefix__ = "jev"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[JobEventType] = mapped_column(EnumStr(JobEventType, 16), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="job_event_seq"),
        Index("ix_job_events_job_seq", "job_id", "seq"),
    )
