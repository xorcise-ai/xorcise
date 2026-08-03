from __future__ import annotations

from datetime import UTC, datetime

from xorcise.core.contracts.reporting import AgentHistoryEntry


def test_agent_history_entry_round_trips_through_json():
    entry = AgentHistoryEntry(
        run_id="r1",
        agent_id="a1",
        overall=0.5,
        deterministic=0.5,
        judge=0.5,
        trace_ref="r1",
        created_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    restored = AgentHistoryEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry
    assert restored.deterministic == 0.5 and restored.judge == 0.5
