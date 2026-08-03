"""The judge transcript TOKEN-budget + tokenizer as live-tunable knobs.

Mirrors the judge-timeout knob: the budget + tokenizer are persisted to ~/.xorcise/.env
by apply_model_update and surfaced (read-only) on the ConfigView, so `config set-model
--transcript-max-tokens/--tokenizer` takes effect immediately with no server restart.
"""

import pytest

pytestmark = pytest.mark.unit


def test_settings_default_transcript_cap_is_off(tmp_path, monkeypatch):
    # Default 0 = disabled: attempt the call and rely on the model's own context limit.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_JUDGE_TRANSCRIPT_MAX_TOKENS", raising=False)
    from xorcise.core import config as cfg

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    assert cfg.get_settings().judge_transcript_max_tokens == 0
    cfg.get_settings.cache_clear()


def test_settings_default_span_cap_is_2k_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_JUDGE_SPAN_MAX_TOKENS", raising=False)
    from xorcise.core import config as cfg

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    assert cfg.get_settings().judge_span_max_tokens == 2000
    cfg.get_settings.cache_clear()


def test_span_cap_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setenv("XORCISE_JUDGE_SPAN_MAX_TOKENS", "500")
    from xorcise.core import config as cfg

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    assert cfg.get_settings().judge_span_max_tokens == 500
    cfg.get_settings.cache_clear()


def test_apply_update_persists_span_cap_and_view_exposes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config as cfg
    from xorcise.core.contracts.config import ModelConfigUpdate
    from xorcise.core.rest.config_view import apply_model_update

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    view = apply_model_update(tmp_path, ModelConfigUpdate(key="k", span_max_tokens=500))
    assert "XORCISE_JUDGE_SPAN_MAX_TOKENS=500" in (tmp_path / ".env").read_text()
    assert view.judge.span_max_tokens == 500
    cfg.get_settings.cache_clear()


def test_span_cap_zero_disables_and_persists(tmp_path, monkeypatch):
    # 0 is a valid value (disable the cap) — unlike transcript_max_tokens it is ge=0, not gt=0.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config as cfg
    from xorcise.core.contracts.config import ModelConfigUpdate
    from xorcise.core.rest.config_view import apply_model_update

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    view = apply_model_update(tmp_path, ModelConfigUpdate(key="k", span_max_tokens=0))
    assert "XORCISE_JUDGE_SPAN_MAX_TOKENS=0" in (tmp_path / ".env").read_text()
    assert view.judge.span_max_tokens == 0
    cfg.get_settings.cache_clear()


def test_span_cap_rejects_negative():
    from pydantic import ValidationError

    from xorcise.core.contracts.config import ModelConfigUpdate

    with pytest.raises(ValidationError):
        ModelConfigUpdate(span_max_tokens=-1)


def test_settings_default_tokenizer_is_o200k(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_JUDGE_TOKENIZER", raising=False)
    from xorcise.core import config as cfg

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    assert cfg.get_settings().judge_tokenizer == "o200k_base"
    cfg.get_settings.cache_clear()


def test_apply_update_persists_budget_and_tokenizer_and_view_exposes_them(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config as cfg
    from xorcise.core.contracts.config import ModelConfigUpdate
    from xorcise.core.rest.config_view import apply_model_update

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    view = apply_model_update(
        tmp_path,
        ModelConfigUpdate(
            key="k", model_name="m", transcript_max_tokens=8000, tokenizer="cl100k_base"
        ),
    )
    env = (tmp_path / ".env").read_text()
    assert "XORCISE_JUDGE_TRANSCRIPT_MAX_TOKENS=8000" in env
    assert "XORCISE_JUDGE_TOKENIZER=cl100k_base" in env
    assert view.judge.transcript_max_tokens == 8000
    assert view.judge.tokenizer == "cl100k_base"
    cfg.get_settings.cache_clear()


def test_transcript_cap_allows_zero_to_disable_but_rejects_negative():
    # 0 is now a valid value (disable the pre-flight cap); only negatives are rejected (ge=0).
    from pydantic import ValidationError

    from xorcise.core.contracts.config import ModelConfigUpdate

    assert ModelConfigUpdate(transcript_max_tokens=0).transcript_max_tokens == 0
    with pytest.raises(ValidationError):
        ModelConfigUpdate(transcript_max_tokens=-1)


def test_terrain_apply_persists_transcript_cap_and_view_exposes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config as cfg
    from xorcise.core.contracts.config import TerrainModelConfigUpdate
    from xorcise.core.rest.config_view import apply_terrain_model_update

    cfg.get_settings.cache_clear()
    (tmp_path / ".env").write_text("")
    view = apply_terrain_model_update(
        tmp_path, TerrainModelConfigUpdate(transcript_max_tokens=32000)
    )
    env = (tmp_path / ".env").read_text()
    assert "XORCISE_TERRAIN_TRANSCRIPT_MAX_TOKENS=32000" in env
    assert view.terrain.transcript_max_tokens == 32000
    cfg.get_settings.cache_clear()


def test_terrain_transcript_budget_must_be_positive():
    from pydantic import ValidationError

    from xorcise.core.contracts.config import TerrainModelConfigUpdate

    with pytest.raises(ValidationError):
        TerrainModelConfigUpdate(transcript_max_tokens=0)
