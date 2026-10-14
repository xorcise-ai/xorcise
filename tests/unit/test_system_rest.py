"""GET /api/system — read-only Reflect view (role / planes / db schema / catalog / remotes)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


@pytest.mark.unit
def test_system_reports_role_planes_db_catalog(migrated_home) -> None:
    r = _client().get("/api/system")
    assert r.status_code == 200
    body = r.json()
    assert body["role"]  # a non-empty role string
    plane_names = {p["name"] for p in body["planes"]}
    assert {"rest", "otlp", "docker"} <= plane_names
    assert body["db_schema"] in {"head", "behind", "fresh", "unknown"}
    assert "state" in body["catalog"]
    assert isinstance(body["remotes"], list)


@pytest.mark.unit
def test_system_reports_the_serving_pid(migrated_home) -> None:
    """`down`/`up`/`db upgrade` find an instance whose pid file is gone by asking its port; the
    pid is what lets them stop it or repair the record instead of orphaning it (#72/#73)."""
    import os

    body = _client().get("/api/system").json()
    assert body["pid"] == os.getpid()  # TestClient serves in-process


@pytest.mark.unit
def test_system_counts_environments_a_teardown_left_up(migrated_home) -> None:
    """#173: only runs whose teardown FAILED are counted. A run whose grade has not finished yet is
    unreleased too, but that is normal and not something for an operator to act on."""
    from datetime import UTC, datetime, timedelta

    from xorcise.core import runs

    t0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    for i, rid in enumerate(("old", "new", "grading")):
        runs.reserve_run(rid, "a1", "c", network_cidr=f"10.200.{i}.0/24", entry_cidrs="")
        runs.finalize_run(rid, budget_seconds=300, prompt="P")
        runs.mark_terminal(rid, "done", t0 + timedelta(minutes=i))
    runs.record_release_failure("new", "container: later error")
    runs.record_release_failure("old", "tailnet: Headscale is not reachable")

    releases = _client().get("/api/system").json()["releases"]
    assert releases["pending"] == 2
    assert releases["oldest_ended_at"].startswith("2026-10-14T12:00:00")
    assert releases["last_error"] == "tailnet: Headscale is not reachable"


@pytest.mark.unit
def test_system_reports_no_backlog_when_every_release_landed(migrated_home) -> None:
    assert _client().get("/api/system").json()["releases"] == {
        "pending": 0,
        "oldest_ended_at": None,
        "last_error": "",
    }
