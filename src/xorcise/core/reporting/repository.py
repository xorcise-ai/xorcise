"""Per-agent result history (domain module). Results accrue to a live agent
and are cascade-deleted with it."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from xorcise.core.contracts.grading import GradeResult
from xorcise.core.contracts.reporting import AgentHistoryEntry, ResultConditions, RunStats
from xorcise.core.db import session_scope
from xorcise.core.reporting.models import ResultRow


def _to_entry(row: ResultRow) -> AgentHistoryEntry:
    return AgentHistoryEntry(
        run_id=row.run_id,
        agent_id=row.agent_id,
        overall=row.overall,
        deterministic=row.deterministic,
        judge=row.judge,
        trace_ref=row.trace_ref,
        created_at=row.created_at,
        conditions=ResultConditions(
            model=row.model,
            judge_model=row.judge_model,
            budget_seconds=row.budget_seconds,
            sandbox_ref=row.sandbox_ref,
            agent_version=row.agent_version,
            mission_version=row.mission_version,
        ),
        partial=row.partial,
        partial_trigger=row.partial_trigger,
    )


def record_result(
    run_id: str,
    agent_id: str,
    result: GradeResult,
    conditions: ResultConditions | None = None,
    partial: bool = False,
    partial_trigger: str | None = None,
    stats: RunStats | None = None,
) -> AgentHistoryEntry:
    """Persist a run's result against its agent (denormalized agent_id).

    Idempotent on run_id, first-write-wins: async grading + the budget watchdog can
    both reach a terminal run, so a second record for the same run must not double-write.

    `stats` is an optional per-run telemetry snapshot (RunStats) persisted alongside the grade as
    display/comparison data — never an observed fact, never a grading input.
    """
    if conditions is None:
        conditions = ResultConditions()
    with session_scope() as s:
        existing = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if existing is not None:
            return _to_entry(existing)
        row = ResultRow(
            id=uuid4().hex,
            run_id=run_id,
            agent_id=agent_id,
            overall=result.overall,
            deterministic=result.breakdown.deterministic,
            judge=result.breakdown.judge,
            trace_ref=result.trace_ref,
            detail_json=result.model_dump_json(),
            model=conditions.model,
            judge_model=conditions.judge_model,
            budget_seconds=conditions.budget_seconds,
            sandbox_ref=conditions.sandbox_ref,
            agent_version=conditions.agent_version,
            mission_version=conditions.mission_version,
            partial=partial,
            partial_trigger=partial_trigger,
            stats_json=stats.model_dump_json() if stats is not None else "",
        )
        s.add(row)
        s.flush()
        return _to_entry(row)


def agent_history(agent_id: str) -> list[AgentHistoryEntry]:
    """The agent's recorded results, oldest first."""
    with session_scope() as s:
        rows = s.scalars(
            select(ResultRow).where(ResultRow.agent_id == agent_id).order_by(ResultRow.created_at)
        ).all()
        return [_to_entry(r) for r in rows]


def get_result(run_id: str) -> GradeResult | None:
    """The full recorded result for a run, or None if absent."""
    with session_scope() as s:
        row = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if row is None or not row.detail_json:
            return None
        return GradeResult.model_validate_json(row.detail_json)


def get_stats(run_id: str) -> RunStats | None:
    """The persisted per-run telemetry snapshot, or None if absent/empty."""
    with session_scope() as s:
        row = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if row is None or not row.stats_json:
            return None
        return RunStats.model_validate_json(row.stats_json)


def result_conditions(run_id: str) -> ResultConditions | None:
    """The disclosed conditions for a run's recorded result, or None if absent."""
    with session_scope() as s:
        row = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if row is None:
            return None
        return ResultConditions(
            model=row.model,
            judge_model=row.judge_model,
            budget_seconds=row.budget_seconds,
            sandbox_ref=row.sandbox_ref,
            agent_version=row.agent_version,
            mission_version=row.mission_version,
        )


def result_partial(run_id: str) -> tuple[bool, str | None]:
    """Whether a run's recorded result is partial (timed out), + the trigger.

    Returns (False, None) if no result is recorded for the run.
    """
    with session_scope() as s:
        row = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if row is None:
            return (False, None)
        return (row.partial, row.partial_trigger)


def delete_for_agent(agent_id: str) -> int:
    """Delete all results for an agent (the agent-delete cascade). Returns the count."""
    with session_scope() as s:
        rows = s.scalars(select(ResultRow).where(ResultRow.agent_id == agent_id)).all()
        for r in rows:
            s.delete(r)
        return len(rows)


def delete_result(run_id: str) -> bool:
    """Delete the recorded result for a single run. True if a row was removed.

    Operator-facing per-run delete — the narrow counterpart to the agent-scoped
    delete_for_agent cascade. A no-op returning False when no result was recorded for the run.
    """
    with session_scope() as s:
        row = s.scalar(select(ResultRow).where(ResultRow.run_id == run_id))
        if row is None:
            return False
        s.delete(row)
        return True
