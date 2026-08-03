from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from xorcise.core import runs

pytestmark = pytest.mark.unit


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def test_teardown_run_calls_control_and_fence(migrated_home, monkeypatch) -> None:
    # releasing a run stops its container (control.teardown) AND removes its tailnet nodes
    # (fence.teardown_run_network), authenticated with the run-create api key.
    from xorcise.core.rest import run_teardown

    torn: dict[str, object] = {}

    class _Fence:
        def teardown_run_network(self, run_id: str) -> None:
            torn["fence"] = run_id

    class _Control:
        def teardown(self, run_id: str, *, credential: str) -> None:
            torn["control"] = (run_id, credential)

    fake = SimpleNamespace(control=_Control(), fence=_Fence(), api_key="k")
    monkeypatch.setattr(run_teardown, "build_run_create_deps", lambda settings: fake)

    run_teardown.teardown_run("rid1")
    assert torn["fence"] == "rid1"
    assert torn["control"] == ("rid1", "k")


def test_teardown_run_swallows_errors(migrated_home, monkeypatch) -> None:
    # Best-effort: a teardown failure must never propagate (grading must not break on it).
    from xorcise.core.rest import run_teardown

    class _Boom:
        def teardown_run_network(self, run_id: str) -> None:
            raise RuntimeError("boom")

    class _Ctl:
        def teardown(self, run_id: str, *, credential: str) -> None:
            raise RuntimeError("boom")

    fake = SimpleNamespace(control=_Ctl(), fence=_Boom(), api_key="k")
    monkeypatch.setattr(run_teardown, "build_run_create_deps", lambda settings: fake)

    run_teardown.teardown_run("rid1")  # must not raise


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
