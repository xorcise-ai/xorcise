"""The traces table model (PART-ISLAND; subclasses the kernel Base)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base


class TraceRow(Base):
    __tablename__ = "traces"

    # One row per persisted OTLP export payload, keyed by run_id. run_id is a
    # plain indexed column (no cross-module FK): otel is a part-island that
    # cannot import the runs table (the plain-id pattern).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LogRow(Base):
    __tablename__ = "logs"

    # One row per persisted OTLP *logs* export payload, keyed by run_id — the RAW logs
    # signal (assistant responses, api bodies, the claude_code.events stream), the peer of `traces`.
    # RAW-canonical; run_id is a plain indexed column (the plain-id pattern).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TraceSealRow(Base):
    __tablename__ = "trace_seals"

    # One row per sealed run. run_id is a plain PK (no cross-module FK): otel is a
    # part-island and cannot import the runs table (the plain-id pattern).
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AgentEventRunRow(Base):
    __tablename__ = "agent_event_runs"

    # Run-level header of the normalized AgentEvent projection cache:
    # the (adapter_name, adapter_version, source_max_seq) staleness triple + the run-level
    # RunEventsView metadata (source_agent, fallback, next_since, counts, warnings). DERIVED +
    # rebuildable from `traces`, NEVER canonical. run_id is a plain PK.
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(255))
    adapter_version: Mapped[str] = mapped_column(String(255))
    source_max_seq: Mapped[int] = mapped_column(Integer)
    # max seq of the RAW *logs* store folded into the staleness key so the projection
    # rebuilds when EITHER signal grows; default 0 for pre-logs cache rows.
    log_max_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_agent: Mapped[str] = mapped_column(String(255))
    fallback: Mapped[bool] = mapped_column(Boolean)
    next_since: Mapped[int] = mapped_column(Integer)
    next_log_since: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")
    counts_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AgentEventRow(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        Index("ix_agent_events_run_ord", "run_id", "ord"),
        Index("ix_agent_events_run_seq", "run_id", "raw_seq"),
        Index("ix_agent_events_run_signal_seq", "run_id", "signal", "raw_seq"),
        Index("ix_agent_events_kind", "kind"),
    )

    # One row per normalized AgentEvent, ordered by `ord` within a run.
    # DERIVED + rebuildable from `traces`, NEVER canonical. Every AgentEvent field is a
    # first-class column so events are queryable; only the small variable `data` map stays JSON.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36))
    ord: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(512))
    ts: Mapped[str] = mapped_column(String(64))  # ISO-8601 (exact round-trip; sortable)
    received_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_agent: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    subkind: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text)
    group_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    raw_seq: Mapped[int] = mapped_column(Integer)
    span_id: Mapped[str] = mapped_column(String(255))
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signal: Mapped[str] = mapped_column(String(8), default="trace", server_default="trace")
