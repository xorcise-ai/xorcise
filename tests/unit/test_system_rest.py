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
