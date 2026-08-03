"""The API origin answers /favicon.ico with the exported app icon.

The operator UI lives under /ui, but a browser pointed at the API origin still
probes the root for a favicon — so mount_ui serves the icon there too, degrading
to a 404 (never a crash) when the frontend hasn't been built into the wheel.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xorcise.core.rest import app as rest_app


def _client(monkeypatch: pytest.MonkeyPatch, static_dir: Path) -> TestClient:
    monkeypatch.setattr(rest_app, "_STATIC_DIR", static_dir)
    app = rest_app.create_app()
    rest_app.mount_ui(app)
    return TestClient(app)


def test_favicon_serves_the_exported_icon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    static = tmp_path / "_static"
    static.mkdir()
    (static / "icon.svg").write_text("<svg viewBox='0 0 32 32'/>", encoding="utf-8")

    r = _client(monkeypatch, static).get("/favicon.ico")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in r.text


def test_favicon_404s_when_the_export_has_no_icon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A source checkout / older export: the route exists but has nothing to serve.
    static = tmp_path / "_static"
    static.mkdir()

    assert _client(monkeypatch, static).get("/favicon.ico").status_code == 404


def test_placeholder_ui_and_no_icon_without_an_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Nothing vendored: /ui explains itself with the generated placeholder (a bare
    # 404 reads as a broken install); the favicon still degrades to 404.
    client = _client(monkeypatch, tmp_path / "missing")

    assert client.get("/favicon.ico").status_code == 404
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "xorcise up" in r.text
