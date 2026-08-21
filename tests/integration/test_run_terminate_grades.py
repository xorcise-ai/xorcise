"""terminate_run grades the sealed run and records the REAL result.

Verifies that after terminate_run() the recorded GradeResult is a real grade over
the sealed evidence, not the old canned 0.5/0.5 placeholder.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from xorcise.core import reporting, runs
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    Check,
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    RubricCriterion,
)
from xorcise.core.contracts.telemetry import ObservedFact, TraceRecord

pytestmark = pytest.mark.integration


def _install_mission(
    home: Path,
    slug: str,
    *,
    checks: tuple[Check, ...] = (),
    rubric: tuple[RubricCriterion, ...] = (),
) -> None:
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    root = home / "missions" / slug
    root.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
        checks=checks,
        rubric=rubric,
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _seed_run_with_evidence(home: Path) -> str:
    """A registered-agent run + a correct flag submission + a sealed trace + an observed fact.

    Mirrors the seeding pattern from tests/adapters/test_grade_assembly.py so the
    deterministic check (flag equals "XORCISE{x}") passes, yielding deterministic=1.0.
    """
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    flag_check = Check(
        id="flag",
        source="artifacts",
        ref="flag",
        op="equals",
        args={"expected": "XORCISE{x}"},
        weight=1.0,
    )
    _install_mission(
        home,
        "c1",
        checks=(flag_check,),
        rubric=(RubricCriterion(id="r1", text="did it", weight=1.0),),
    )
    run = runs.create_run(agent_id="a1", mission="c1", budget_seconds=600)
    rid = run.run_id

    subs = SqliteSubmissionStore()
    subs.record(rid, "flag", "flag", "XORCISE{x}")

    trace = SqliteTraceStore()
    trace.append(TraceRecord(run_id=rid, seq=1, payload="span-a"))

    SqliteObservedFactsStore().record(
        ObservedFact(run_id=rid, kind="run-control", name="flag-submitted", value="True")
    )
    # NOTE: do NOT pre-seal — terminate_run seals as part of its own flow
    return rid


def _seed_run_with_conditions(home: Path) -> tuple[str, str]:
    """Seed a run whose agent has a disclosed model and sandbox_ref + budget_seconds on the run.

    Returns (run_id, model_name).
    """
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    flag_check = Check(
        id="flag",
        source="artifacts",
        ref="flag",
        op="equals",
        args={"expected": "XORCISE{y}"},
        weight=1.0,
    )
    _install_mission(
        home,
        "c2",
        checks=(flag_check,),
        rubric=(RubricCriterion(id="r2", text="did it", weight=1.0),),
    )
    model_name = "test-agent-model"
    run = runs.create_run(
        agent_id="a1",
        mission="c2",
        budget_seconds=300,
        model=model_name,
        sandbox_ref="xorcise/mission-c2:0",
        agent_version=4,
        install_revision=6,
    )
    rid = run.run_id

    subs = SqliteSubmissionStore()
    subs.record(rid, "flag", "flag", "XORCISE{y}")

    trace = SqliteTraceStore()
    trace.append(TraceRecord(run_id=rid, seq=1, payload="span-b"))

    SqliteObservedFactsStore().record(
        ObservedFact(run_id=rid, kind="run-control", name="flag-submitted", value="True")
    )
    return rid, model_name


def test_terminate_records_conditions_on_result(migrated_home) -> None:
    """terminate_run denormalizes run conditions (model/budget/sandbox/versions) onto the result."""
    from xorcise.core.rest.run_terminate import terminate_run

    rid, model_name = _seed_run_with_conditions(migrated_home)
    terminate_run(rid, "done", datetime.now(UTC))

    rc = reporting.result_conditions(rid)
    assert rc is not None
    assert rc.model == model_name
    assert rc.budget_seconds == 300
    assert rc.sandbox_ref == "xorcise/mission-c2:0"
    # No BYOM key configured in throwaway home → judge_model is None
    assert rc.judge_model is None
    # agent_version + install_revision denormalized onto result
    assert rc.agent_version == 4
    assert rc.install_revision == 6


def test_timeout_terminate_marks_result_partial(migrated_home) -> None:
    """A timeout-triggered terminate_run records partial=True on the result."""
    from xorcise.core.rest.run_terminate import terminate_run

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "timeout", datetime.now(UTC))

    assert reporting.result_partial(rid) == (True, "timeout")


def test_done_terminate_marks_result_not_partial(migrated_home) -> None:
    """A done-triggered terminate_run records partial=False on the result."""
    from xorcise.core.rest.run_terminate import terminate_run

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "done", datetime.now(UTC))

    assert reporting.result_partial(rid) == (False, None)


def test_timeout_run_result_endpoint_surfaces_partial(migrated_home) -> None:
    """End-to-end: after a timeout terminate, GET /runs/{id}/result has partial=True."""
    from fastapi.testclient import TestClient

    from xorcise.core.rest.run_terminate import terminate_run
    from xorcise.core.roles.boot.role_all import build_rest_app

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "timeout", datetime.now(UTC))

    r = TestClient(build_rest_app()).get(f"/api/runs/{rid}/result")
    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is True
    assert body["partial_trigger"] == "timeout"


def test_done_run_result_endpoint_surfaces_not_partial(migrated_home) -> None:
    """End-to-end: after a clean done terminate, GET /runs/{id}/result has partial=False.

    No-false-positive guard at the integration boundary.
    """
    from fastapi.testclient import TestClient

    from xorcise.core.rest.run_terminate import terminate_run
    from xorcise.core.roles.boot.role_all import build_rest_app

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "done", datetime.now(UTC))

    r = TestClient(build_rest_app()).get(f"/api/runs/{rid}/result")
    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is False
    assert body["partial_trigger"] is None


def test_terminate_records_real_grade(migrated_home) -> None:
    """terminate_run should record the REAL grade, not the old canned 0.5/0.5 placeholder."""
    from xorcise.core.rest.run_terminate import terminate_run

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "done", datetime.now(UTC))

    got = reporting.get_result(rid)
    assert got is not None

    # More specific: the flag check passes, so deterministic score must be 1.0
    assert got.breakdown.deterministic == pytest.approx(1.0), (
        f"Expected deterministic=1.0 (flag check passes), got {got.breakdown.deterministic}"
    )
    # No BYOM key configured in throwaway home: judge half degrades cleanly
    assert got.judge_status in {"model-not-configured", "unavailable"}, (
        f"Expected judge degradation, got {got.judge_status!r}"
    )


def test_regrade_heals_a_terminal_run_whose_grade_was_lost(migrated_home) -> None:
    """seal_terminal WITHOUT grade (a server stop between seal and the background grade) leaves a
    run wedged at "grading"; regrade_orphaned_terminal_runs grades it on the next boot."""
    from xorcise.core.rest.run_terminate import regrade_orphaned_terminal_runs, seal_terminal

    rid = _seed_run_with_evidence(migrated_home)
    seal_terminal(rid, "done", datetime.now(UTC))  # terminal, but grade_and_record never ran
    assert reporting.get_result(rid) is None  # wedged at "grading"

    healed = regrade_orphaned_terminal_runs()

    assert healed == 1
    got = reporting.get_result(rid)
    assert got is not None and got.breakdown.deterministic == pytest.approx(1.0)
    # Idempotent: a second sweep finds nothing left to heal.
    assert regrade_orphaned_terminal_runs() == 0


def test_ensure_graded_async_redrives_only_a_terminal_ungraded_run_once(migrated_home) -> None:
    """The read-path self-heal: it schedules a grade for a terminal-ungraded run exactly once,
    and no-ops for a non-terminal run or an already-graded one (de-dup + idempotent)."""
    from xorcise.core.rest.run_terminate import ensure_graded_async, seal_terminal

    scheduled: list[object] = []

    def run_now(fn: object) -> None:  # a synchronous stand-in for BackgroundTasks.add_task
        scheduled.append(fn)
        fn()  # type: ignore[operator]

    rid = _seed_run_with_evidence(migrated_home)
    # Not terminal yet → nothing to re-drive.
    assert ensure_graded_async(rid, run_now) is False
    assert reporting.get_result(rid) is None

    seal_terminal(rid, "done", datetime.now(UTC))
    # Terminal + ungraded → schedules one grade, which records the real result.
    assert ensure_graded_async(rid, run_now) is True
    assert reporting.get_result(rid) is not None
    # Already graded → no second judge call.
    assert ensure_graded_async(rid, run_now) is False
    assert len(scheduled) == 1


def test_concurrent_grade_schedulings_call_the_judge_once(migrated_home, monkeypatch) -> None:
    """An endpoint-scheduled grade_and_record (which never touched the in-flight slot) and a
    poll-driven ensure_graded_async during the drain/judge window must not double-grade: the slot
    is claimed by grade_and_record itself, so EVERY scheduling path is de-duplicated and the
    (paid, BYOM) judge is assembled exactly once per run."""
    import threading

    from xorcise.core.rest import grade_assembly, run_terminate

    rid = _seed_run_with_evidence(migrated_home)
    run_terminate.seal_terminal(rid, "operator", datetime.now(UTC))

    entered = threading.Event()
    release = threading.Event()
    builds = 0
    real_build = grade_assembly.build_eval_judge

    def slow_build():  # freezes the first grade mid-flight, standing in for drain + judge latency
        nonlocal builds
        builds += 1
        entered.set()
        release.wait(timeout=2)
        return real_build()

    monkeypatch.setattr(grade_assembly, "build_eval_judge", slow_build)

    # The terminate endpoint's background task: grade_and_record scheduled directly.
    endpoint_grade = threading.Thread(target=run_terminate.grade_and_record, args=(rid,))
    endpoint_grade.start()
    assert entered.wait(timeout=10)  # the first grade is now mid-flight

    def run_now(fn):  # a synchronous stand-in for BackgroundTasks.add_task
        fn()

    # A client polling /result during that window must NOT re-drive a second grade.
    assert run_terminate.ensure_graded_async(rid, run_now) is False
    release.set()
    endpoint_grade.join(timeout=10)
    assert builds == 1
    assert reporting.get_result(rid) is not None


def test_regrade_endpoint_re_evaluates_a_terminal_run(migrated_home) -> None:
    """POST /runs/{id}/regrade drops the recorded result and re-grades the SEALED evidence with the
    current settings — the re-evaluate feature (no new agent run). The background re-grade runs
    inside the TestClient request, so the real grade is restored over the sealed trace."""
    from fastapi.testclient import TestClient

    from xorcise.core.rest.run_terminate import terminate_run
    from xorcise.core.roles.boot.role_all import build_rest_app

    rid = _seed_run_with_evidence(migrated_home)
    terminate_run(rid, "done", datetime.now(UTC))
    assert reporting.get_result(rid) is not None  # graded once already

    r = TestClient(build_rest_app()).post(f"/api/runs/{rid}/regrade")
    assert r.status_code == 202
    assert r.json()["status"] == "grading"

    # The re-grade dropped the old result and recomputed the real one over the same evidence.
    got = reporting.get_result(rid)
    assert got is not None and got.breakdown.deterministic == pytest.approx(1.0)


def test_regrade_endpoint_rejects_an_active_run(migrated_home) -> None:
    """A non-terminal run has nothing sealed to grade — regrade is 409, not a silent no-op."""
    from fastapi.testclient import TestClient

    from xorcise.core.roles.boot.role_all import build_rest_app

    rid = _seed_run_with_evidence(migrated_home)  # created, never terminated
    client = TestClient(build_rest_app())
    assert client.post(f"/api/runs/{rid}/regrade").status_code == 409
    assert client.post("/api/runs/nope/regrade").status_code == 404
