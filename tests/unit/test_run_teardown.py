from datetime import UTC, datetime

import pytest

from xorcise.core import runs

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def _lab_run(run_id: str = "rid1") -> str:
    runs.reserve_run(run_id, "a1", "c", network_cidr="10.200.1.0/24", entry_cidrs="10.200.1.0/24")
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")
    runs.mark_terminal(run_id, "done", _now())
    return run_id


class _Fence:
    def __init__(self, torn: dict[str, object], fail: bool = False) -> None:
        self._torn, self._fail = torn, fail

    def teardown_run_network(self, run_id: str) -> None:
        if self._fail:
            raise RuntimeError("tailnet boom")
        self._torn["fence"] = run_id


class _Control:
    def __init__(self, torn: dict[str, object], fail: bool = False) -> None:
        self._torn, self._fail = torn, fail

    def teardown(self, run_id: str, *, credential: str) -> None:
        if self._fail:
            raise RuntimeError("docker boom")
        self._torn["control"] = (run_id, credential)


def _planes(
    monkeypatch,
    torn: dict[str, object],
    *,
    control: str = "ok",
    fence: str = "ok",
) -> None:
    """Wire the two teardown builders. Each plane is "ok", "fail" (the teardown call raises) or
    "down" (building it raises, as the real builders do when Docker or Headscale is unreachable)."""
    from xorcise.core.rest import run_teardown

    def build_control(settings):
        if control == "down":
            raise RuntimeError("Docker daemon is not reachable")
        return _Control(torn, fail=control == "fail")

    def build_fence(settings):
        if fence == "down":
            raise RuntimeError("Headscale control plane 'headscale' is not reachable")
        return _Fence(torn, fail=fence == "fail")

    monkeypatch.setattr(run_teardown, "build_teardown_control", build_control)
    monkeypatch.setattr(run_teardown, "build_teardown_fence", build_fence)


def test_teardown_run_releases_both_halves_and_records_it(migrated_home, monkeypatch) -> None:
    # releasing a run stops its container (control.teardown) AND removes its tailnet nodes
    # (fence.teardown_run_network), and the run then records when that happened.
    from xorcise.core.rest import run_teardown
    from xorcise.core.rest.run_create import CONTROL_API_KEY

    rid = _lab_run()
    torn: dict[str, object] = {}
    _planes(monkeypatch, torn)

    outcome = run_teardown.teardown_run(rid, now_fn=_now)
    assert outcome.released is True
    assert torn == {"fence": rid, "control": (rid, CONTROL_API_KEY)}
    state = runs.release_state(rid)
    assert state is not None
    assert state.released_at == _now()
    assert state.error is None


@pytest.mark.parametrize(
    ("control", "fence", "released_half", "failed_half"),
    [
        # #173: a down Headscale raised before the Docker half was even built, so the container
        # leaked although Docker was fine.
        ("ok", "down", "control", "tailnet"),
        ("ok", "fail", "control", "tailnet"),
        ("down", "ok", "fence", "container"),
        ("fail", "ok", "fence", "container"),
    ],
)
def test_one_plane_failing_never_skips_the_other(
    migrated_home, monkeypatch, control, fence, released_half, failed_half
) -> None:
    from xorcise.core.rest import run_teardown

    rid = _lab_run()
    torn: dict[str, object] = {}
    _planes(monkeypatch, torn, control=control, fence=fence)

    outcome = run_teardown.teardown_run(rid, now_fn=_now)  # must not raise
    assert outcome.released is False
    assert released_half in torn
    assert outcome.error.startswith(f"{failed_half}: ")
    state = runs.release_state(rid)
    assert state is not None
    assert state.released_at is None  # left for the sweeper
    assert state.attempts == 1
    assert state.error == outcome.error


def test_teardown_run_never_raises_when_both_planes_are_down(migrated_home, monkeypatch) -> None:
    # Best-effort: a teardown failure must never propagate (grading must not break on it).
    from xorcise.core.rest import run_teardown

    rid = _lab_run()
    _planes(monkeypatch, {}, control="down", fence="down")
    outcome = run_teardown.teardown_run(rid)
    assert outcome.released is False
    assert "container: " in outcome.error and "tailnet: " in outcome.error


def test_a_retry_that_lands_records_the_release(migrated_home, monkeypatch, caplog) -> None:
    from xorcise.core.rest import run_teardown

    rid = _lab_run()
    _planes(monkeypatch, {}, fence="down")
    with caplog.at_level("DEBUG", logger=run_teardown.__name__):
        run_teardown.teardown_run(rid)
        run_teardown.teardown_run(rid)
        _planes(monkeypatch, {})
        assert run_teardown.teardown_run(rid, now_fn=_now).released is True
    state = runs.release_state(rid)
    assert state is not None
    assert state.released_at == _now()
    assert state.attempts == 2  # kept as history
    assert state.error is None
    levels = [(r.levelname, r.getMessage()) for r in caplog.records]
    # One warning for the first failure, quiet retries, and a line saying it finally landed.
    assert [lvl for lvl, _ in levels] == ["WARNING", "DEBUG", "INFO"]
    assert "after 2 failed attempt(s)" in levels[-1][1]


def test_teardown_run_of_a_missing_run_does_not_raise(migrated_home, monkeypatch) -> None:
    from xorcise.core.rest import run_teardown

    _planes(monkeypatch, {}, fence="down")
    assert run_teardown.teardown_run("ghost").released is False
    _planes(monkeypatch, {})
    assert run_teardown.teardown_run("ghost").released is True


def test_grade_and_record_triggers_teardown(migrated_home, monkeypatch) -> None:
    # reaching terminal (via the single grade_and_record choke point) releases the run.
    from xorcise.core.rest import run_teardown
    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    called: list[str] = []
    monkeypatch.setattr(run_teardown, "teardown_run", lambda run_id: called.append(run_id))

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())
    grade_and_record(r.run_id)
    assert called == [r.run_id]
