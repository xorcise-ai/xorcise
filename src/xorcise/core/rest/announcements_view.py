"""Announcement relay (delivery/rest layer).

Fetches the banners XORCISE Remote is publishing and hands them to the LOCAL browser. Lives
in rest, not in the catalog island, because it reads Settings to decide whether to ask at all
— the same division as `rest/catalog_view.py`, whose `build_catalog_source` factory this
reuses so browse, pull and banners can never disagree about which remote is in play.

Degrade, don't crash — and here that is the ONLY rule worth remembering. An announcement is
decoration: a banner nobody sees costs an operator nothing, while an exception escaping this
module would take out the page the missions live on. Every path below therefore ends in an
`AnnouncementsResponse`, and there is no path that ends in an exception.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from xorcise.core.config import Settings
from xorcise.core.contracts.announcements import AnnouncementsResponse

if TYPE_CHECKING:
    from xorcise.core.catalog.source import CatalogSource

log = logging.getLogger(__name__)

_NONE = AnnouncementsResponse()


# One remote fetch per window per install, rather than one per browser document load.
#
# The client does not poll — `queries.ts` pins one request per document load and no refetch on
# focus, reconnect, remount or navigation. But that is a statement about one TAB: three open tabs
# refreshed were three remote requests, so "no polling" described what a reader costs over time
# and not what an install costs the hosted service. This is the other half of it.
#
# Keyed by catalog URL so pointing the app at a different remote cannot serve the previous one's
# banners. The TTL is deliberately short: an incident banner is the case that matters most, and a
# reader who refreshes should not wait minutes to see one.
#
# Failures the SOURCE absorbs are cached too, at the same TTL, and that is the trade-off worth
# naming: a remote that is down already degrades to an empty response, and retrying it on every
# page load is exactly the hammering this exists to stop — but it does mean a banner published
# during an outage can take up to one window to appear after the remote recovers.
#
# A failure caught at the BOUNDARY (the fetch's broad except) is not cached, because the memo is
# written only on success. httpx.InvalidURL from a malformed catalog_url is the realistic one, and
# it retries on every page load — harmless, because it never opens a socket, but it is not what
# the paragraph above describes.
_ANNOUNCEMENTS_TTL_SECONDS = 120.0
_announcements_cache: dict[str, tuple[float, AnnouncementsResponse]] = {}

# The TOTAL time a page load spends on the remote (#161). The fetch runs on its own thread and the
# request waits for it up to this long, then answers with the last banners this install knew of
# (or none) and leaves the fetch to land in the memo for the next load.
#
# Why a thread, when `HttpCatalogSource.announcements()` already carries a 5 s deadline: that
# deadline is tested once the response headers are in and between body reads, and a remote that
# dribbles one header just inside the per-read timeout never reaches either test — measured at
# 22.5 s, and linear in how many headers it chooses to send. Nothing synchronous in the calling
# thread can bound that phase, so the bound is taken here, by not BEING the thread that waits.
#
# The same figure as the source's deadline, so a remote that answers inside its budget is, in the
# normal case, served to the page load that asked. It is not exact: this clock starts a moment
# before the source's, and the source can overrun its budget by one read timeout during the body,
# so a remote finishing between roughly five and eight seconds is served to the NEXT load instead.
_PAGE_LOAD_WAIT_SECONDS = 5.0


class _Fetch:
    """One fetch in flight for one catalog URL: when it started, and a flag set when it ends."""

    def __init__(self, generation: int) -> None:
        self.started = time.monotonic()
        self.done = threading.Event()
        self.generation = generation


# At most ONE fetch in flight per catalog URL, and that is what makes the thread affordable. The
# cost the issue weighed against a watchdog was a leaked thread and socket per slow page load;
# single-flight caps it at one per remote, however many tabs load while that remote is stuck.
# Later page loads join the fetch already running instead of starting another, which also ends the
# cold-cache race the previous design accepted (two tabs, two requests).
#
# The price of single-flight, stated so it is not mistaken for a bug: a fetch has no maximum age.
# A remote that dribbles header bytes just inside the per-read timeout keeps its one fetch alive
# until h11's header cap ends it, which can be hours, and no new fetch for that catalog starts in
# the meantime. Page loads stay fast throughout; the banners simply stop changing until it ends.
#
# The lock guards the bookkeeping only, never the network call: holding it across a fetch would
# serialise every page load in the app behind one remote.
#
# `_generation` is bumped by `reset_announcements_cache`, so a fetch that started before a reset
# cannot write its answer into the memo after it — a test's stub, or a previous catalog's banners.
_lock = threading.Lock()
_in_flight: dict[str, _Fetch] = {}
_generation = 0


def _cached(url: str) -> AnnouncementsResponse | None:
    hit = _announcements_cache.get(url)
    if hit is None or (time.monotonic() - hit[0]) >= _ANNOUNCEMENTS_TTL_SECONDS:
        return None
    return hit[1]


def _last_known(url: str) -> AnnouncementsResponse:
    """The memo's answer regardless of age, or none: what a late remote leaves a page load.

    Stale beats empty here, but only while the refresh is still RUNNING: a banner already
    showing — an incident above all — stays on every reload while the remote is slow to repeat
    it. Once the fetch ends, its answer replaces this one, and that includes the empty answer the
    source gives when it abandons a fetch at its own deadline: the source does not distinguish
    "gave up" from "nothing published", so a remote that answers too slowly clears the banner for
    one window. A remote that is DOWN fails fast inside the source with the same empty answer.
    """
    hit = _announcements_cache.get(url)
    return _NONE if hit is None else hit[1]


def reset_announcements_cache() -> None:
    """Drop the memoised response — for tests, and for anything that changes the catalog config.

    A fetch still running is left to finish, but its answer is discarded (see `_generation`).
    """
    global _generation
    with _lock:
        _announcements_cache.clear()
        _in_flight.clear()
        _generation += 1


def _run_fetch(url: str, source: CatalogSource, fetch: _Fetch) -> None:
    """The fetch, on its own thread. Ends by marking itself done, whatever happened."""
    try:
        fresh = AnnouncementsResponse(announcements=source.announcements())
        with _lock:
            if fetch.generation == _generation:
                _announcements_cache[url] = (time.monotonic(), fresh)
    except Exception as exc:  # noqa: BLE001
        # BROAD ON PURPOSE — do not narrow this, and do not delete it as redundant with the
        # narrow catch in HttpCatalogSource.announcements(): that one absorbs the EXPECTED
        # failures of a remote call, while this is the boundary that makes the guarantee total
        # (httpx.InvalidURL from a malformed catalog_url, for one, is not an HTTPError and
        # arrives here). On this thread an escaped exception would not 500 anything, but it would
        # leave the reason unlogged and, without the `finally`, the fetch marked in flight
        # forever. One WARNING line for an operator (an unreachable remote already degraded
        # silently inside the source, so anything arriving here is unexpected); the traceback
        # stays available at DEBUG.
        log.warning("announcements unavailable: %s", exc)
        log.debug("announcement fetch failure detail", exc_info=True)
    finally:
        with _lock:
            if _in_flight.get(url) is fetch:
                del _in_flight[url]
        # LAST, after the memo and the log: a page load woken by this reads both.
        fetch.done.set()


def _fetch_for(url: str, settings: Settings) -> _Fetch:
    """The fetch in flight for `url`, starting one if there is none."""
    with _lock:
        fetch = _in_flight.get(url)
        if fetch is not None:
            return fetch
        fetch = _Fetch(_generation)
        _in_flight[url] = fetch
    try:
        # INSIDE a function, not at module scope: a module-scope import would drag the catalog
        # island onto every role's boot path and fail the role-isolation topology test.
        from xorcise.core.rest.catalog_view import build_catalog_source

        source = build_catalog_source(settings)
        # Daemon: a fetch stuck on a hostile remote must never hold `xorcise down` open.
        threading.Thread(
            target=_run_fetch,
            args=(url, source, fetch),
            name="xorcise-announcements",
            daemon=True,
        ).start()
    except BaseException:
        # Nothing is running for this entry, so nothing would ever clear it: clear it here, or
        # every later page load would join a fetch that does not exist.
        with _lock:
            if _in_flight.get(url) is fetch:
                del _in_flight[url]
        fetch.done.set()
        raise
    return fetch


def list_announcements(settings: Settings) -> AnnouncementsResponse:
    """The active remote announcements, or an empty response — never an exception."""
    # Short-circuit BEFORE any network call. `catalog_enabled`/`catalog_url` are the operator's
    # "disconnect the remote catalog" switch, and it would not mean much if the app still
    # phoned home for banners after it was thrown.
    #
    # `use_stubs` is in this list deliberately, and is not merely an optimisation: `xorcise up
    # --stub` must stay deterministic, and the documentation screenshot pipeline runs stub mode
    # against the real default catalog URL — without this guard every screenshot would capture
    # whatever banner happened to be live the day it was taken.
    if not settings.catalog_enabled or not settings.catalog_url or settings.use_stubs:
        return _NONE

    url = settings.catalog_url
    try:
        cached = _cached(url)
        if cached is not None:
            return cached
        fetch = _fetch_for(url, settings)
        # Measured from when the FETCH started, not this request: a page load that joins a fetch
        # already past the budget answers at once instead of waiting a fresh five seconds behind
        # a remote that has already shown it is stuck.
        remaining = fetch.started + _PAGE_LOAD_WAIT_SECONDS - time.monotonic()
        if remaining > 0 and not fetch.done.wait(remaining):
            log.warning(
                "catalog has not answered with its announcements within %.0fs; serving the "
                "last answer it gave, if any, while the fetch continues in the background",
                _PAGE_LOAD_WAIT_SECONDS,
            )
        # Whichever happened, the memo holds the best answer there is: the one the fetch just
        # wrote, or the last one before it.
        return _last_known(url)
    except Exception as exc:  # noqa: BLE001
        # The same boundary as `_run_fetch`'s, for the half of the work done on THIS thread:
        # building the source and starting its thread. Any failure reaching the router would turn
        # a missing banner into a 500 and, through the frontend's error handling, into an app that
        # will not load. It matches catalog_view.catalog_status for the same reason.
        log.warning("announcements unavailable: %s", exc)
        log.debug("announcement fetch failure detail", exc_info=True)
        return _NONE
