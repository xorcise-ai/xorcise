"""The results table model (domain module; subclasses the kernel Base)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base


class ResultRow(Base):
    __tablename__ = "results"

    # run_id and agent_id are plain indexed columns at the ORM layer (no
    # model-level ForeignKey across sibling domain modules). agent_id is
    # denormalized at record time so history is a single-table query and the
    # delete cascade is app-layer.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    overall: Mapped[float] = mapped_column(Float)
    deterministic: Mapped[float] = mapped_column(Float)
    judge: Mapped[float] = mapped_column(Float)
    trace_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail_json: Mapped[str] = mapped_column(
        Text, default="", server_default=""
    )  # full GradeResult JSON
    # Disclosed conditions
    model: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    judge_model: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    budget_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    sandbox_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    agent_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    install_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # Versioning-contract provenance snapshotted into the result's disclosed conditions.
    # The full digest chain lives on the run row (shared run_id); the result carries what a
    # report and a track record need to LABEL the artifact.
    mission_version: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    mission_base_version: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    partial_trigger: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    # Per-run telemetry snapshot (RunStats JSON, XOR run-report). Agent-self-reported display data
    # beside the grade — never an observed fact, never a grading input. Empty ⇒ no snapshot.
    stats_json: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
