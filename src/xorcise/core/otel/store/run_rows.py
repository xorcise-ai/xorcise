"""Every otel table keyed by run, listed and deleted as one set (the run-delete cascade)."""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import delete, select

from xorcise.core.db import session_scope
from xorcise.core.otel.store.models import (
    AgentEventRow,
    AgentEventRunRow,
    LogRow,
    TraceRow,
    TraceSealRow,
)

_RUN_TABLES = (TraceRow, LogRow, TraceSealRow, AgentEventRunRow, AgentEventRow)


def held_run_ids() -> set[str]:
    """Every run id that has a row in any otel table."""
    with session_scope() as s:
        return {
            run_id for model in _RUN_TABLES for run_id in s.scalars(select(model.run_id).distinct())
        }


def delete_for_runs(run_ids: Collection[str]) -> None:
    """Delete these runs' telemetry, seal and agent events in one transaction."""
    with session_scope() as s:
        for model in _RUN_TABLES:
            s.execute(delete(model).where(model.run_id.in_(run_ids)))
