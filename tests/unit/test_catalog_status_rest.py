"""GET /api/catalog/status reflects catalog reachability (real wiring)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def test_status_disconnected_when_no_endpoint_is_configured(migrated_home, monkeypatch):
    # Clearing the shipped endpoint leaves the library `disconnected` rather than erroring:
    # nothing is configured, so there is nothing to fail against.
    # NOTE the empty override is deliberate. The default is a REAL production endpoint, so
    # a test that exercised it would dial the internet from the unit lane.
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_CATALOG_URL", "")
    get_settings.cache_clear()
    r = _client().get("/api/catalog/status")
    assert r.status_code == 200 and r.json()["state"] == "disconnected"


def test_status_errors_when_the_configured_endpoint_is_unreachable(migrated_home, monkeypatch):
    # A configured-but-unreachable catalog is `error`, distinct from the `disconnected`
    # above: the operator chose an endpoint, so a failure to reach it is worth surfacing.
    # Deliberately offline — reachability is asserted against a mock transport in
    # test_http_catalog_source.py, never by dialing a real host from the unit lane.
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_CATALOG_URL", "https://catalog.invalid")
    get_settings.cache_clear()
    r = _client().get("/api/catalog/status")
    assert r.status_code == 200 and r.json()["state"] == "error"


def test_status_disconnected_when_switch_off(migrated_home, monkeypatch):
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_CATALOG_ENABLED", "false")
    get_settings.cache_clear()
    r = _client().get("/api/catalog/status")
    assert r.status_code == 200 and r.json()["state"] == "disconnected"
