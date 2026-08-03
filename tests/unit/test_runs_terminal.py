from datetime import UTC, datetime, timedelta

import pytest

from xorcise.core import runs

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def test_mark_terminal_first_trigger_wins_and_is_idempotent(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert runs.mark_terminal(r.run_id, "done", _now()) == "done"
    # a later, different trigger does not overwrite the first
    assert runs.mark_terminal(r.run_id, "timeout", _now() + timedelta(seconds=5)) == "done"
    is_term, trigger, completed = runs.terminal_state(r.run_id)
    assert is_term is True and trigger == "done" and completed == _now()


def test_terminal_state_for_live_and_absent_run(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert runs.terminal_state(r.run_id) == (False, None, None)
    assert runs.terminal_state("ghost") == (False, None, None)


def test_is_budget_expired_boundary(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    entry = runs.get(r.run_id)
    assert entry is not None
    created = entry.created_at
    assert runs.is_budget_expired(r.run_id, created + timedelta(seconds=599)) is False
    assert runs.is_budget_expired(r.run_id, created + timedelta(seconds=600)) is True


def test_zero_budget_never_expires(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=0)
    assert runs.is_budget_expired(r.run_id, _now() + timedelta(days=365)) is False


def test_active_runs_with_deadline_excludes_terminal_and_zero_budget(migrated_home) -> None:
    live = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    runs.create_run(agent_id="a1", mission="c", run_id="zero", budget_seconds=0)
    done = runs.create_run(agent_id="a1", mission="c", run_id="done", budget_seconds=600)
    runs.mark_terminal(done.run_id, "done", _now())
    ids = [rid for rid, _ in runs.active_runs_with_deadline()]
    assert live.run_id in ids and "zero" not in ids and "done" not in ids


def test_mark_terminal_absent_run_returns_empty_sentinel(migrated_home) -> None:
    assert runs.mark_terminal("ghost", "done", _now()) == ""
    assert runs.terminal_state("ghost") == (False, None, None)
