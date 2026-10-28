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


# --- a line break in any text field is refused, not written (#160) ------------------------------
#
# Values land in ~/.xorcise/.env unquoted, so a line break splits the line: dotenv reads back the
# first fragment and the rest persists as a line the writer can never parse. The CLI refuses a
# multi-line piped key, but this route is also reached by the GUI, scripts and `--key` on argv.


def _text_fields() -> list[tuple[str, str]]:
    """(route, field) for every text field of every setter that writes ~/.xorcise/.env."""
    from xorcise.core.contracts.config import (
        ModelConfigUpdate,
        NetworkConfigUpdate,
        TerrainModelConfigUpdate,
    )

    cases = []
    for route, model in (
        ("/api/config/model", ModelConfigUpdate),
        ("/api/config/terrain-model", TerrainModelConfigUpdate),
        ("/api/config/network", NetworkConfigUpdate),
    ):
        for field, info in model.model_fields.items():
            if info.annotation == str | None:
                cases.append((route, field))
    return cases


@pytest.mark.unit
# \n and \r, plus separators the writer's own splitlines() splits on that a search for those two
# misses — the gap the first version of this fix had.
@pytest.mark.parametrize("brk", ["\n", "\r", "\r\n", "\v", "\x1e", "\x85", "\u2028", "\u2029"])
@pytest.mark.parametrize(("route", "field"), _text_fields())
def test_a_multi_line_text_field_is_refused_and_nothing_is_written(
    migrated_home, route, field, brk
) -> None:
    env = migrated_home / ".env"
    env.write_text("XORCISE_MODEL_NAME=before\n")

    r = _client().put(route, json={field: f"secret-line1{brk}secret-line2"})

    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str)  # a sentence the CLI and the GUI can show as-is
    assert field in detail and "single line" in detail
    assert "secret-line" not in r.text  # the value may be the API key: never echoed
    assert env.read_text() == "XORCISE_MODEL_NAME=before\n"


@pytest.mark.unit
def test_every_env_writing_setter_has_its_text_fields_covered() -> None:
    """The case list above is derived from the contracts; this pins that it found them all, so a
    change in how fields are typed cannot silently empty it."""
    assert sorted(_text_fields()) == sorted(
        [
            ("/api/config/model", "key"),
            ("/api/config/model", "base_url"),
            ("/api/config/model", "model_name"),
            ("/api/config/model", "tokenizer"),
            ("/api/config/terrain-model", "key"),
            ("/api/config/terrain-model", "base_url"),
            ("/api/config/terrain-model", "model_name"),
            ("/api/config/network", "headscale_url"),
            ("/api/config/network", "advertise_host"),
        ]
    )
