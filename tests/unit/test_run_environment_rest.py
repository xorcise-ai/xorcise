"""GET /runs/{id}/environment — the live mission-environment state for the run page.

The UI used to infer this from `sandbox_ref` alone, which could only say "Starting" or "Ready" and
was wrong twice: a STATIC mission (no image ⇒ no sandbox_ref) read as a perpetual "Starting", and
an environment that had DIED still read as "Ready".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.rest import run_readiness
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


@pytest.fixture
def client(migrated_home) -> TestClient:
    return TestClient(build_rest_app())


@pytest.fixture(autouse=True)
def _clear_observations():
    run_readiness._OBSERVED.clear()
    yield
    run_readiness._OBSERVED.clear()


def _lab(run_id: str = "lab") -> None:
    runs.reserve_run(run_id, "ag", "c", network_cidr="10.200.1.0/24", entry_cidrs="10.200.1.0/24")
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")


def _static(run_id: str = "stat") -> None:
    runs.reserve_run(run_id, "ag", "c", network_cidr="", entry_cidrs="")
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")


def test_static_run_reports_no_environment_not_a_perpetual_starting(client):
    # The reported symptom: a static mission has no environment BY DESIGN, so reporting it as
    # forever-starting told the operator to wait for something that is never coming.
    _static()
    body = client.get("/api/runs/stat/environment").json()
    assert body["state"] == "none"
    assert body["ready"] is False
    assert "static" in body["detail"].lower()


def test_unscanned_run_reports_starting(client):
    _lab()
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "starting"
    assert body["ready"] is False


def test_reports_the_gates_observation_once_it_has_scanned(client):
    _lab()
    run_readiness._OBSERVED["lab"] = ("ready", "")
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "ready"
    assert body["ready"] is True


def test_reports_a_dead_environment_as_failed(client):
    # Previously this still read as "Ready" — the operator had no signal at all.
    _lab()
    run_readiness._OBSERVED["lab"] = ("failed", "the mission environment exited")
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "failed"
    assert body["ready"] is False
    assert body["detail"]


def test_terminal_run_reports_released_regardless_of_the_last_observation(client):
    _lab()
    run_readiness._OBSERVED["lab"] = ("ready", "")
    runs.mark_terminal("lab", "done", datetime.now(UTC))
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "released"
    assert body["ready"] is False


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/ghost/environment").status_code == 404
