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


def test_terminal_run_reports_released_once_its_teardown_landed(client):
    _lab()
    run_readiness._OBSERVED["lab"] = ("ready", "")
    runs.mark_terminal("lab", "done", datetime.now(UTC))
    runs.mark_released("lab", datetime.now(UTC))
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "released"
    assert body["ready"] is False


def test_terminal_run_reports_releasing_until_its_teardown_lands(client):
    # #173: every terminal run used to report "released", even one whose teardown had failed and
    # left the container and tailnet nodes up. Until the release lands, it says so.
    _lab()
    run_readiness._OBSERVED["lab"] = ("ready", "")
    runs.mark_terminal("lab", "done", datetime.now(UTC))
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "releasing"
    assert body["ready"] is False
    assert body["release_attempts"] == 0
    assert "grading" in body["detail"]


def test_a_failed_teardown_reports_its_attempts_and_error(client):
    _lab()
    runs.mark_terminal("lab", "done", datetime.now(UTC))
    runs.record_release_failure("lab", "tailnet: Headscale control plane is not reachable")
    runs.record_release_failure("lab", "tailnet: Headscale control plane is not reachable")
    body = client.get("/api/runs/lab/environment").json()
    assert body["state"] == "releasing"
    assert body["release_attempts"] == 2
    assert "failed 2 time(s)" in body["detail"]
    assert "Headscale control plane is not reachable" in body["detail"]


def test_a_finished_static_run_still_reports_no_environment(client):
    # A static run owns nothing to release, so it never reads "releasing".
    _static()
    runs.mark_terminal("stat", "done", datetime.now(UTC))
    assert client.get("/api/runs/stat/environment").json()["state"] == "none"


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/ghost/environment").status_code == 404
