"""A run moves from `created` to `xorcising` the first time the agent acts (#209).

What counts is something only the agent can do: an admitted OTel export, a run-control call with
the run's key, or fetching its tailnet key. The join script's `/alive` reaper holds a different
token and is not the agent, so it never counts."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.rest import run_start
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit

BEARER = "rk-bearer"


@pytest.fixture(autouse=True)
def _forget_seen(monkeypatch):
    # Run ids are unique per test, but the memo is process-wide: start each test without it.
    monkeypatch.setattr(run_start, "_seen", OrderedDict())


def _make_run() -> str:
    return runs.create_run(
        agent_id="a1", mission="c", run_control_key=BEARER, join_key="tskey-secret"
    ).run_id


def _started(run_id: str) -> tuple[str, str | None, bool]:
    entry = runs.get(run_id)
    assert entry is not None
    return entry.state, entry.start_trigger, entry.started_at is not None


def test_a_new_run_has_not_started(migrated_home):
    rid = _make_run()
    assert _started(rid) == ("created", None, False)
    entry = runs.get(rid)
    assert entry is not None and entry.status.label == "Created"


def test_a_run_control_call_with_the_runs_key_starts_it(migrated_home):
    from xorcise.core.rest.routers.runcontrol import require_run

    rid = _make_run()
    require_run(rid, f"Bearer {BEARER}")
    assert _started(rid) == ("xorcising", "run_control", True)
    entry = runs.get(rid)
    assert entry is not None and entry.status.label == "Xorcising"


def test_fetching_the_tailnet_key_starts_it(migrated_home):
    rid = _make_run()
    r = TestClient(build_rest_app()).get(
        f"/api/runs/{rid}/connect", headers={"Authorization": f"Bearer {BEARER}"}
    )
    assert r.status_code == 200
    assert _started(rid) == ("xorcising", "tailnet_key", True)


def test_a_refused_key_does_not_start_it(migrated_home):
    rid = _make_run()
    r = TestClient(build_rest_app()).get(
        f"/api/runs/{rid}/connect", headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401
    assert _started(rid) == ("created", None, False)


def test_the_join_scripts_reaper_does_not_start_it(migrated_home):
    rid = _make_run()
    r = TestClient(build_rest_app()).get(
        f"/api/runs/{rid}/alive", headers={"Authorization": f"Bearer {runs.watch_token(rid)}"}
    )
    assert r.status_code == 204
    assert _started(rid) == ("created", None, False)


def test_an_admitted_export_starts_it(migrated_home):
    rid = _make_run()
    run_start.telemetry_started(rid)
    assert _started(rid) == ("xorcising", "telemetry", True)


def test_the_first_signal_is_the_one_recorded(migrated_home):
    rid = _make_run()
    first = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
    assert runs.mark_started(rid, "tailnet_key", first) is True
    assert runs.mark_started(rid, "telemetry", datetime.now(UTC)) is False
    entry = runs.get(rid)
    assert entry is not None
    assert (entry.start_trigger, entry.started_at) == ("tailnet_key", first)


def test_a_finished_run_is_never_moved_back(migrated_home):
    rid = _make_run()
    runs.mark_terminal(rid, "deploy_failed", datetime.now(UTC))
    run_start.note_started(rid, "telemetry")
    assert _started(rid) == ("terminal", None, False)


def test_a_run_seen_starting_is_not_written_again(migrated_home, monkeypatch):
    """A run exports every few seconds; only its first signal costs a database write."""
    rid = _make_run()
    calls: list[str] = []
    real = runs.mark_started

    def counting(run_id, trigger, at):
        calls.append(run_id)
        return real(run_id, trigger, at)

    monkeypatch.setattr(runs, "mark_started", counting)
    for _ in range(3):
        run_start.telemetry_started(rid)
    assert calls == [rid]


def test_the_memo_of_runs_seen_starting_is_bounded(migrated_home, monkeypatch):
    """Run ids reach it from OTLP resource attributes, which name any id an exporter likes."""
    monkeypatch.setattr(run_start, "_SEEN_MAX", 3)
    for i in range(5):
        run_start.telemetry_started(f"no-such-run-{i}")
    assert list(run_start._seen) == ["no-such-run-2", "no-such-run-3", "no-such-run-4"]


def test_a_failed_write_is_retried_by_the_next_signal(migrated_home, monkeypatch):
    rid = _make_run()
    real = runs.mark_started
    attempts: list[str] = []

    def locked_once(run_id, trigger, at):
        attempts.append(trigger)
        if len(attempts) == 1:
            raise RuntimeError("database is locked")
        return real(run_id, trigger, at)

    monkeypatch.setattr(runs, "mark_started", locked_once)
    run_start.telemetry_started(rid)  # logged, never raised
    assert _started(rid) == ("created", None, False)
    run_start.telemetry_started(rid)
    assert _started(rid) == ("xorcising", "telemetry", True)
