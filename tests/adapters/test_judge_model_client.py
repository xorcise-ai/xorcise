from __future__ import annotations

import json

import httpx
import pytest

from xorcise.core.config import Settings
from xorcise.core.eval.judge import JudgeError
from xorcise.core.orchestration.clients.judge_model import (
    OpenAiCompatibleJudgeModel,
    build_judge_model,
    build_terrain_model,
)


def _client(handler: httpx.MockTransport) -> OpenAiCompatibleJudgeModel:
    m = OpenAiCompatibleJudgeModel(base_url="http://model", api_key="k", model="gpt-x")
    m._http = httpx.Client(transport=handler)  # inject the mock transport (no live network)
    return m


@pytest.mark.adapters
def test_score_sends_system_and_user_roles_and_returns_content():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"c1": 1}'}}]})

    out = _client(httpx.MockTransport(handler)).score(
        [("system", "SYS instructions"), ("user", "USER evidence")]
    )
    assert out == '{"c1": 1}'
    body = seen["body"]
    assert isinstance(body, dict)
    # the trusted instructions and untrusted evidence are sent as distinct roles (injection defense)
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == "SYS instructions"
    assert messages[1]["content"] == "USER evidence"
    # Sampling controls are not part of the common OpenAI-compatible subset: reasoning models
    # such as GPT-5.6 reject temperature=0 and accept only their provider default.
    assert "temperature" not in body


@pytest.mark.adapters
def test_http_error_becomes_judge_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(JudgeError):
        _client(httpx.MockTransport(handler)).score([("system", "sys"), ("user", "user")])


@pytest.mark.adapters
def test_build_judge_model_none_when_not_configured():
    assert build_judge_model(Settings(model_key=None)) is None


@pytest.mark.adapters
def test_build_judge_model_real_when_configured():
    m = build_judge_model(Settings(model_key="k", model_base_url="http://m", model_name="gpt-x"))
    assert isinstance(m, OpenAiCompatibleJudgeModel)


@pytest.mark.adapters
def test_build_terrain_model_none_without_any_key():
    assert build_terrain_model(Settings()) is None


@pytest.mark.adapters
def test_build_terrain_model_uses_effective_config():
    s = Settings(
        model_key="jk",
        model_base_url="http://judge",
        model_name="judge-m",
        terrain_model_name="cheap-m",
    )
    m = build_terrain_model(s)
    assert isinstance(m, OpenAiCompatibleJudgeModel)
    assert m._model == "cheap-m" and m._base_url == "http://judge"  # name overridden, url inherited


@pytest.mark.adapters
def test_judge_error_carries_the_providers_own_reason():
    """A judge failure must say WHY. `raise_for_status()` alone stringifies to
    "Client error '400 Bad Request' for url ..." and never reads the body, so a real
    grading outage reported nothing actionable: the provider had answered
    "Input tokens exceed the configured limit of 272000 tokens", and that sentence —
    the one naming both the fix and the limit — was discarded."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Input tokens exceed the configured limit of 272000 tokens.",
                    "code": "context_length_exceeded",
                }
            },
        )

    with pytest.raises(JudgeError) as exc:
        _client(httpx.MockTransport(handler)).score([("system", "sys"), ("user", "user")])
    detail = str(exc.value)
    assert "272000" in detail
    assert "context_length_exceeded" in detail
    assert "400" in detail


@pytest.mark.adapters
def test_judge_error_falls_back_to_raw_text_when_the_body_is_not_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    with pytest.raises(JudgeError) as exc:
        _client(httpx.MockTransport(handler)).score([("system", "sys"), ("user", "user")])
    assert "upstream unavailable" in str(exc.value)
    assert "503" in str(exc.value)


@pytest.mark.adapters
def test_judge_error_never_persists_a_credential():
    """The provider's error prose can quote the key it rejected — OpenAI answers a bad
    key with "Incorrect API key provided: sk-…" — and this reason is stored on the run
    (JudgeOutcome.detail -> GradeResult.judge_detail) and rendered in the results UI.
    Reading the body to make failures diagnosable must not turn it into a credential sink."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": (
                        "Incorrect API key provided: sk-livesecret1234567890. "
                        "You can find your API key at https://platform.openai.com/account/api-keys"
                    ),
                    "code": "invalid_api_key",
                }
            },
        )

    m = OpenAiCompatibleJudgeModel(
        base_url="http://model", api_key="sk-livesecret1234567890", model="gpt-x"
    )
    m._http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(JudgeError) as exc:
        m.score([("system", "sys"), ("user", "user")])
    detail = str(exc.value)
    assert "sk-livesecret1234567890" not in detail
    assert "livesecret" not in detail
    assert "redacted" in detail.lower()
    # Still diagnosable: the status, the model and the provider's error CODE survive.
    assert "401" in detail and "invalid_api_key" in detail


@pytest.mark.adapters
def test_redaction_leaves_the_useful_reason_intact():
    """The message that actually matters must survive redaction verbatim — it names both
    the cause and the setting to change."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        "Input tokens exceed the configured limit of 272000 tokens. "
                        "Your messages resulted in 368991 tokens."
                    ),
                    "code": "context_length_exceeded",
                }
            },
        )

    m = OpenAiCompatibleJudgeModel(base_url="http://model", api_key="sk-abc123", model="gpt-x")
    m._http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(JudgeError) as exc:
        m.score([("system", "sys"), ("user", "user")])
    detail = str(exc.value)
    assert "272000" in detail and "368991" in detail
    assert "context_length_exceeded" in detail
    assert "tokens" in detail  # not mangled by an over-broad secret pattern


@pytest.mark.adapters
def test_redaction_covers_a_bearer_token_in_a_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="denied for Authorization: Bearer abcdef1234567890xyz")

    m = OpenAiCompatibleJudgeModel(base_url="http://model", api_key="", model="gpt-x")
    m._http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(JudgeError) as exc:
        m.score([("system", "sys"), ("user", "user")])
    assert "abcdef1234567890xyz" not in str(exc.value)
