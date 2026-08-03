# tests/unit/test_otel_boot_wiring.py
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from xorcise.core.otel.store import SqliteSealStore
from xorcise.core.roles.boot.role_all import build_otel_app


@pytest.mark.unit
def test_build_otel_app_exposes_real_traces_route() -> None:
    app = build_otel_app()
    routes = {getattr(r, "path", None) for r in app.routes}
    assert "/v1/traces" in routes and "/healthz" in routes
    # the real app round-trips through its default store without raising
    assert TestClient(app).get("/healthz").json()["status"] == "ok"


@pytest.mark.unit
def test_build_otel_app_enforces_seals_via_sqlite_seal_store(migrated_home) -> None:
    # The boot-composed receiver must drop spans for a run sealed in the DURABLE sqlite
    # seal store — proving build_otel_app wires SqliteSealStore (not the InMemory default).
    SqliteSealStore().seal("boot-sealed")
    client = TestClient(build_otel_app())
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "xorcise.run_id", "value": {"stringValue": "boot-sealed"}}
                    ]
                },
                "scopeSpans": [{"spans": [{"name": "late"}]}],
            }
        ]
    }
    resp = client.post("/v1/traces", content=json.dumps(body))
    assert resp.status_code == 200
    assert resp.json()["partialSuccess"]["rejectedSpans"] == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("route", "decoder"),
    [
        ("/v1/traces", "route_otlp_protobuf"),
        ("/v1/logs", "route_otlp_logs_protobuf"),
    ],
)
def test_protobuf_decode_unavailable_returns_clean_500_not_uncaught(
    migrated_home, monkeypatch, route: str, decoder: str
) -> None:
    # When opentelemetry-proto is absent, the protobuf decoder raises RuntimeError with an
    # actionable "install xorcise[collector]" message. The ingest handler must surface that as a
    # clean 500 body — NOT let it escape as an unhandled 500 traceback (which is what happened
    # before the RuntimeError clause; TestClient would re-raise it instead of returning a response).
    from xorcise.core.otel.ingest import embedded

    def _boom(_body: bytes) -> object:
        raise RuntimeError("opentelemetry-proto is not installed; install xorcise[collector]")

    monkeypatch.setattr(embedded, decoder, _boom)
    client = TestClient(build_otel_app())
    resp = client.post(
        route, content=b"\x00\x01", headers={"content-type": "application/x-protobuf"}
    )
    assert resp.status_code == 500
    assert "opentelemetry-proto" in resp.json()["message"]


@pytest.mark.unit
def test_build_otel_app_fails_fast_when_mirror_enabled(migrated_home, monkeypatch) -> None:
    # enabling the reserved mirror must fail fast at the otel boot root,
    # not silently no-op. Default-off (the other tests in this file) builds fine.
    from xorcise.core import config
    from xorcise.core.otel.mirror import MirrorConfigError

    monkeypatch.setenv("XORCISE_OTEL_MIRROR_ENABLED", "1")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(MirrorConfigError):
            build_otel_app()
    finally:
        config.get_settings.cache_clear()  # don't leak the enabled setting to other tests


@pytest.mark.unit
def test_role_collector_apps_fails_fast_when_mirror_enabled(migrated_home, monkeypatch) -> None:
    # the collector boot root must also fail fast when the reserved mirror is enabled.
    from xorcise.core import config
    from xorcise.core.otel.mirror import MirrorConfigError
    from xorcise.core.roles.boot.role_collector import apps

    monkeypatch.setenv("XORCISE_OTEL_MIRROR_ENABLED", "1")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(MirrorConfigError):
            apps()
    finally:
        config.get_settings.cache_clear()  # don't leak the enabled setting to other tests
