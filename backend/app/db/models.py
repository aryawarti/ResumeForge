"""Database schema.

Soft deletes throughout, with one consequence worth stating explicitly:
base resumes and generated versions are independent lifetimes. Deleting a base
resume must never remove or invalidate a generation made from it. That is not
just a foreign-key choice -- each generation stores its own full ``tex_source``
snapshot, so a version stays renderable and downloadable after its parent is
gone. You will get a callback three weeks later and need to know exactly which
version they are holding.

The jobs table is the queue. Render's free tier has no background worker
service, so generation runs in-process against this table with
``FOR UPDATE SKIP LOCKED``. That removes Redis from the stack entirely and, as
a side effect, makes progress replayable: the events are rows, so a client
that reconnects mid-generation can catch up rather than starting blind.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

__all__ = [
    "Base",
    "User",
    "RefreshToken",
    "BaseResume",
    "JobDescription",
    "Generation",
    "GenerationEvent",
    "GenerationGap",
    "Job",
    "utcnow",
    "as_utc",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Coerce a stored timestamp to timezone-aware UTC.

    Postgres round-trips TIMESTAMPTZ with its offset intact; SQLite drops it
    and hands back a naive value. Comparing the two raises, so every read of a
    stored timestamp goes through here rather than assuming the backend.
    """
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _uuid() -> str:
    return str(uuid.uuid4())


# JSONB on Postgres, plain JSON on SQLite so tests run without a server.
JSONType = JSON().with_variant(JSONB(), "postgresql")
UUIDType = String(36).with_variant(PGUUID(as_uuid=False), "postgresql")


class Base(DeclarativeBase):
    pass


class SoftDeleteMixin:
    """Adds ``deleted_at`` plus the helper every query is expected to use."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")

    resumes: Mapped[list["BaseResume"]] = relationship(back_populates="user")
    generations: Mapped[list["Generation"]] = relationship(back_populates="user")


class RefreshToken(Base, TimestampMixin):
    """One row per issued refresh token, so a token can be revoked server-side."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    user_agent: Mapped[str] = mapped_column(String(255), default="")

    @property
    def is_active(self) -> bool:
        expires = as_utc(self.expires_at)
        return self.revoked_at is None and expires is not None and expires > utcnow()


class BaseResume(Base, TimestampMixin, SoftDeleteMixin):
    """An uploaded source resume, validated and parsed at upload time."""

    __tablename__ = "base_resumes"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    tex_source: Mapped[str] = mapped_column(Text)
    template_profile: Mapped[str] = mapped_column(String(40), default="generic")
    parse_tree: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    compiles: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_warnings: Mapped[list[str]] = mapped_column(JSONType, default=list)

    user: Mapped[User] = relationship(back_populates="resumes")

    __table_args__ = (Index("ix_base_resumes_user_live", "user_id", "deleted_at"),)


class JobDescription(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "job_descriptions"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    source_url: Mapped[str] = mapped_column(String(1000), default="")
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class Generation(Base, TimestampMixin, SoftDeleteMixin):
    """One tailoring run. Retained, never overwritten by a regeneration."""

    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Deliberately nullable with ON DELETE SET NULL: a hard-deleted base resume
    # must not cascade into the generations made from it.
    base_resume_id: Mapped[str | None] = mapped_column(
        UUIDType, ForeignKey("base_resumes.id", ondelete="SET NULL"), nullable=True
    )
    job_description_id: Mapped[str | None] = mapped_column(
        UUIDType, ForeignKey("job_descriptions.id", ondelete="SET NULL"), nullable=True
    )

    # Denormalised so a generation stays meaningful after its parents are gone.
    base_resume_name: Mapped[str] = mapped_column(String(160), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    version_no: Mapped[int] = mapped_column(Integer, default=1)

    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    error: Mapped[str] = mapped_column(Text, default="")

    # The full output snapshot -- this is what makes a generation independent.
    tex_source: Mapped[str] = mapped_column(Text, default="")
    pdf_key: Mapped[str] = mapped_column(String(400), default="")
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    coverage: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    change_report: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    gap_report: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    coverage_score: Mapped[float] = mapped_column(default=0.0)

    user: Mapped[User] = relationship(back_populates="generations")
    events: Mapped[list["GenerationEvent"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )
    gaps: Mapped[list["GenerationGap"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_generations_user_live", "user_id", "deleted_at"),
        Index("ix_generations_user_company", "user_id", "company", "deleted_at"),
    )


class GenerationEvent(Base):
    """Append-only progress log, replayed to reconnecting SSE clients."""

    __tablename__ = "generation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("generations.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text, default="")
    percent: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    generation: Mapped[Generation] = relationship(back_populates="events")

    __table_args__ = (Index("ix_events_gen_seq", "generation_id", "seq"),)


class GenerationGap(Base):
    """One missing requirement, normalised so the roadmap query is a GROUP BY.

    This is what turns per-application gap lists into the aggregate view:
    after fifteen applications, eleven wanted Kafka and you have none.
    """

    __tablename__ = "generation_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("generations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(UUIDType, index=True)
    skill: Mapped[str] = mapped_column(String(200), index=True)
    normalized_skill: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="required")
    category: Mapped[str] = mapped_column(String(30), default="technology")
    company: Mapped[str] = mapped_column(String(200), default="")

    generation: Mapped[Generation] = relationship(back_populates="gaps")

    __table_args__ = (Index("ix_gaps_user_skill", "user_id", "normalized_skill"),)


class Job(Base, TimestampMixin):
    """The queue. Claimed with FOR UPDATE SKIP LOCKED by the in-process worker."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(UUIDType, primary_key=True, default=_uuid)
    generation_id: Mapped[str] = mapped_column(
        UUIDType, ForeignKey("generations.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[str] = mapped_column(String(80), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (Index("ix_jobs_claimable", "state", "locked_at"),)
