# tests/unit/test_rest_harness_capabilities.py
"""GET /api/harnesses/capabilities — thin, sorted view over the adapter registry."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xorcise.core.rest.routers import harnesses


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(harnesses.router, prefix="/api")
    return TestClient(app)


def test_lists_every_registered_adapter_sorted_and_total() -> None:
    body = _client().get("/api/harnesses/capabilities").json()
    names = [p["adapter_name"] for p in body]
    assert names == sorted(names)
    assert {"claude-code", "codex", "openhands", "generic"} <= set(names)
    for p in body:
        assert len(p["kinds"]) == 18  # total over AgentEventKind


def test_generic_is_flagged_unverified() -> None:
    body = _client().get("/api/harnesses/capabilities").json()
    generic = next(p for p in body if p["adapter_name"] == "generic")
    assert generic["verified"] is False


def test_registration_descriptors_combine_capability_and_launch_planes() -> None:
    body = _client().get("/api/harnesses").json()
    names = [p["kind"] for p in body]
    assert names == sorted(names)
    assert {"claude-code", "codex", "openhands", "generic"} <= set(names)

    codex = next(p for p in body if p["kind"] == "codex")
    assert codex["display_name"] == "Codex CLI"
    assert "gpt-5.3-codex" in codex["model_hints"]
    assert codex["capabilities"]["adapter_name"] == "codex"
    assert codex["launch"]["launch_modes"] == ["host"]
    assert codex["launch"]["command_template"].startswith("codex exec")
    assert codex["launch"]["model_flag"] == "--model"
    assert codex["capabilities"]["message_roles"] == {
        "user": "supported",
        "agent": "unsupported",
    }
    assert codex["launch"]["tips"]
    assert codex["launch"]["mission_preamble"]

    generic = next(p for p in body if p["kind"] == "generic")
    assert generic["model_hints"] == []
    assert generic["launch"]["command_template"] is None
    assert generic["launch"]["tips"] == []
    assert generic["launch"]["mission_preamble"] == []
