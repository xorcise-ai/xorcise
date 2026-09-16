"""GET /api/announcements (real wiring): always 200, always a list, never the app's problem.

Announcements are decoration. The contract this file pins is that NOTHING about them — a
switch, an empty setting, stub mode, an unreachable host, or an outright exception from the
source — can produce anything other than a 200 with an announcements array.

NOTE the env override in every test: the default `catalog_url` is a REAL production
endpoint, so an un-overridden test would dial the internet from the unit lane.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.announcements import Announcement
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _settings(monkeypatch, **env: str) -> None:
    from xorcise.core.config import get_settings

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _explode(settings):  # noqa: ANN001 — a stand-in for the factory, signature-compatible
    raise AssertionError("the catalog source must not be built on a short-circuit path")


def _banner(ident: str, placement: str) -> Announcement:
    return Announcement(
        id=ident,
        revision=1,
        placement=placement,  # type: ignore[arg-type]  # the test supplies a valid member
        tone="information",
        body_md="hello",
        dismissible=True,
    )


class _Source:
    """Minimal stand-in for a CatalogSource: only announcements() is exercised here."""

    def __init__(self, *banners: Announcement) -> None:
        self._banners = banners

    def announcements(self) -> tuple[Announcement, ...]:
        return self._banners


def test_switched_off_returns_empty_without_building_a_source(migrated_home, monkeypatch):
    # The operator disconnected the remote catalog, so there is nothing to ask. Proven by
    # making the factory raise: if the response is still 200/empty, no network path was taken.
    _settings(monkeypatch, XORCISE_CATALOG_ENABLED="false")
    monkeypatch.setattr("xorcise.core.rest.catalog_view.build_catalog_source", _explode)
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_no_catalog_url_returns_empty_without_building_a_source(migrated_home, monkeypatch):
    _settings(monkeypatch, XORCISE_CATALOG_URL="")
    monkeypatch.setattr("xorcise.core.rest.catalog_view.build_catalog_source", _explode)
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_stub_mode_returns_empty_without_building_a_source(migrated_home, monkeypatch):
    # `xorcise up --stub` must stay deterministic, and the docs screenshot pipeline runs stub
    # mode against the real default catalog URL — so stub mode must never fetch a live banner.
    _settings(monkeypatch, XORCISE_USE_STUBS="1", XORCISE_CATALOG_URL="https://catalog.invalid")
    monkeypatch.setattr("xorcise.core.rest.catalog_view.build_catalog_source", _explode)
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_an_unreachable_catalog_is_empty_not_a_500(migrated_home, monkeypatch):
    # Deliberately offline: a host that does not resolve. The endpoint answers 200 with no
    # banners rather than a 500 that a frontend error boundary would have to absorb.
    _settings(monkeypatch, XORCISE_CATALOG_URL="https://catalog.invalid", XORCISE_USE_STUBS="0")
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_a_configured_source_serves_both_placements(migrated_home, monkeypatch):
    _settings(monkeypatch, XORCISE_CATALOG_URL="https://catalog.invalid", XORCISE_USE_STUBS="0")
    source = _Source(_banner("app-1", "application"), _banner("cat-1", "catalog"))
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: source
    )
    body = _client().get("/api/announcements").json()
    assert [a["id"] for a in body["announcements"]] == ["app-1", "cat-1"]
    assert body["announcements"][0]["placement"] == "application"


def test_a_raising_source_is_empty_and_leaves_the_catalog_working(
    migrated_home, monkeypatch, caplog
):
    # FAILURE ISOLATION, the whole point of this endpoint's error handling: a broken
    # announcement source costs the banner and nothing else — the catalog still answers.
    # The remote is CONFIGURED here, so no settings guard short-circuits and the raise really
    # does reach the view's broad except (the logged warning proves which branch ran).
    import logging

    _settings(monkeypatch, XORCISE_CATALOG_URL="https://catalog.invalid", XORCISE_USE_STUBS="0")

    class _Broken:
        def announcements(self) -> tuple[Announcement, ...]:
            raise RuntimeError("source exploded")

    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: _Broken()
    )
    client = _client()
    with caplog.at_level(logging.WARNING, logger="xorcise.core.rest.announcements_view"):
        r = client.get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}
    assert "source exploded" in caplog.text
    assert client.get("/api/catalog/status").status_code == 200


def test_a_malformed_catalog_url_is_empty_not_a_500(migrated_home, monkeypatch):
    # The real reason the view's broad `except Exception` cannot be deleted as redundant:
    # httpx.InvalidURL derives from Exception, NOT httpx.HTTPError, so a malformed setting
    # sails straight through HttpCatalogSource.announcements()' narrow catch and is stopped
    # only here. Both layers are load-bearing; this pins that.
    _settings(monkeypatch, XORCISE_CATALOG_URL="::::", XORCISE_USE_STUBS="0")
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_the_response_is_never_cached(migrated_home, monkeypatch):
    # A publish or a withdrawal must reach the operator on a manual refresh, so the browser
    # is told not to keep a copy.
    _settings(monkeypatch, XORCISE_CATALOG_URL="")
    r = _client().get("/api/announcements")
    assert r.headers["cache-control"] == "no-store"
