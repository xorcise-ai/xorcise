"""run_terrain_live_test — a real (minimal) reachability check for the terrain attribution model.

Mirrors run_judge_live_test: the "configured" flag is only a presence check, so this helper
actually calls the effective terrain model (the per-field override, else the judge trio) to prove
the key works. The live call is stubbed here (via build_terrain_model) so no network is hit.
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
        raise JudgeError("terrain model unreachable")


@pytest.mark.unit
def test_terrain_live_test_not_configured_without_any_key() -> None:
    # No judge key and no terrain override → nothing to test against.
    res = config_view.run_terrain_live_test(Settings(model_key=None, terrain_model_key=None))
    assert res.ok is False
    assert res.status == "not_configured"


@pytest.mark.unit
def test_terrain_live_test_ok_reports_effective_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_view, "build_terrain_model", lambda _s: _OkModel())
    res = config_view.run_terrain_live_test(
        Settings(terrain_model_key="tk", terrain_model_name="cheap-m")
    )
    assert res.ok is True
    assert res.status == "ok"
    assert res.model_name == "cheap-m"  # the terrain-effective name, not the judge's


@pytest.mark.unit
def test_terrain_live_test_error_surfaces_provider_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_view, "build_terrain_model", lambda _s: _BoomModel())
    res = config_view.run_terrain_live_test(
        Settings(terrain_model_key="tk", terrain_model_name="cheap-m")
    )
    assert res.ok is False
    assert res.status == "error"
    assert "terrain model unreachable" in (res.message or "")


def test_explain_model_failure_leads_with_the_action() -> None:
    """A raw httpx 401 tells a developer what happened, not what to do — lead with the fix."""
    from xorcise.core.rest.config_view import explain_model_failure

    raw = (
        "Client error '401 Unauthorized' for url 'https://api.openai.com/v1/chat/completions' "
        "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401"
    )
    msg = explain_model_failure(raw)
    assert msg.startswith("The provider rejected this API key")
    assert raw in msg  # the original detail is kept for diagnosis

    assert explain_model_failure("read timed out").startswith("The model did not answer in time")
    assert explain_model_failure("Connection refused").startswith("Could not reach the provider")
    # An error we have no advice for passes through untouched rather than being mislabelled.
    assert explain_model_failure("weird provider glitch") == "weird provider glitch"
