"""The announcement DTO (strict, local) + the lenient inbound parser (untrusted, remote).

Two directions, two rules, and this file pins both: the frozen model is what we SERVE to our
own frontend, so it forbids surprises; `announcement_from_remote` is what we READ from XORCISE
Remote, so it tolerates them and returns None rather than raising.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.announcements import (
    MAX_BODY_CHARS,
    MAX_ID_CHARS,
    Announcement,
    AnnouncementsResponse,
    announcement_from_remote,
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


def test_remote_parses_a_well_formed_payload() -> None:
    ann = announcement_from_remote(_GOOD)
    assert ann is not None
    assert ann.id == "a1"
    assert ann.revision == 3
    assert ann.placement == "application"
    assert ann.tone == "information"
    assert ann.dismissible is True


def test_remote_ignores_a_key_the_server_added_later() -> None:
    # The whole point of the lenient path: a server that grows a field must not blank the
    # banner on every client that predates it.
    ann = announcement_from_remote({**_GOOD, "starts_at": "2026-09-01T00:00:00Z", "colour": "red"})
    assert ann is not None and ann.id == "a1"


def test_remote_rejects_a_non_mapping() -> None:
    assert announcement_from_remote(["not", "a", "mapping"]) is None
    assert announcement_from_remote(None) is None
    assert announcement_from_remote("a1") is None


def test_remote_rejects_a_missing_key() -> None:
    for key in _GOOD:
        payload = {k: v for k, v in _GOOD.items() if k != key}
        assert announcement_from_remote(payload) is None, key


def test_remote_rejects_an_empty_or_non_string_id() -> None:
    assert announcement_from_remote({**_GOOD, "id": ""}) is None
    assert announcement_from_remote({**_GOOD, "id": 7}) is None


def test_remote_rejects_an_oversized_id() -> None:
    # The browser uses `id` as a localStorage dismissal key, so an unbounded identifier from a
    # broken or hostile remote would be written verbatim into the user's browser storage.
    assert announcement_from_remote({**_GOOD, "id": "a" * MAX_ID_CHARS}) is not None
    assert announcement_from_remote({**_GOOD, "id": "a" * (MAX_ID_CHARS + 1)}) is None


def test_remote_rejects_a_bad_placement() -> None:
    assert announcement_from_remote({**_GOOD, "placement": "sidebar"}) is None


def test_remote_rejects_a_bad_tone() -> None:
    assert announcement_from_remote({**_GOOD, "tone": "urgent"}) is None


def test_remote_rejects_a_body_over_the_cap() -> None:
    assert announcement_from_remote({**_GOOD, "body_md": "x" * MAX_BODY_CHARS}) is not None
    assert announcement_from_remote({**_GOOD, "body_md": "x" * (MAX_BODY_CHARS + 1)}) is None


def test_remote_rejects_a_non_string_body() -> None:
    assert announcement_from_remote({**_GOOD, "body_md": 42}) is None


def test_remote_rejects_a_bool_revision() -> None:
    # isinstance(True, int) is True in Python, so a naive int check would let a bool
    # through and serve `revision: true` to the browser. A bool here is a server bug.
    assert announcement_from_remote({**_GOOD, "revision": True}) is None
    assert announcement_from_remote({**_GOOD, "revision": "3"}) is None


def test_remote_rejects_a_non_bool_dismissible() -> None:
    assert announcement_from_remote({**_GOOD, "dismissible": "yes"}) is None
    assert announcement_from_remote({**_GOOD, "dismissible": 1}) is None


def test_remote_forces_an_incident_to_be_undismissible() -> None:
    # An incident banner is never dismissible. Enforcing it locally means a server bug
    # cannot hide an active incident from an operator with one click.
    ann = announcement_from_remote({**_GOOD, "tone": "incident", "dismissible": True})
    assert ann is not None and ann.dismissible is False


def test_remote_leaves_other_tones_dismissible_as_published() -> None:
    for tone in ("information", "maintenance", "resolved"):
        ann = announcement_from_remote({**_GOOD, "tone": tone, "dismissible": True})
        assert ann is not None and ann.dismissible is True, tone
