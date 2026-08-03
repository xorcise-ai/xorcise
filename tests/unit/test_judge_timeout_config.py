import pytest

pytestmark = pytest.mark.unit


def test_settings_default_timeout_is_120(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_MODEL_TIMEOUT_SECONDS", raising=False)
    from xorcise.core import config as cfg

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    assert cfg.get_settings().model_timeout_seconds == 120.0
    cfg.get_settings.cache_clear()


def test_apply_update_persists_timeout_and_view_exposes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config as cfg
    from xorcise.core.contracts.config import ModelConfigUpdate
    from xorcise.core.rest.config_view import apply_model_update

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    view = apply_model_update(
        tmp_path, ModelConfigUpdate(key="k", model_name="m", timeout_seconds=180)
    )
    assert "XORCISE_MODEL_TIMEOUT_SECONDS=180" in (tmp_path / ".env").read_text()
    assert view.judge.timeout_seconds == 180.0
    cfg.get_settings.cache_clear()


def test_build_judge_model_passes_configured_timeout():
    from xorcise.core.config import Settings
    from xorcise.core.orchestration.clients.judge_model import (
        OpenAiCompatibleJudgeModel,
        build_judge_model,
    )

    s = Settings(
        model_key="k", model_base_url="http://h/v1", model_name="m", model_timeout_seconds=200
    )
    judge = build_judge_model(s)
    assert isinstance(judge, OpenAiCompatibleJudgeModel)
    assert judge._timeout == 200.0


def test_timeout_must_be_positive():
    from pydantic import ValidationError

    from xorcise.core.contracts.config import ModelConfigUpdate

    with pytest.raises(ValidationError):
        ModelConfigUpdate(timeout_seconds=0)
