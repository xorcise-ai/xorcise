"""`xorcise config` group — routes through the server.

set-model PUTs /config/model and show GETs /config, so the running server applies the change
(and clears its own settings cache) instead of the CLI poking ~/.xorcise/.env in its own process.
The .env persistence + key masking are the server's job now (the config_view unit tests +
tests/integration/test_config_live_update.py cover that)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_show_reads_from_server(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    got = []

    class _C:
        def get(self, path):
            got.append(path)
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://xorcise-cluster01.local:8000/v1",
                    "model_name": "Qwen3.6-27B-FP8",
                    "key_hint": "…cdef",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    out = capsys.readouterr().out
    assert got == ["/config"]  # hit the service, not the local file
    assert "Configured" in out
    assert "Qwen3.6-27B-FP8" in out


def test_set_model_puts_to_server(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["path"] = path
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(
        key="sk-x",
        base_url="http://h:8000/v1",
        name="Qwen3.6-27B-FP8",
        timeout=None,
        transcript_max_tokens=None,
        span_max_tokens=None,
        tokenizer=None,
    )
    assert calls["path"] == "/config/model"
    assert calls["json"] == {
        "key": "sk-x",
        "base_url": "http://h:8000/v1",
        "model_name": "Qwen3.6-27B-FP8",
        "timeout_seconds": None,
        "transcript_max_tokens": None,
        "span_max_tokens": None,
        "tokenizer": None,
    }
    assert "configured" in capsys.readouterr().out


def test_set_model_sends_transcript_max_tokens_and_tokenizer(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "transcript_max_tokens": json.get("transcript_max_tokens"),
                    "tokenizer": json.get("tokenizer"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(
        key="k",
        base_url="http://h/v1",
        name="m",
        transcript_max_tokens=8000,
        tokenizer="cl100k_base",
    )
    assert calls["json"]["transcript_max_tokens"] == 8000
    assert calls["json"]["tokenizer"] == "cl100k_base"


def test_show_renders_transcript_max_tokens_and_tokenizer(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "timeout_seconds": 120.0,
                    "transcript_max_tokens": 8000,
                    "tokenizer": "cl100k_base",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    out = capsys.readouterr().out
    assert "8000" in out and "cl100k_base" in out


def test_set_model_sends_span_max_tokens(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "span_max_tokens": json.get("span_max_tokens"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(key="k", base_url="http://h/v1", name="m", span_max_tokens=500)
    assert calls["json"]["span_max_tokens"] == 500


def test_show_renders_span_cap_and_disabled_state(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def __init__(self, span: int) -> None:
            self._span = span

        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "span_max_tokens": self._span,
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C(500))
    cfg_cmd.show()
    assert "500" in capsys.readouterr().out
    # 0 reads as an explicit "disabled", not "0 tokens"
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C(0))
    cfg_cmd.show()
    assert "disabled" in capsys.readouterr().out.lower()


def test_set_model_sends_timeout(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "timeout_seconds": json.get("timeout_seconds"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(key="k", base_url="http://h/v1", name="m", timeout=180.0)
    assert calls["json"]["timeout_seconds"] == 180.0


def test_show_renders_timeout(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "timeout_seconds": 180.0,
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    assert "180" in capsys.readouterr().out
