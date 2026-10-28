"""GET /api/announcements (real wiring): always 200, always a list, never the app's problem.

Announcements are decoration. The contract this file pins is that NOTHING about them — a
switch, an empty setting, stub mode, an unreachable host, or an outright exception from the
source — can produce anything other than a 200 with an announcements array.

NOTE the env override in every test: the default `catalog_url` is a REAL production
endpoint, so an un-overridden test would dial the internet from the unit lane. The override is
`http://127.0.0.1:1` — a closed port on the loopback interface — rather than a reserved
`.invalid` name, because a name still goes to the resolver: `.invalid` is guaranteed not to
EXIST, not guaranteed not to ANSWER, and a hijacking or captive resolver (the case
`catalog/http.py` names in its own comments) turns "unreachable" into a real connection
attempt against somebody else's host. A refused loopback connect needs no resolver and fails
in microseconds.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.announcements import Announcement
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


# A closed port on loopback: refused immediately, by the kernel, with no name to resolve.
_UNREACHABLE = "http://127.0.0.1:1"


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _settings(monkeypatch, **env: str) -> None:
    from xorcise.core.config import get_settings
    from xorcise.core.rest.announcements_view import reset_announcements_cache

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    # The announcements memo is dropped around every test by an autouse fixture in
    # tests/conftest.py, so it is not reset here — but a test that changes the catalog URL
    # MID-test still needs it, which is why this helper keeps calling it.
    reset_announcements_cache()


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
    _settings(monkeypatch, XORCISE_USE_STUBS="1", XORCISE_CATALOG_URL=_UNREACHABLE)
    monkeypatch.setattr("xorcise.core.rest.catalog_view.build_catalog_source", _explode)
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_an_unreachable_catalog_is_empty_not_a_500(migrated_home, monkeypatch):
    # Deliberately offline: a port nothing is listening on. The endpoint answers 200 with no
    # banners rather than a 500 that a frontend error boundary would have to absorb.
    _settings(monkeypatch, XORCISE_CATALOG_URL=_UNREACHABLE, XORCISE_USE_STUBS="0")
    r = _client().get("/api/announcements")
    assert r.status_code == 200 and r.json() == {"announcements": []}


def test_a_configured_source_serves_both_placements(migrated_home, monkeypatch):
    _settings(monkeypatch, XORCISE_CATALOG_URL=_UNREACHABLE, XORCISE_USE_STUBS="0")
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

    _settings(monkeypatch, XORCISE_CATALOG_URL=_UNREACHABLE, XORCISE_USE_STUBS="0")

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


# ── one remote fetch per window per install, not one per document load (#137 review) ──────────


def _counting(monkeypatch, calls: list[str]) -> None:
    """Point the factory at a source that records which catalog it was asked for."""
    from xorcise.core.rest import announcements_view

    class _Counter:
        def __init__(self, url: str) -> None:
            self._url = url

        def announcements(self) -> tuple[Announcement, ...]:
            calls.append(self._url)
            return ()

    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source",
        lambda s: _Counter(s.catalog_url),
    )
    announcements_view.reset_announcements_cache()


def _live_settings(monkeypatch, url: str):
    from xorcise.core.config import get_settings

    _settings(monkeypatch, XORCISE_CATALOG_URL=url, XORCISE_USE_STUBS="0")
    return get_settings()


def test_a_second_page_load_inside_the_window_does_not_hit_the_remote(
    migrated_home, monkeypatch
) -> None:
    from xorcise.core.rest.announcements_view import list_announcements

    calls: list[str] = []
    _counting(monkeypatch, calls)
    settings = _live_settings(monkeypatch, "https://catalog.example")

    list_announcements(settings)
    list_announcements(settings)
    list_announcements(settings)

    assert calls == ["https://catalog.example"], "three document loads, one remote request"


def test_the_window_expires(migrated_home, monkeypatch) -> None:
    from xorcise.core.rest import announcements_view
    from xorcise.core.rest.announcements_view import list_announcements

    calls: list[str] = []
    _counting(monkeypatch, calls)
    settings = _live_settings(monkeypatch, "https://catalog.example")

    import time

    list_announcements(settings)
    later = time.monotonic() + announcements_view._ANNOUNCEMENTS_TTL_SECONDS + 1
    monkeypatch.setattr(time, "monotonic", lambda: later)
    list_announcements(settings)

    assert len(calls) == 2, "a request after the window must reach the remote again"


def test_a_different_catalog_is_not_served_the_previous_one_s_banners(
    migrated_home, monkeypatch
) -> None:
    from xorcise.core.rest.announcements_view import list_announcements

    calls: list[str] = []
    _counting(monkeypatch, calls)

    list_announcements(_live_settings(monkeypatch, "https://one.example"))
    list_announcements(_live_settings(monkeypatch, "https://two.example"))

    assert calls == ["https://one.example", "https://two.example"]


# ── a slow remote cannot hold a page load open (#161) ─────────────────────────────────────────


def _drain_fetches(timeout: float = 5.0) -> None:
    """Wait for every background announcements fetch to end, so none outlives its test.

    It only waits, and does not assert, because it runs in `finally` blocks, where a failure of
    its own would replace the test's real one in the report.
    """
    import threading

    for thread in threading.enumerate():
        if thread.name == "xorcise-announcements":
            thread.join(timeout)


class _Gated:
    """A source whose fetch does not answer until the test opens its gate."""

    def __init__(self, *banners: Announcement) -> None:
        import threading

        self.gate = threading.Event()
        self.calls = 0
        self._banners = banners

    def announcements(self) -> tuple[Announcement, ...]:
        self.calls += 1
        self.gate.wait(10)
        return self._banners


def _short_wait(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr("xorcise.core.rest.announcements_view._PAGE_LOAD_WAIT_SECONDS", seconds)


def test_a_remote_trickling_its_headers_cannot_hold_a_page_load_open(
    migrated_home, monkeypatch
) -> None:
    # The issue's own reproduction, against the real HttpCatalogSource: a remote that sends one
    # header at a time, each inside the per-read timeout, never reaches the source's deadline
    # check. The page load must answer at the view's wait regardless.
    import socket
    import threading
    import time

    stop = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    # So a fetch that never connects cannot leave this server blocked in accept() for good.
    listener.settimeout(5)

    def trickle() -> None:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            conn.sendall(b"HTTP/1.1 200 OK\r\n")
            n = 0
            while not stop.wait(0.1):
                conn.sendall(f"X-Pad-{n}: x\r\n".encode())
                n += 1

    server = threading.Thread(target=trickle, daemon=True)
    server.start()
    _settings(
        monkeypatch,
        XORCISE_CATALOG_URL=f"http://127.0.0.1:{listener.getsockname()[1]}",
        XORCISE_USE_STUBS="0",
    )
    _short_wait(monkeypatch, 0.5)
    try:
        began = time.monotonic()
        r = _client().get("/api/announcements")
        elapsed = time.monotonic() - began
    finally:
        # Closing the connection ends the stuck fetch, so its thread does not outlive the test.
        stop.set()
        server.join(5)
        listener.close()
        _drain_fetches()
    assert r.status_code == 200 and r.json() == {"announcements": []}
    assert not any(
        t.name == "xorcise-announcements" and t.is_alive() for t in threading.enumerate()
    ), "the stuck fetch outlived the connection it was stuck on"
    assert elapsed < 3.0, f"the page load waited {elapsed:.1f}s on a remote still sending headers"


def test_a_late_answer_is_served_to_the_next_page_load_without_another_fetch(
    migrated_home, monkeypatch
) -> None:
    from xorcise.core.rest.announcements_view import list_announcements

    source = _Gated(_banner("late-1", "application"))
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: source
    )
    settings = _live_settings(monkeypatch, "https://catalog.example")
    _short_wait(monkeypatch, 0.1)
    try:
        assert list_announcements(settings).announcements == ()
    finally:
        source.gate.set()
        _drain_fetches()

    assert [a.id for a in list_announcements(settings).announcements] == ["late-1"]
    assert source.calls == 1, "the late answer was remembered, not fetched again"


def test_a_banner_already_known_survives_a_refresh_that_is_late(migrated_home, monkeypatch) -> None:
    # Stale beats empty: an incident banner must not vanish from every reload because the remote
    # has become slow to repeat it.
    from xorcise.core.rest import announcements_view
    from xorcise.core.rest.announcements_view import list_announcements

    settings = _live_settings(monkeypatch, "https://catalog.example")
    first = _Source(_banner("incident-1", "application"))
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: first
    )
    list_announcements(settings)
    _drain_fetches()

    slow = _Gated()
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: slow
    )
    monkeypatch.setattr(announcements_view, "_ANNOUNCEMENTS_TTL_SECONDS", 0.0)
    _short_wait(monkeypatch, 0.1)
    try:
        served = list_announcements(settings)
    finally:
        slow.gate.set()
        _drain_fetches()
    assert slow.calls == 1, "the expired memo did start a refresh"
    assert [a.id for a in served.announcements] == ["incident-1"]


def test_page_loads_join_the_fetch_in_flight_and_do_not_wait_on_it_again(
    migrated_home, monkeypatch
) -> None:
    # Single-flight is what keeps the thread affordable: however many tabs load while the remote
    # is stuck, there is one fetch and one socket. And a load that joins a fetch already past the
    # budget answers at once, rather than waiting a fresh budget behind it.
    import time

    from xorcise.core.rest.announcements_view import list_announcements

    source = _Gated()
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: source
    )
    settings = _live_settings(monkeypatch, "https://catalog.example")
    _short_wait(monkeypatch, 0.3)
    try:
        list_announcements(settings)
        began = time.monotonic()
        list_announcements(settings)
        list_announcements(settings)
        rejoined = time.monotonic() - began
    finally:
        source.gate.set()
        _drain_fetches()
    assert source.calls == 1, "three page loads, one fetch in flight"
    assert rejoined < 0.25, f"page loads joining a late fetch waited {rejoined:.2f}s"


def test_a_fetch_that_began_before_a_reset_cannot_answer_after_it(
    migrated_home, monkeypatch
) -> None:
    from xorcise.core.rest.announcements_view import list_announcements, reset_announcements_cache

    stale = _Gated(_banner("before-reset", "application"))
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: stale
    )
    settings = _live_settings(monkeypatch, "https://catalog.example")
    _short_wait(monkeypatch, 0.1)
    try:
        list_announcements(settings)
        reset_announcements_cache()
    finally:
        stale.gate.set()
        _drain_fetches()

    # Not `_counting`: it resets the memo itself, which would hide a late write.
    fresh = _Source(_banner("after-reset", "application"))
    monkeypatch.setattr(
        "xorcise.core.rest.catalog_view.build_catalog_source", lambda settings: fresh
    )
    served = list_announcements(settings)
    assert [a.id for a in served.announcements] == ["after-reset"], (
        "the fetch from before the reset wrote its answer into the memo"
    )


def test_a_source_that_cannot_be_built_does_not_leave_a_fetch_in_flight(
    migrated_home, monkeypatch
) -> None:
    from xorcise.core.rest import announcements_view
    from xorcise.core.rest.announcements_view import list_announcements

    def _broken(settings):  # noqa: ANN001
        raise RuntimeError("factory exploded")

    monkeypatch.setattr("xorcise.core.rest.catalog_view.build_catalog_source", _broken)
    settings = _live_settings(monkeypatch, "https://catalog.example")
    assert list_announcements(settings).announcements == ()
    assert announcements_view._in_flight == {}, "a later page load would join a dead fetch"
