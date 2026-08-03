from __future__ import annotations

from datetime import UTC, datetime

from xorcise.core.contracts.run import RunCreate, RunEntry


def test_run_create_fields():
    c = RunCreate(agent="alpha", mission="c1")
    assert c.agent == "alpha" and c.mission == "c1"


def test_run_entry_round_trips_through_json():
    entry = RunEntry(
        run_id="r1",
        agent_id="a1",
        mission="c1",
        state="created",
        created_at=datetime(2026, 6, 17, tzinfo=UTC),
    )
    restored = RunEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry
