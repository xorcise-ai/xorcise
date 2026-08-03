"""GET /api/runs/{id}/stats — the per-run telemetry snapshot endpoint.

Covers the four states: 404 unknown, 409 not-terminal, 202 terminal-but-ungraded, 200 stored
snapshot, and the live-fold fallback for a result recorded without a stored snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import reporting, runs
from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def test_stats_unknown_run_is_404(migrated_home) -> None:
    assert _client().get("/api/runs/ghost/stats").status_code == 404


def test_stats_active_run_is_409(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert _client().get(f"/api/runs/{r.run_id}/stats").status_code == 409


def test_stats_terminal_ungraded_is_202(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import seal_terminal

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())  # sealed, NOT graded
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 202
    assert resp.json()["status"] == "grading"


def test_stats_graded_returns_stored_snapshot(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())  # grades + records + persists the snapshot
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "tokens" in body and "total" in body["tokens"]
    assert "counts" in body and "timing" in body


def test_stats_live_fold_fallback_for_snapshotless_result(migrated_home) -> None:
    """A result recorded before the snapshot existed (empty stats_json) still serves stats via a
    read-only live fold, not a 500."""
    from xorcise.core.rest.run_terminate import seal_terminal

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())
    # Record a result directly WITHOUT a stats snapshot (simulates a pre-migration graded run).
    reporting.record_result(
        r.run_id,
        "a1",
        GradeResult(
            run_id=r.run_id, overall=0.5, breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5)
        ),
    )
    assert reporting.get_stats(r.run_id) is None  # no stored snapshot
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 200  # live-fold fallback
    assert "tokens" in resp.json()
