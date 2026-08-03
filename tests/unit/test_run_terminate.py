from datetime import UTC, datetime, timedelta

import pytest

from xorcise.core import reporting, runs

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def test_terminate_run_seals_records_and_stamps_once(migrated_home) -> None:
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert terminate_run(r.run_id, "done", _now()) == "done"
    assert SqliteSealStore().is_sealed(r.run_id) is True
    assert runs.terminal_state(r.run_id) == (True, "done", _now())
    # result recorded for agent history (one row)
    assert len(reporting.agent_history("a1")) == 1

    # second call is a no-op: trigger unchanged, no second result recorded
    assert terminate_run(r.run_id, "timeout", _now() + timedelta(seconds=1)) == "done"
    assert len(reporting.agent_history("a1")) == 1


def test_seal_terminal_marks_and_seals_without_grading(migrated_home) -> None:
    """Zero-delay mode preserves synchronous sealing while grading remains separate."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert seal_terminal(r.run_id, "done", _now()) == "done"
    assert SqliteSealStore().is_sealed(r.run_id) is True
    assert runs.terminal_state(r.run_id)[0] is True
    assert reporting.get_result(r.run_id) is None  # NOT graded yet

    grade_and_record(r.run_id)
    assert reporting.get_result(r.run_id) is not None
    assert len(reporting.agent_history("a1")) == 1

    grade_and_record(r.run_id)  # idempotent — no second row
    assert len(reporting.agent_history("a1")) == 1


def test_terminal_drain_accepts_configured_wait_before_sealing(migrated_home, monkeypatch) -> None:
    from xorcise.core import config
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest import run_terminate

    monkeypatch.setenv("XORCISE_TELEMETRY_DRAIN_SECONDS", "0.25")
    config.get_settings.cache_clear()
    waits: list[float] = []
    monkeypatch.setattr("xorcise.core.rest.run_terminate.time.sleep", waits.append)
    run = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)

    run_terminate.seal_terminal(run.run_id, "done", _now())

    assert runs.terminal_state(run.run_id)[0] is True
    assert SqliteSealStore().is_sealed(run.run_id) is False
    run_terminate.grade_and_record(run.run_id)
    assert waits == [0.25]
    assert SqliteSealStore().is_sealed(run.run_id) is True
    assert reporting.get_result(run.run_id) is not None


def test_operator_termination_recorded_partial(migrated_home) -> None:
    """An operator (manual) kill must not count against the agent as a genuine result.

    It is still recorded (visible in history) but flagged partial with trigger "operator", the
    same treatment as a budget timeout, so any track-record aggregate can exclude it.
    """
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert terminate_run(r.run_id, "operator", _now()) == "operator"
    assert reporting.result_partial(r.run_id) == (True, "operator")


def test_agent_self_completion_not_partial(migrated_home) -> None:
    """Regression: an agent's own "done" completion stays a full (non-partial) result."""
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert terminate_run(r.run_id, "done", _now()) == "done"
    assert reporting.result_partial(r.run_id) == (False, None)


def test_grade_and_record_noop_on_absent_or_nonterminal(migrated_home) -> None:
    """grade_and_record is a safe no-op for an unknown run or a still-active run."""
    from xorcise.core.rest.run_terminate import grade_and_record

    runs.create_run(agent_id="a1", mission="c", budget_seconds=600, run_id="live")
    baseline = len(reporting.agent_history("a1"))
    grade_and_record("ghost")  # absent → no-op
    grade_and_record("live")  # not terminal → no-op
    assert len(reporting.agent_history("a1")) == baseline


def test_grade_and_record_kicks_terrain_attribution_when_enabled(
    migrated_home, monkeypatch
) -> None:
    """Lifecycle backfill: a run going terminal attributes its terrain mission plane
    independent of any viewer (default on), so the map is complete for runs no one watched."""
    import xorcise.core.rest.terrain_catchup_v2 as tc
    from xorcise.core.rest.run_terminate import terminate_run

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(tc, "maybe_start_catchup_v2", lambda *a, **k: calls.append(a))
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())
    assert calls and calls[0][0] == r.run_id


def test_grade_and_record_skips_terrain_attribution_when_disabled(
    migrated_home, monkeypatch
) -> None:
    """The lifecycle attribution is gated on terrain_auto_attribute so BYOM spend can stay
    strictly view-driven."""
    import xorcise.core.rest.terrain_catchup_v2 as tc
    from xorcise.core import config
    from xorcise.core.rest.run_terminate import terminate_run

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(tc, "maybe_start_catchup_v2", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("XORCISE_TERRAIN_AUTO_ATTRIBUTE", "false")
    config.get_settings.cache_clear()  # rebuild settings from patched env (server-owned config)
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())
    assert calls == []


def test_grade_and_record_persists_stats_snapshot(migrated_home) -> None:
    """A graded run gets a RunStats snapshot persisted beside its result.

    A run with no emitted events still yields a zeroed-but-present snapshot (get_stats non-None).
    """
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())
    assert reporting.get_result(r.run_id) is not None
    assert reporting.get_stats(r.run_id) is not None  # snapshot recorded alongside the grade


def test_stats_fold_failure_never_breaks_finalization(migrated_home, monkeypatch) -> None:
    """Best-effort: a fold exception must not stop the result being recorded."""
    import xorcise.core.otel.run_stats as run_stats
    from xorcise.core.rest.run_terminate import terminate_run

    def _boom(*a: object, **k: object) -> object:
        raise RuntimeError("fold blew up")

    monkeypatch.setattr(run_stats, "fold_run_stats", _boom)
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())  # must not raise
    assert reporting.get_result(r.run_id) is not None  # result still recorded
    assert reporting.get_stats(r.run_id) is None  # stats absent, finalization intact


def test_grading_crash_records_fallback_and_still_tears_down(migrated_home, monkeypatch) -> None:
    """The "grading stuck forever" bug: a grading exception must STILL record a (failed) result —
    /result flips from 202 "grading" to a real verdict — and release the environment. Nothing
    ever re-schedules grade_and_record, so an uncaught exception wedged the run permanently."""
    import xorcise.core.rest.grade_assembly as grade_assembly
    import xorcise.core.rest.run_teardown as run_teardown
    from xorcise.core.rest.run_terminate import terminate_run

    class _BoomJudge:
        def grade(self, request: object) -> object:
            raise KeyError("regex")  # the historical crash shape: unknown op → bare KeyError

    monkeypatch.setattr(grade_assembly, "build_eval_judge", lambda: _BoomJudge())
    torn: list[str] = []
    monkeypatch.setattr(run_teardown, "teardown_run", lambda rid: torn.append(rid))

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())  # must not raise
    res = reporting.get_result(r.run_id)
    assert res is not None
    assert res.overall == 0.0
    assert res.judge_status == "unavailable"
    assert res.judge_detail is not None and "grading failed" in res.judge_detail
    assert torn == [r.run_id]  # environment released despite the crash


def test_record_failure_still_tears_down_environment(migrated_home, monkeypatch) -> None:
    """teardown_run runs in a finally: even a result-store failure must not leak the run's
    environment (container + tailnet nodes)."""
    import xorcise.core.rest.run_teardown as run_teardown
    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    torn: list[str] = []
    monkeypatch.setattr(run_teardown, "teardown_run", lambda rid: torn.append(rid))

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(reporting, "record_result", _boom)
    with pytest.raises(RuntimeError, match="store down"):
        grade_and_record(r.run_id)
    assert torn == [r.run_id]


def test_make_gate_open_when_live_raises_when_terminal(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import MissionOverError, make_gate, terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    gate = make_gate(lambda: _now())
    gate(r.run_id)  # live → no raise
    terminate_run(r.run_id, "done", _now())
    with pytest.raises(MissionOverError):
        gate(r.run_id)


def test_make_gate_budget_backstop_terminates_then_raises(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import MissionOverError, make_gate

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=10)
    entry = runs.get(r.run_id)
    assert entry is not None
    created = entry.created_at
    gate = make_gate(lambda: created + timedelta(seconds=11))  # past deadline
    with pytest.raises(MissionOverError):
        gate(r.run_id)
    assert runs.terminal_state(r.run_id) == (True, "timeout", created + timedelta(seconds=11))


def test_terminate_run_absent_run_returns_empty_no_seal_no_record(migrated_home) -> None:
    """Fix 3: use a real agent+run baseline so the assertion is not vacuously true.

    Previously `reporting.agent_history("ghost")` passed agent_id="ghost" where
    "ghost" was actually a run_id — always empty, never a real coverage assertion.
    Now we register agent "a1", capture the history length before the call, invoke
    terminate_run on a non-existent run, then assert:
      - return value is ''  (absent-run sentinel)
      - the ghost run_id is not sealed
      - no additional result was recorded for the real agent
    """
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.run_terminate import terminate_run

    # Register a real agent+run so agent_history has a meaningful baseline.
    runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    baseline = len(reporting.agent_history("a1"))

    result = terminate_run("ghost-run", "done", _now())
    assert result == ""
    assert SqliteSealStore().is_sealed("ghost-run") is False
    assert len(reporting.agent_history("a1")) == baseline
