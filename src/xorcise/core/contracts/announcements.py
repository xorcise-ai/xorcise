"""Announcement wire DTO (LEAF). Imports nothing internal.

An Announcement is a short Markdown banner published by XORCISE Remote and relayed by the
LOCAL backend to its own browser: `placement="application"` banners the whole app,
`placement="catalog"` banners only the Mission Catalog's XORCISE Remote tab. At most one of
each is ever active.

The browser never talks to XORCISE Remote directly — the local backend fetches on its behalf,
so the operator's "disconnect the remote catalog" switch stays meaningful and no CORS is
needed. Announcements are DECORATION: every path that produces them degrades to an empty
response rather than failing, because nothing here may keep the app or its missions from
loading.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# The publisher caps a body at this many characters. Enforced again on the way IN — by
# `_announcement_from_remote` in `catalog/http.py`, which is the wire boundary — so a
# server-side regression cannot push an unbounded blob through the local API into the DOM.
MAX_BODY_CHARS = 600

# `id` is not just an identifier: the browser uses it as the localStorage key for "this reader
# dismissed this banner". An unbounded id would therefore be written verbatim into a user's
# browser storage, so it is bounded here — far above any real id, far below anything harmful.
MAX_ID_CHARS = 128

Placement = Literal["application", "catalog"]
Tone = Literal["information", "maintenance", "incident", "resolved"]

# The members again, as runtime values. Public because the inbound parser in
# `catalog/http.py` validates against them, and they sit here, directly under the Literals
# they repeat, so the two cannot drift apart unnoticed.
PLACEMENTS: tuple[Placement, ...] = ("application", "catalog")
TONES: tuple[Tone, ...] = ("information", "maintenance", "incident", "resolved")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Announcement(_Frozen):
    """One active banner. `revision` increments when a published announcement is edited,
    so the browser can re-show a banner the reader had already dismissed."""

    id: str
    revision: int
    placement: Placement
    tone: Tone
    body_md: str
    dismissible: bool


class AnnouncementsResponse(_Frozen):
    """GET /api/announcements. Empty is the normal answer — nothing published, the remote
    catalog switched off, an older deployment that 404s, or any failure at all."""

    announcements: tuple[Announcement, ...] = ()
