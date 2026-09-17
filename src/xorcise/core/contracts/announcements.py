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

# The publisher caps a body at this many characters. Enforced again on the way IN so a
# server-side regression cannot push an unbounded blob through the local API into the DOM.
MAX_BODY_CHARS = 600

# `id` is not just an identifier: the browser uses it as the localStorage key for "this reader
# dismissed this banner". An unbounded id would therefore be written verbatim into a user's
# browser storage, so it is bounded here — far above any real id, far below anything harmful.
MAX_ID_CHARS = 128

Placement = Literal["application", "catalog"]
Tone = Literal["information", "maintenance", "incident", "resolved"]

_PLACEMENTS: tuple[Placement, ...] = ("application", "catalog")
_TONES: tuple[Tone, ...] = ("information", "maintenance", "incident", "resolved")


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


def announcement_from_remote(payload: object) -> Announcement | None:
    """One remote item -> a trusted Announcement, or None when it cannot be trusted.

    A module-level function, not a classmethod, so the frozen model stays a pure DTO.

    LENIENT by design, and deliberately NOT `extra="forbid"` on this path: a strict remote
    contract has already broken every client of this project once, when the server added a
    field. The strict frozen model above is the LOCAL wire shape — what we serve to our own
    frontend, where an unknown key is our own bug. The remote payload is untrusted input from
    a service that ships independently of this client, so it is read key by key: exactly the
    six known keys, every other key ignored, and anything unreadable returns None instead of
    raising. A malformed item costs one banner, never the response.

    `revision` rejects `bool` explicitly because `isinstance(True, int)` is True in Python —
    without that check a `revision: true` server bug would be served on to the browser as a
    valid revision.

    `id` and `body_md` are LENGTH-bounded, not merely type-checked, because both are relayed
    into the browser: `body_md` is rendered, and `id` becomes a localStorage dismissal key.
    Type-checking alone would let a broken or hostile remote hand the local app an unbounded
    string to store or draw. Over the bound is a rejection like any other — one banner lost,
    nothing else.

    An `incident` is then FORCED undismissible whatever the server said: an active incident
    banner is not something a server bug gets to let an operator click away.
    """
    if not isinstance(payload, dict):
        return None
    raw: dict[str, object] = payload

    ident = raw.get("id")
    if not isinstance(ident, str) or not ident or len(ident) > MAX_ID_CHARS:
        return None

    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        return None

    # `_PLACEMENTS`/`_TONES` are typed tuples of the Literal members, so this membership
    # test both validates at runtime and narrows the type — no cast needed below.
    placement = raw.get("placement")
    if placement not in _PLACEMENTS:
        return None

    tone = raw.get("tone")
    if tone not in _TONES:
        return None

    body_md = raw.get("body_md")
    if not isinstance(body_md, str) or len(body_md) > MAX_BODY_CHARS:
        return None

    dismissible = raw.get("dismissible")
    if not isinstance(dismissible, bool):
        return None

    return Announcement(
        id=ident,
        revision=revision,
        placement=placement,
        tone=tone,
        body_md=body_md,
        dismissible=False if tone == "incident" else dismissible,
    )
