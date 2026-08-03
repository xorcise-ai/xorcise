"""Mission-library (catalog) failure paths + operation-aware error guidance.

The full failure matrix: configured vs live state are never conflated, every
failure answers with guidance relevant to ITS OWN workflow, and no catalog
failure ever suggests `xorcise run status`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.cli._shared import app
from xorcise.core.cli.rest_client import RestClient

pytestmark = pytest.mark.unit

runner = CliRunner()

_URL = "https://catalog.example.com"


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool,
    live: dict[str, Any] | None,
) -> dict[str, int]:
    """Path-keyed stubs: /config carries the SETTING, /catalog/status the LIVE probe
    (None ⇒ the live check gets no answer). Returns a call counter."""
    calls = {"config": 0, "live": 0, "put": 0}

    def fake_get(self: RestClient, path: str, timeout: float | None = None) -> Any:
        assert path == "/config"
        calls["config"] += 1
        return {"catalog": {"connected": enabled, "url": _URL}}

    def fake_get_or_none(self: RestClient, path: str, timeout: float | None = None) -> Any:
        assert path == "/catalog/status"
        calls["live"] += 1
        return live

    def fake_put(self: RestClient, path: str, json: dict[str, Any]) -> Any:
        calls["put"] += 1
        return {"catalog": {"connected": json["connected"], "url": _URL}}

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "get_or_none", fake_get_or_none)
    monkeypatch.setattr(RestClient, "put", fake_put)
    return calls


# --- catalog status: the state model -----------------------------------------


def test_status_enabled_and_reachable(monkeypatch):
    _wire(monkeypatch, enabled=True, live={"state": "connected", "last_sync": None})
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 0
    assert "Configuration: Enabled" in result.stdout
    assert _URL in result.stdout
    assert "Reachable" in result.stdout


def test_status_enabled_but_unreachable(monkeypatch):
    _wire(
        monkeypatch,
        enabled=True,
        live={"state": "error", "message": "connect timeout to the library"},
    )
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 1
    assert "Configuration: Enabled" in result.stdout
    assert "Unreachable" in result.stdout
    assert "could not reach it" in result.stderr
    assert "xorcise doctor" in result.stderr


def test_status_disabled(monkeypatch):
    _wire(monkeypatch, enabled=False, live={"state": "disconnected"})
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 0
    assert "Configuration: Disabled" in result.stdout
    assert "xorcise catalog connect" in result.stdout


def test_status_live_check_times_out(monkeypatch):
    """The service held the live probe too long — degrade, never a generic error."""
    _wire(monkeypatch, enabled=True, live=None)
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 1
    assert "Configuration: Enabled" in result.stdout  # the setting still renders
    assert "Unreachable" in result.stdout


def test_status_json_carries_configured_and_live_state(monkeypatch):
    _wire(monkeypatch, enabled=True, live={"state": "connected", "last_sync": None})
    result = runner.invoke(app, ["catalog", "status", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["configured"] is True
    assert body["state"] == "connected"
    assert body["url"] == _URL


def test_status_json_when_live_check_unanswered(monkeypatch):
    _wire(monkeypatch, enabled=True, live=None)
    result = runner.invoke(app, ["catalog", "status", "--json"])
    assert result.exit_code == 1
    body = json.loads(result.stdout)
    assert body["configured"] is True
    assert body["state"] == "unreachable"


# --- connect / disconnect are state-aware -------------------------------------


def test_connect_when_already_enabled_is_a_noop(monkeypatch):
    calls = _wire(monkeypatch, enabled=True, live={"state": "connected"})
    result = runner.invoke(app, ["catalog", "connect"])
    assert result.exit_code == 0
    assert "already enabled" in result.stdout
    assert calls["put"] == 0  # no write for a no-op


def test_connect_json_stays_json_when_already_enabled(monkeypatch):
    """The regression: --json fell through to prose on the idempotent path.

    `already enabled` is exit 0 — a SUCCESS — so a script piping to `jq` broke exactly
    when the library WAS connected, i.e. in the normal state. The shape must not depend
    on whether the command had anything to write."""
    calls = _wire(monkeypatch, enabled=True, live={"state": "connected"})
    result = runner.invoke(app, ["catalog", "connect", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)  # would raise on the prose this used to print
    assert body["catalog"]["connected"] is True
    assert calls["put"] == 0  # still a no-op — parity did not cost idempotence


def test_disconnect_json_stays_json_when_already_disabled(monkeypatch):
    # Symmetric to the above. The defect first surfaced only on `connect` because the
    # catalog happened to be CONNECTED, so `disconnect` never reached its own early
    # return — but it was in both commands all along.
    calls = _wire(monkeypatch, enabled=False, live=None)
    result = runner.invoke(app, ["catalog", "disconnect", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["catalog"]["connected"] is False
    assert calls["put"] == 0


def test_connect_reports_saved_but_unreachable(monkeypatch):
    """A saved preference is never reported as a verified connection."""
    _wire(monkeypatch, enabled=False, live={"state": "error", "message": "boom"})
    result = runner.invoke(app, ["catalog", "connect"])
    assert result.exit_code == 0
    assert "could not be reached" in result.stdout
    assert "setting was saved" in result.stdout
    assert "xorcise doctor" in result.stdout


def test_connect_verified_reachable(monkeypatch):
    _wire(monkeypatch, enabled=False, live={"state": "connected"})
    result = runner.invoke(app, ["catalog", "connect"])
    assert result.exit_code == 0
    assert "library enabled" in result.stdout
    assert "xorcise mission list" in result.stdout


def test_disconnect_when_already_disabled_is_a_noop(monkeypatch):
    calls = _wire(monkeypatch, enabled=False, live=None)
    result = runner.invoke(app, ["catalog", "disconnect"])
    assert result.exit_code == 0
    assert "already disabled" in result.stdout
    assert calls["put"] == 0


def test_disconnect_explains_the_consequence(monkeypatch):
    _wire(monkeypatch, enabled=True, live=None)
    result = runner.invoke(app, ["catalog", "disconnect"])
    assert result.exit_code == 0
    assert "library disabled" in result.stdout
    assert "local missions only" in result.stdout


# --- operation-aware transport errors -----------------------------------------


def _raise_on_send(monkeypatch, exc: Exception) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(exc))
    monkeypatch.setattr(httpx, "put", lambda *a, **k: (_ for _ in ()).throw(exc))


def test_catalog_timeout_never_suggests_run_status(monkeypatch):
    _raise_on_send(monkeypatch, httpx.ReadTimeout("slow"))
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 1
    assert "run status" not in result.stderr  # the round-3 brief's hard assertion
    assert "did not respond" in result.stderr
    assert "xorcise doctor" in result.stderr
    assert "xorcise status" in result.stderr


def test_service_down_guidance_is_service_scoped(monkeypatch):
    """DNS failure / conn refused / TLS failure all present as ConnectError —
    the answer is the service lifecycle, not any specific workflow."""
    for wire_error in (
        httpx.ConnectError("refused"),
        httpx.ConnectError("[Errno -2] Name or service not known"),
        httpx.ConnectError("CERTIFICATE_VERIFY_FAILED"),
    ):
        _raise_on_send(monkeypatch, wire_error)
        result = runner.invoke(app, ["catalog", "status"])
        assert result.exit_code == 1
        assert "cannot reach the XORCISE service" in result.stderr
        assert "xorcise up" in result.stderr
        assert "run status" not in result.stderr


def test_invalid_response_body_fails_clean(monkeypatch):
    request = httpx.Request("GET", "http://x")
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: httpx.Response(200, request=request, text="<html>")
    )
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 1
    assert "expected JSON" in result.stderr
    assert "Traceback" not in result.output


def test_auth_failure_surfaces_server_detail(monkeypatch):
    request = httpx.Request("GET", "http://x")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **k: httpx.Response(
            401, request=request, json={"detail": "library token rejected"}
        ),
    )
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 1
    assert "library token rejected" in result.stderr
    assert "run status" not in result.stderr


def test_system_after_catalog_failure_shows_config_not_connected(monkeypatch):
    """`system` must never print a bare 'connected' meaning merely 'enabled'."""
    info = {
        "role": "all",
        "planes": [{"name": "rest", "ok": True, "detail": "ok", "location": "127.0.0.1:3001"}],
        "catalog": {"state": "error", "message": "probe failed", "last_sync": None},
        "remotes": [],
    }
    monkeypatch.setattr(RestClient, "get", lambda self, path, timeout=None: info)
    result = runner.invoke(app, ["system"])
    assert result.exit_code == 0
    assert "mission library: enabled — last live check failed" in result.stdout
    assert "xorcise catalog status" in result.stdout
    assert "catalog:   connected" not in result.stdout
