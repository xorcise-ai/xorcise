"""The runs table model (domain module; subclasses the kernel Base)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base


class RunRow(Base):
    __tablename__ = "runs"

    # agent_id is a plain indexed column at the ORM layer (not a model-level
    # ForeignKey): runs and agents are sibling modules that cannot import each
    # other, so the agents table is not guaranteed to be in the
    # shared Base.metadata here. The FK is declared in the migration DDL for
    # schema clarity; the app-layer run-create gate is the real guarantee.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    mission: Mapped[str] = mapped_column(String(255))
    # XOR run-naming: the operator's label for the run. Set at create (user-supplied or the lazy
    # `<mission> · <agent> #<n>` default); nullable for rows created before this column.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    state: Mapped[str] = mapped_column(String(32), default="created")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # the time budget the terminal watchdog enforces, + the stored
    # connect prompt so `run prompt <id>` can re-print it.
    budget_seconds: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text, default="")
    # the per-run bearer the REST run-control adapter authenticates against.
    # Minted in rest/run_create.create_run; empty for runs created before this column.
    run_control_key: Mapped[str] = mapped_column(String(64), default="")
    # the per-run tailnet join key, persisted so the run-control /connect endpoint can
    # hand it to the agent instead of the prompt inlining it. Secret — read only via the narrow
    # get_join_key getter, never surfaced on RunEntry (mirrors run_control_key).
    join_key: Mapped[str] = mapped_column(String(255), default="", server_default="")
    # the run's allocated /24 on the mission tailnet, persisted so subnet allocation
    # avoids every LIVE run's subnet across processes (not just this process's in-memory set).
    # Empty for runs created before this column.
    network_cidr: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # the run's carved entry subnets (comma-joined), persisted so the per-run ACL can be
    # rendered authoritatively from the shared DB's non-terminal runs (no process clobbers another).
    # network_cidr is the /24 for allocation; entry_cidrs is the carved set the ACL rule needs (they
    # differ for multi-entry-network missions). Read via active_run_networks(); empty pre-column.
    entry_cidrs: Mapped[str] = mapped_column(Text, default="", server_default="")
    # terminal state machine. trigger ∈ {done, timeout, flag}; first-wins.
    terminal_trigger: Mapped[str | None] = mapped_column(String(16), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # disclosed conditions: captured at run-create time, never mutated.
    model: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None
    )  # disclosed model
    sandbox_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None
    )  # mission image
    # version conditions: monotonic version snapshot at create time.
    agent_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    install_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # Artifact provenance, copied from installed.json at create time (mission-versioning
    # contract §31) and NEVER re-resolved: the local mission may later be updated, and a
    # floating tag must not be able to rewrite what this run actually executed. All nullable —
    # a your_own fuse and any pre-contract install carry none of it.
    mission_version: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )  # creator SemVer, e.g. "1.4.2"
    mission_base_version: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    platform: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )  # what was pulled for THIS machine, e.g. "linux/arm64"
    index_digest: Mapped[str | None] = mapped_column(String(96), nullable=True, default=None)
    platform_digest: Mapped[str | None] = mapped_column(String(96), nullable=True, default=None)
    # the rendering agent's kind, snapshotted at create time — frozen, NOT a live
    # lookup through the agents registry, so a run keeps its adapter after the agent is
    # re-declared (kind change bumps version) or removed (mirrors model/agent_version above).
    source_agent: Mapped[str] = mapped_column(
        String(255), nullable=False, default="generic", server_default="generic"
    )
    # Per-run intel disclosure policy chosen at create time (runcontrol.intel_policy grammar): "all"
    # / "" ⇒ all authored intel, "none" ⇒ none, "h1,h3" ⇒ those ids. Default 'all' keeps runs
    # created before intel-control disclosing every authored intel (back-compat).
    intel_policy: Mapped[str] = mapped_column(
        String(255), nullable=False, default="all", server_default="all"
    )


class TerrainUpdateRow(Base):
    """A persisted v2 terrain update. One row per folded node/group/edge update the v2
    map replays client-side, OR a `target_kind="none"` marker recording a considered-but-no-op
    span (is_an_action=false) so that span is cached (not re-attributed) and counts as considered
    without ever reaching the wire. Derived + rebuildable from traces/agent_events — never
    canonical, never a grading input."""

    __tablename__ = "terrain_updates"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "event_id",
            "target_kind",
            "target_id",
            name="uq_terrain_updates_run_event_target",
        ),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str | None] = mapped_column(String(255), default=None)
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(128))
    new_state: Mapped[str | None] = mapped_column(String(16), default=None)
    discovered: Mapped[bool | None] = mapped_column(Boolean, default=None)
    active: Mapped[bool | None] = mapped_column(Boolean, default=None)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RunObservedFactRow(Base):
    """A run-scoped observed fact — the XORCISE-owned anti-forgery stream.

    Plain-id pattern: run_id is an indexed column, NO cross-module FK (mirror RunSubmissionRow /
    the pattern). Append-only; immutable once a run is terminal (no update/delete path)."""

    __tablename__ = "run_observed_facts"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
