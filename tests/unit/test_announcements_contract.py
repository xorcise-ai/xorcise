"""The announcement DTO served at GET /api/announcements: strict, frozen, local.

This file pins the OUTBOUND half of the contract — the shape we hand our own frontend, where
an unknown key is our own bug, so the model forbids surprises. The INBOUND half (the lenient
parser that reads an untrusted XORCISE Remote row) lives with the rest of the wire in
`catalog/http.py`, and is pinned in `test_announcements_http_source.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.announcements import (
    PLACEMENTS,
    TONES,
    Announcement,
    AnnouncementsResponse,
)

_GOOD: dict[str, object] = {
    "id": "a1",
    "revision": 3,
    "placement": "application",
    "tone": "information",
    "body_md": "Scheduled maintenance on **Sunday**.",
    "dismissible": True,
}


def test_the_local_model_is_strict_about_extra_keys() -> None:
    # The local wire shape is ours, so an unknown key is a bug in OUR code, not a
    # forward-compatible server. Fail loudly here; be lenient only on the inbound path.
    with pytest.raises(ValidationError):
        Announcement.model_validate({**_GOOD, "colour": "red"})


def test_the_local_model_is_frozen() -> None:
    ann = Announcement.model_validate(_GOOD)
    with pytest.raises(ValidationError):
        ann.body_md = "mutated"


def test_the_response_defaults_to_no_announcements() -> None:
    # The degraded answer must be constructible with no arguments — every failure path
    # in the stack returns exactly this.
    assert AnnouncementsResponse().announcements == ()


def test_the_runtime_member_tuples_match_the_literals_they_repeat() -> None:
    # PLACEMENTS/TONES exist so the inbound parser can validate at runtime, and they are a
    # hand-written copy of the Literal members. Nothing in the type system ties the two
    # together, so a tone added to one and not the other would be caught only here: the model
    # accepts every member of the tuple, and rejects the thing that is not one.
    for placement in PLACEMENTS:
        assert Announcement.model_validate({**_GOOD, "placement": placement})
    for tone in TONES:
        assert Announcement.model_validate({**_GOOD, "tone": tone})
    with pytest.raises(ValidationError):
        Announcement.model_validate({**_GOOD, "tone": "urgent"})
    with pytest.raises(ValidationError):
        Announcement.model_validate({**_GOOD, "placement": "sidebar"})
