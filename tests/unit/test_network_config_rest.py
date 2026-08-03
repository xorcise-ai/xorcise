"""PUT /api/config/network — distributed network addresses; enriched /api/system fields."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def test_config_network_defaults_empty(migrated_home):
    net = _client().get("/api/config").json()["network"]
    assert net == {"headscale_url": None, "advertise_host": None}


def test_put_network_persists_addresses(migrated_home):
    c = _client()
    r = c.put(
        "/api/config/network",
        json={"headscale_url": "https://hs.remote:8080", "advertise_host": "10.0.0.5"},
    )
    assert r.status_code == 200
    net = r.json()["network"]
    assert net["headscale_url"] == "https://hs.remote:8080"
    assert net["advertise_host"] == "10.0.0.5"
    # Persisted: a fresh client (re-reads .env) sees them too.
    assert _client().get("/api/config").json()["network"]["advertise_host"] == "10.0.0.5"


def test_put_network_partial_leaves_other(migrated_home):
    c = _client()
    c.put("/api/config/network", json={"advertise_host": "10.0.0.9"})
    # Only headscale_url provided now; advertise_host (None) is left untouched.
    r = c.put("/api/config/network", json={"headscale_url": "https://hs.x:8080"})
    net = r.json()["network"]
    assert net["advertise_host"] == "10.0.0.9"
    assert net["headscale_url"] == "https://hs.x:8080"


def test_system_reports_home_db_topology_and_locations(migrated_home):
    body = _client().get("/api/system").json()
    assert body["home"]  # resolved XORCISE_HOME
    assert body["db_url"].startswith("sqlite")
    assert body["topology"] == "local"
    rest = next(p for p in body["planes"] if p["name"] == "rest")
    assert ":" in rest["location"]  # host:port
