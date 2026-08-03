"""run_judge_live_test — a real (minimal) judge-key check reusing the grade-time client.

The judge "configured" flag is only a presence check; this helper actually calls the model so
the Settings UI can report whether the key really works. The live call is stubbed here (via
build_judge_model) so no network is hit.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from xorcise.core.config import Settings
from xorcise.core.eval.judge import JudgeError
from xorcise.core.rest import config_view


class _OkModel:
    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        return "pong"


class _BoomModel:
    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        raise JudgeError("invalid api key")


@pytest.mark.unit
def test_live_test_not_configured_without_key() -> None:
    res = config_view.run_judge_live_test(Settings(model_key=None))
    assert res.ok is False
    assert res.status == "not_configured"


@pytest.mark.unit
def test_live_test_ok_when_model_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_view, "build_judge_model", lambda _s: _OkModel())
    res = config_view.run_judge_live_test(Settings(model_key="k", model_name="gpt-x"))
    assert res.ok is True
    assert res.status == "ok"
    assert res.model_name == "gpt-x"


@pytest.mark.unit
def test_live_test_error_surfaces_provider_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_view, "build_judge_model", lambda _s: _BoomModel())
    res = config_view.run_judge_live_test(Settings(model_key="k", model_name="gpt-x"))
    assert res.ok is False
    assert res.status == "error"
    assert "invalid api key" in (res.message or "")
