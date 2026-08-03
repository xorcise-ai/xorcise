"""PUT /api/config/catalog — the remote connect/disconnect switch (persists catalog_enabled)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def test_config_ships_the_production_catalog_connected(migrated_home):
    # A fresh install points at the production catalog with the switch on. `connected` is
    # pure config — enabled AND a url — so asserting it costs no network call.
    from xorcise.core.config import REMOTE_CATALOG_URL

    body = _client().get("/api/config").json()
    assert body["catalog"]["connected"] is True
    assert body["catalog"]["url"] == REMOTE_CATALOG_URL


def test_config_reports_disconnected_when_the_endpoint_is_cleared(migrated_home, monkeypatch):
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_CATALOG_URL", "")
    get_settings.cache_clear()
    body = _client().get("/api/config").json()
    assert body["catalog"]["connected"] is False
    assert not body["catalog"]["url"]


def test_toggle_disconnect_then_reconnect(migrated_home, monkeypatch):
    from xorcise.core.config import get_settings

    # The switch is only meaningful against a configured endpoint. `connected` is pure
    # config (enabled AND a url), so this exercises the toggle without any network.
    monkeypatch.setenv("XORCISE_CATALOG_URL", "https://catalog.example.com")
    get_settings.cache_clear()
    c = _client()
    assert c.get("/api/config").json()["catalog"]["connected"] is True
    # Disconnect: the switch off both shows in config and degrades catalog status.
    off = c.put("/api/config/catalog", json={"connected": False})
    assert off.status_code == 200 and off.json()["catalog"]["connected"] is False
    assert c.get("/api/catalog/status").json()["state"] == "disconnected"
    # Reconnect: the switch flips back and the url is still there (disconnect never clears it).
    on = c.put("/api/config/catalog", json={"connected": True})
    assert on.status_code == 200 and on.json()["catalog"]["connected"] is True
    assert on.json()["catalog"]["url"] == "https://catalog.example.com"
