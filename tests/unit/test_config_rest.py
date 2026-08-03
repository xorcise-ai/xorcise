"""GET /api/config + PUT /api/config/model — the fixed-shape config surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.config import get_settings
from xorcise.core.roles.boot.role_all import build_rest_app


def _client() -> TestClient:
    return TestClient(build_rest_app())


@pytest.fixture(autouse=True)
def _no_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "XORCISE_MODEL_KEY",
        "XORCISE_MODEL_BASE_URL",
        "XORCISE_MODEL_NAME",
        "XORCISE_TERRAIN_MODEL_KEY",
        "XORCISE_TERRAIN_MODEL_BASE_URL",
        "XORCISE_TERRAIN_MODEL_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()


@pytest.mark.unit
def test_get_config_unconfigured_on_fresh_home(migrated_home) -> None:
    r = _client().get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["judge"]["configured"] is False
    assert body["judge"]["key_hint"] is None


@pytest.mark.unit
def test_get_config_never_returns_raw_key(migrated_home) -> None:
    _client().put(
        "/api/config/model", json={"key": "sk-super-secret-1234", "model_name": "gpt-4o-mini"}
    )
    r = _client().get("/api/config")
    assert "sk-super-secret-1234" not in r.text


@pytest.mark.unit
def test_put_model_configures_and_echoes(migrated_home) -> None:
    r = _client().put(
        "/api/config/model",
        json={"key": "sk-live-9999", "base_url": "https://api.example/v1", "model_name": "m1"},
    )
    assert r.status_code == 200
    judge = r.json()["judge"]
    assert judge["configured"] is True
    assert judge["model_name"] == "m1"
    assert judge["base_url"] == "https://api.example/v1"
    assert judge["key_hint"] and judge["key_hint"] != "sk-live-9999"


@pytest.mark.unit
def test_model_test_reports_not_configured_on_fresh_home(migrated_home) -> None:
    r = _client().post("/api/config/model/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "not_configured"


@pytest.mark.unit
def test_terrain_model_test_reports_not_configured_on_fresh_home(migrated_home) -> None:
    r = _client().post("/api/config/terrain-model/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "not_configured"


@pytest.mark.unit
def test_put_then_get_round_trips(migrated_home) -> None:
    _client().put("/api/config/model", json={"key": "sk-round-trip", "model_name": "rt"})
    r = _client().get("/api/config")
    assert r.json()["judge"]["configured"] is True
    assert r.json()["judge"]["model_name"] == "rt"


@pytest.mark.unit
def test_put_terrain_model_persists_and_clear_reverts_to_judge(migrated_home) -> None:
    client = _client()
    client.put("/api/config/model", json={"key": "sk-judge", "model_name": "judge-m"})
    # set a terrain override
    body = client.put("/api/config/terrain-model", json={"model_name": "cheap-m"}).json()
    assert body["terrain"]["uses_judge_default"] is False
    assert body["terrain"]["model_name"] == "cheap-m"
    # clear it (empty string removes the env var) -> back to judge default
    body = client.put("/api/config/terrain-model", json={"model_name": ""}).json()
    assert body["terrain"]["uses_judge_default"] is True
    assert body["terrain"]["model_name"] == "judge-m"
