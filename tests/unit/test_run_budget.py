from datetime import UTC, datetime

from xorcise.core.config import Settings
from xorcise.core.contracts.run import RunCreate, RunEntry


def test_default_budget_setting():
    assert Settings().default_budget_seconds == 3600


def test_run_create_budget_optional():
    assert RunCreate(agent="a", mission="c").budget_seconds is None
    assert RunCreate(agent="a", mission="c", budget_seconds=120).budget_seconds == 120


def test_run_entry_carries_budget():
    e = RunEntry(
        run_id="r",
        agent_id="a",
        mission="c",
        state="ready",
        created_at=datetime.now(UTC),
        budget_seconds=900,
    )
    assert e.budget_seconds == 900


def test_repository_persists_budget_and_prompt(migrated_home):
    from xorcise.core import runs

    entry = runs.create_run(agent_id="a1", mission="c1", budget_seconds=900, prompt="hello")
    assert entry.budget_seconds == 900
    assert runs.get_prompt(entry.run_id) == "hello"
    assert runs.get_prompt("ghost") is None
