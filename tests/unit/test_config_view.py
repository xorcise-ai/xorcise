"""config_view: assemble + mask the judge-model config; apply writes .env and clears cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core.config import Settings, get_settings
from xorcise.core.contracts.config import ModelConfigUpdate, TerrainModelConfigUpdate
from xorcise.core.rest.config_view import (
    apply_model_update,
    apply_terrain_model_update,
    build_config_view,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
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
    return tmp_path


@pytest.mark.unit
def test_build_view_masks_key_and_never_leaks_it() -> None:
    view = build_config_view(Settings(model_key="sk-secret-abcd", model_name="gpt-4o-mini"))
    assert view.judge.configured is True
    assert view.judge.model_name == "gpt-4o-mini"
    assert view.judge.key_hint is not None
    # the masked hint must not be the raw key, and the raw key must not appear anywhere
    assert view.judge.key_hint != "sk-secret-abcd"
    assert "sk-secret-abcd" not in json.dumps(view.model_dump())


@pytest.mark.unit
def test_build_view_unconfigured_when_no_key() -> None:
    view = build_config_view(Settings())
    assert view.judge.configured is False
    assert view.judge.key_hint is None


@pytest.mark.unit
def test_apply_update_writes_env_clears_cache_and_configures(tmp_path: Path) -> None:
    view = apply_model_update(
        tmp_path,
        ModelConfigUpdate(key="sk-live-9999", base_url="https://api.example/v1", model_name="m1"),
    )
    assert view.judge.configured is True
    assert view.judge.model_name == "m1"
    assert view.judge.base_url == "https://api.example/v1"
    # persisted to .env and visible through a fresh get_settings()
    assert "XORCISE_MODEL_KEY=sk-live-9999" in (tmp_path / ".env").read_text()
    assert get_settings().model_configured() is True


@pytest.mark.unit
def test_apply_update_empty_key_unconfigures(tmp_path: Path) -> None:
    apply_model_update(tmp_path, ModelConfigUpdate(key="sk-temp"))
    view = apply_model_update(tmp_path, ModelConfigUpdate(key=""))
    assert view.judge.configured is False


@pytest.mark.unit
def test_config_view_reports_terrain_model_defaulting_to_judge() -> None:
    view = build_config_view(Settings(model_key="jk", model_name="judge-m"))
    assert view.terrain.configured is True
    assert view.terrain.uses_judge_default is True
    assert view.terrain.model_name == "judge-m"  # effective name = judge's
    assert view.terrain.key_hint is not None and "jk" not in (view.terrain.key_hint or "")


@pytest.mark.unit
def test_config_view_reports_terrain_override_as_custom() -> None:
    view = build_config_view(
        Settings(model_key="jk", model_name="judge-m", terrain_model_name="cheap-m")
    )
    assert view.terrain.uses_judge_default is False
    assert view.terrain.model_name == "cheap-m"


@pytest.mark.unit
def test_apply_terrain_update_persists_and_clear_reverts_to_judge(tmp_path: Path) -> None:
    apply_model_update(tmp_path, ModelConfigUpdate(key="sk-judge", model_name="judge-m"))
    view = apply_terrain_model_update(tmp_path, TerrainModelConfigUpdate(model_name="cheap-m"))
    assert view.terrain.uses_judge_default is False
    assert view.terrain.model_name == "cheap-m"
    view = apply_terrain_model_update(tmp_path, TerrainModelConfigUpdate(model_name=""))
    assert view.terrain.uses_judge_default is True
    assert view.terrain.model_name == "judge-m"
