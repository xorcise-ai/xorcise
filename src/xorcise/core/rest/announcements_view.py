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

from xorcise.core.config import Settings
from xorcise.core.contracts.announcements import AnnouncementsResponse

log = logging.getLogger(__name__)

_NONE = AnnouncementsResponse()


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

        return AnnouncementsResponse(announcements=build_catalog_source(settings).announcements())
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
