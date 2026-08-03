from pathlib import Path

from xorcise.core.config import _home, get_settings


def test_home_defaults_to_dot_xorcise_when_unset(monkeypatch):
    monkeypatch.delenv("XORCISE_HOME", raising=False)
    assert _home() == Path.home() / ".xorcise"


def _fresh(monkeypatch, home) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(home))
    get_settings.cache_clear()


def test_env_overrides_config_toml(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('role = "control"\n')
    monkeypatch.setenv("XORCISE_ROLE", "runner")
    _fresh(monkeypatch, tmp_path)
    assert get_settings().role == "runner"  # env wins over config.toml


def test_config_toml_used_when_no_env(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('role = "control"\n')
    monkeypatch.delenv("XORCISE_ROLE", raising=False)
    _fresh(monkeypatch, tmp_path)
    assert get_settings().role == "control"


def test_database_url_defaults_to_sqlite_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XORCISE_DATABASE_URL", raising=False)
    _fresh(monkeypatch, tmp_path)
    url = get_settings().database_url
    assert url.startswith("sqlite:///")
    assert str(tmp_path) in url


def test_model_configured_false_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("XORCISE_MODEL_KEY", raising=False)
    _fresh(monkeypatch, tmp_path)
    assert get_settings().model_configured() is False


def test_model_configured_true_with_key(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_MODEL_KEY", "sk-test")
    _fresh(monkeypatch, tmp_path)
    s = get_settings()
    assert s.model_configured() is True
    assert s.model_key == "sk-test"


def test_runner_and_headscale_ports_default_to_constants(monkeypatch, tmp_path):
    for var in ("XORCISE_RUNNER_PORT", "XORCISE_HEADSCALE_PORT"):
        monkeypatch.delenv(var, raising=False)
    _fresh(monkeypatch, tmp_path)
    s = get_settings()
    assert s.runner_port == 8800
    assert s.headscale_port == 8080


def test_endpoint_ports_overridable_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_REST_PORT", "4001")
    monkeypatch.setenv("XORCISE_RUNNER_PORT", "9900")
    _fresh(monkeypatch, tmp_path)
    s = get_settings()
    assert s.rest_port == 4001
    assert s.runner_port == 9900


def test_ui_url_reflects_configured_host_and_rest_port(monkeypatch, tmp_path):
    from xorcise.core.config import ui_url

    monkeypatch.setenv("XORCISE_HOST", "0.0.0.0")
    monkeypatch.setenv("XORCISE_REST_PORT", "4001")
    _fresh(monkeypatch, tmp_path)
    assert ui_url() == "http://0.0.0.0:4001/ui"


def test_judge_transcript_cap_defaults_to_off():
    from xorcise.core.config import Settings

    # Default 0 = disabled: attempt the call and rely on the judge model's own context limit.
    assert Settings().judge_transcript_max_tokens == 0


def test_terrain_transcript_max_tokens_defaults_to_256k():
    from xorcise.core.config import Settings

    assert Settings().terrain_transcript_max_tokens == 256000


def test_terrain_transcript_max_tokens_env_override(monkeypatch):
    from xorcise.core.config import Settings

    monkeypatch.setenv("XORCISE_TERRAIN_TRANSCRIPT_MAX_TOKENS", "2048")
    assert Settings().terrain_transcript_max_tokens == 2048


def test_judge_transcript_max_tokens_env_override(monkeypatch):
    from xorcise.core.config import Settings

    monkeypatch.setenv("XORCISE_JUDGE_TRANSCRIPT_MAX_TOKENS", "1024")
    assert Settings().judge_transcript_max_tokens == 1024


def test_judge_tokenizer_defaults_and_env_override(monkeypatch):
    from xorcise.core.config import Settings

    monkeypatch.delenv("XORCISE_JUDGE_TOKENIZER", raising=False)
    assert Settings().judge_tokenizer == "o200k_base"
    monkeypatch.setenv("XORCISE_JUDGE_TOKENIZER", "cl100k_base")
    assert Settings().judge_tokenizer == "cl100k_base"


def test_telemetry_drain_defaults_and_env_override(monkeypatch):
    from xorcise.core.config import Settings

    monkeypatch.delenv("XORCISE_TELEMETRY_DRAIN_SECONDS", raising=False)
    assert Settings().telemetry_drain_seconds == 5.0
    monkeypatch.setenv("XORCISE_TELEMETRY_DRAIN_SECONDS", "1.5")
    assert Settings().telemetry_drain_seconds == 1.5


def test_deployment_topology_defaults_to_local():
    from xorcise.core.config import Settings

    assert Settings().deployment_topology == "local"


def test_deployment_topology_accepts_distributed():
    from xorcise.core.config import Settings

    assert Settings(deployment_topology="distributed").deployment_topology == "distributed"


def test_deployment_topology_rejects_unknown():
    import pytest
    from pydantic import ValidationError

    from xorcise.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(deployment_topology="cloud")  # type: ignore[arg-type]


def test_terrain_model_falls_back_to_judge_config_per_field():
    from xorcise.core.config import Settings

    s = Settings(
        model_key="jk",
        model_base_url="http://judge",
        model_name="judge-m",
        terrain_model_name="cheap-m",
    )  # only the name is overridden
    key, base_url, name, timeout = s.terrain_model_effective()
    assert key == "jk" and base_url == "http://judge"  # inherited from judge
    assert name == "cheap-m"  # overridden
    assert timeout == s.model_timeout_seconds
    assert s.terrain_model_configured() is True
    assert s.terrain_model_overridden() is True


def test_terrain_model_uses_judge_when_no_override():
    from xorcise.core.config import Settings

    s = Settings(model_key="jk", model_name="judge-m")
    assert s.terrain_model_overridden() is False
    _, _, name, _ = s.terrain_model_effective()
    assert name == "judge-m"


def test_terrain_model_not_configured_when_no_key_anywhere():
    from xorcise.core.config import Settings

    s = Settings()  # no judge key, no terrain key
    assert s.terrain_model_configured() is False
