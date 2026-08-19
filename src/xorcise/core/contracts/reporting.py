"""Reporting wire DTOs (LEAF). An agent's recorded run result.

Imports nothing internal (stdlib + pydantic only) — the 50/50 breakdown is flattened
to deterministic/judge in AgentHistoryEntry rather than reusing contracts.grading, to
keep that field self-contained per the leaf rule. Callers that need the full explainable
result (GradeResult) should import it directly from xorcise.core.contracts.grading.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ResultConditions(BaseModel):
    """The disclosed conditions a result was produced under."""

    model: str | None = None  # the agent's DISCLOSED model (None ⇒ not disclosed)
    judge_model: str | None = (
        None  # the judge model used for grading (None ⇒ unconfigured/degraded)
    )
    budget_seconds: int = 0
    sandbox_ref: str | None = None  # the mission image the run executed against
    agent_version: int = 1  # monotonic agent version at run creation
    install_revision: int = 1  # the mission's monotonic local install counter at run creation
    # Versioning-contract labels for the artifact the run executed (None ⇒ pre-contract):
    # the creator SemVer, the base SemVer it was fused on, and the executed platform.
    mission_version: str | None = None
    mission_base_version: str | None = None
    platform: str | None = None
    # Disclosure provenance: how many intel this run was disclosed (kind="intel" submissions). The
    # delivery layer counts the run-control submission store and fills this in (no results-table
    # migration — the rows already exist); grading never reads it.
    intel_disclosed: int = 0


class AgentHistoryEntry(BaseModel):
    """One recorded result in an agent's track record (the 50/50 breakdown)."""

    run_id: str
    agent_id: str
    overall: float
    deterministic: float
    judge: float
    trace_ref: str | None = None
    created_at: datetime
    conditions: ResultConditions = ResultConditions()  # disclosed conditions
    partial: bool = False  # True when terminated by timeout
    partial_trigger: str | None = None  # the terminal trigger when partial


class TokenStats(BaseModel):
    """Summed token usage over a run. `total` is COMPUTED (input+output): no harness emits a
    reliable total, and the three harnesses report the parts under different keys."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    reasoning: int = 0
    total: int = 0


class CountStats(BaseModel):
    """Event counts folded from the projection (uniform across harnesses)."""

    model_calls: int = 0  # token-bearing metric events (harness-neutral proxy; NOT "turns")
    tool_calls: int = 0
    findings: int = 0
    errors: int = 0
    events_total: int = 0
    by_kind: dict[str, int] = {}


class TimingStats(BaseModel):
    """Wall-clock + per-event timing. `elapsed_seconds` is from the run row; the rest are sparse."""

    elapsed_seconds: float | None = None
    first_event_ts: datetime | None = None
    last_event_ts: datetime | None = None
    longest_tool_ms: int | None = None


class RunStats(BaseModel):
    """Per-run telemetry snapshot (XOR run-report). Agent-self-reported → display/comparison only,
    persisted beside the grade; NEVER an observed fact, NEVER a grading input."""

    tokens: TokenStats = TokenStats()
    counts: CountStats = CountStats()
    timing: TimingStats = TimingStats()
    cost_estimated_usd: float | None = None  # deferred — no price map today
