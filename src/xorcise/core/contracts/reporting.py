"""Reporting wire DTOs (LEAF). An agent's recorded run result.

Imports nothing internal (stdlib + pydantic only) — the 50/50 breakdown is flattened
to deterministic/judge in AgentHistoryEntry rather than reusing contracts.grading, to
keep that field self-contained per the leaf rule. Callers that need the full explainable
result (GradeResult) should import it directly from xorcise.core.contracts.grading.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    # The grade's judge_status, so the agent page can disclose a score the judge never contributed
    # to, as the CLI leaderboard and the Results views do (#143). None only for a result whose
    # stored grade cannot be read back. The values of contracts.grading.JudgeStatus, spelled out
    # because a contracts module imports nothing internal (tests/unit/test_contracts_leaf.py).
    judge_status: Literal["ok", "partial", "model-not-configured", "unavailable"] | None = None


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
    # The end of the LAST event that reported a duration, i.e. `ts + duration_ms`. Span-backed
    # events carry the producer's START time, so a run whose telemetry is one 60-second span has
    # first == last and looks zero-length without this. Falls back to `last_event_ts` when no
    # event reported a duration — unknown extent is not zero extent.
    last_event_end_ts: datetime | None = None
    longest_tool_ms: int | None = None


class RunStats(BaseModel):
    """Per-run telemetry snapshot (XOR run-report). Agent-self-reported → display/comparison only,
    persisted beside the grade; NEVER an observed fact, NEVER a grading input."""

    tokens: TokenStats = TokenStats()
    counts: CountStats = CountStats()
    timing: TimingStats = TimingStats()
    cost_estimated_usd: float | None = None  # deferred — no price map today
    # The model(s) the HARNESS reported running, in first-seen order — distinct from
    # ResultConditions.model, which is what the operator DECLARED at `agent register --model`.
    # Almost nobody declares one, so that field is null in practice and a result could not be
    # attributed to a model afterwards; the telemetry carried it the whole time. Empty when the
    # run's telemetry never named a model — an honest unknown, never a placeholder name. Still
    # harness-self-reported, so it stays display/comparison provenance like the rest of RunStats.
    # Each name is clipped to otel.run_stats.MODEL_NAME_MAX for display, so two names differing
    # only past that length render alike and this tuple CAN repeat one. They are still counted as
    # two, though: as two entries here, or in `models_truncated` once the cap is reached.
    models: tuple[str, ...] = ()
    # How many DISTINCT further names the fold saw and dropped, so a capped list reads as capped
    # rather than as the whole truth. 0 whenever the run stayed inside the cap. Distinct is decided
    # on the name the harness reported, before the display clip, so two names that read alike in
    # `models` are still counted as two. The count is not a total: the fold can only hold so many
    # distinct names at once (otel.run_stats._MODELS_TRACK_MAX), and it saturates there — past that
    # many, len(models) + models_truncated is the fold's tracking bound, not how many the run named.
    models_truncated: int = 0
    # The event projection this snapshot was folded under — "<adapter_name>@<adapter_version>",
    # e.g. "generic@2+normalizer.3" (otel.run_stats.projection_key). The projection is versioned
    # and rebuilt from RAW whenever a classifier changes; a stored snapshot whose key no longer
    # matches is re-folded on read (rest.report_assembly.current_run_stats) so the report, the
    # Results page and the replay never disagree about the same run. None on snapshots persisted
    # before this field existed — treated as stale.
    projection: str | None = None


# ── run performance views (GET /results/runs, GET /results/summary) ──────────────────────────────

#: How one finished run counts in a performance summary. Decided once, by the server, so the run
#: strip, the agreement plot, the ledger, the dashboard and the CLI cannot count the same run
#: differently.
#: - `scored`: graded with a judge — fully, or partly, in which case `overall` is the lower bound.
#:   A run that TIMED OUT is scored too, at the grade it earned before the budget ran out: the
#:   budget is part of the task, so not finishing within it is the agent's failure and has to show
#:   in its numbers (`RunScore.timed_out` marks it). The only runs in the median.
#: - `no_judge`: graded, but the judge never ran (`unavailable` / `model-not-configured`), so half
#:   the score is unmeasured. Out of the median, disclosed.
#: - `stopped`: an operator ended it. Whatever it scored says nothing about the agent. Out of the
#:   median, disclosed.
#: - `infra_failed`: cut short by an environment failure. Never graded, not the agent's attempt.
#: - `grading`: finished, grade not recorded yet.
ScoreBucket = Literal["scored", "no_judge", "stopped", "infra_failed", "grading"]


class RunScore(BaseModel):
    """One finished run, as the performance views draw it."""

    run_id: str
    agent_id: str
    mission: str
    # The version label the GUI already shows as `v…`: the creator SemVer, else the local install
    # revision. Its checks and rubric can differ from another version's, so the views keep each
    # version apart.
    mission_version: str
    # False when the run was on a version that is no longer the installed one (or, for a mission
    # no longer installed, no longer the newest version it was run on).
    current_version: bool
    created_at: datetime
    completed_at: datetime | None = None
    outcome: Literal["completed", "partial", "infra_failed"]
    terminal_trigger: str | None = None
    bucket: ScoreBucket
    # Ran out of its time budget. Counted in the median at its graded score, and drawn so the
    # reader can see which scores came from runs that did not finish.
    timed_out: bool = False
    deterministic: float | None = None
    judge: float | None = None
    judge_upper: float | None = None  # set only when the judge scored part of its rubric
    # contracts.grading.JudgeStatus, spelled out (a contracts module imports nothing internal).
    judge_status: Literal["ok", "partial", "model-not-configured", "unavailable"] | None = None
    overall: float | None = None
    overall_upper: float | None = None
    judge_model: str | None = None
    # How many intel the run was given (kind="intel" submissions): an assisted score is not an
    # unassisted one, and the dashboard flags an average that blends the two.
    intel_disclosed: int = 0


class ScoreSummary(BaseModel):
    """A group of finished runs (every run, one agent, one mission version, or one agent on one
    mission version), summarised by the rules in `ScoreBucket`."""

    agent_id: str | None = None
    mission: str | None = None
    mission_version: str | None = None
    current_version: bool | None = None
    runs: int = 0
    completed: int = 0  # finished on the agent's own terms
    scored: int = 0  # in the median
    timed_out: int = 0  # of `scored`, ran out of budget (counted at the score they earned)
    partial_judge: int = 0  # of `scored`, judged on part of the rubric (counted at lower bound)
    assisted: int = 0  # of `scored`, given any intel: a median that blends them says so
    no_judge: int = 0
    stopped: int = 0
    infra_failed: int = 0
    grading: int = 0
    median: float | None = None
    q1: float | None = None  # the middle half of `scored`, for a spread
    q3: float | None = None
    best: float | None = None
    # The two halves over the same `scored` runs: the checks over all of them, the judge over the
    # ones it scored in full (a partial judge's score is only a lower bound).
    mean_deterministic: float | None = None
    mean_judge: float | None = None
    # Mean of (judge - deterministic) over `scored` runs whose judge scored the whole rubric:
    # negative when the judge rates the runs below what the checks gave them.
    judge_gap: float | None = None
    judge_models: tuple[str, ...] = ()
    last_run: datetime | None = None
