"""Pure-logic unit tests for the scripted agent (no network)."""

from tests.fixtures.scripted_agent import ScriptedAgent


def _agent() -> ScriptedAgent:
    return ScriptedAgent(rest_url="http://x/api", otlp_url="http://x:4318")


def test_join_network_marks_joined_when_prompt_present() -> None:
    a = _agent()
    assert a.joined is False
    assert a.join_network("CONNECT PROMPT ...") is True
    assert a.joined is True


def test_join_network_false_on_empty_prompt() -> None:
    a = _agent()
    assert a.join_network("") is False
    assert a.joined is False


def test_otlp_payload_carries_run_id_and_span_names() -> None:
    a = _agent()
    payload = a._otlp_payload("run-7", ["connect", "act"])
    attrs = payload["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "xorcise.run_id", "value": {"stringValue": "run-7"}} in attrs
    span_names = [s["name"] for s in payload["resourceSpans"][0]["scopeSpans"][0]["spans"]]
    assert span_names == ["connect", "act"]
