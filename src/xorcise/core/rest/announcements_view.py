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
import time

from xorcise.core.config import Settings
from xorcise.core.contracts.announcements import AnnouncementsResponse

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
# A failure caught at the BOUNDARY below is not cached, because the memo is written on the way out
# of the fetch and an exception never reaches it. httpx.InvalidURL from a malformed catalog_url is
# the realistic one, and it retries on every page load — harmless, because it never opens a
# socket, but it is not what the paragraph above describes.
#
# No lock around the fetch. Two tabs racing a cold cache make two requests and the second write
# wins, which costs one extra round trip and never serves a wrong answer; holding a lock across a
# network call would serialise every page load in the app behind one remote.
_ANNOUNCEMENTS_TTL_SECONDS = 120.0
_announcements_cache: dict[str, tuple[float, AnnouncementsResponse]] = {}


def _cached(url: str) -> AnnouncementsResponse | None:
    hit = _announcements_cache.get(url)
    if hit is None or (time.monotonic() - hit[0]) >= _ANNOUNCEMENTS_TTL_SECONDS:
        return None
    return hit[1]


def _remember(url: str, response: AnnouncementsResponse) -> None:
    _announcements_cache[url] = (time.monotonic(), response)


def reset_announcements_cache() -> None:
    """Drop the memoised response — for tests, and for anything that changes the catalog config."""
    _announcements_cache.clear()


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

    try:
        # INSIDE the try, not above it, so the docstring's "no path ends in an exception" is
        # literally true and not merely true-in-practice: `catalog_view` is realistically
        # always in sys.modules by now, but an import is still code that can raise. It stays
        # inside the FUNCTION either way — a module-scope import would drag the catalog island
        # onto every role's boot path and fail the role-isolation topology test.
        from xorcise.core.rest.catalog_view import build_catalog_source

        cached = _cached(settings.catalog_url)
        if cached is not None:
            return cached
        fresh = AnnouncementsResponse(announcements=build_catalog_source(settings).announcements())
        _remember(settings.catalog_url, fresh)
        return fresh
    except Exception as exc:  # noqa: BLE001
        # BROAD ON PURPOSE — do not narrow this, and do not delete it as redundant with the
        # narrow catch in HttpCatalogSource.announcements(): that one absorbs the EXPECTED
        # failures of a remote call, while this is the boundary that makes the guarantee total
        # (httpx.InvalidURL from a malformed catalog_url, for one, is not an HTTPError and
        # arrives here). It matches catalog_view.catalog_status for the same reason: this runs
        # on the page-load path, so ANY failure reaching the router would turn a missing banner
        # into a 500 and, through the frontend's error handling, into an app that will not
        # load. One WARNING line for an operator (an unreachable remote already degraded
        # silently inside the source, so anything arriving here is unexpected); the traceback
        # stays available at DEBUG.
        log.warning("announcements unavailable: %s", exc)
        log.debug("announcement fetch failure detail", exc_info=True)
        return _NONE
