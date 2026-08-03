"""CatalogEntry browse wire DTO (leaf). Pure shape + validation, no I/O."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.catalog import CatalogEntry, CatalogStatus


def test_entry_minimal_defaults() -> None:
    e = CatalogEntry(source="library", mission_id="sqli", name="SQLi")
    assert e.installed is False and e.image is None
    assert e.skills == () and e.technologies == () and e.summary == ""
    assert e.proficiency is None and e.specialty is None and e.type is None


def test_entry_round_trips() -> None:
    e = CatalogEntry(
        source="your_own",
        mission_id="hello",
        name="Hello",
        summary="say hi to the box",
        proficiency="easy",
        specialty="web",
        type="lab",
        skills=("recon",),
        technologies=("nginx",),
        installed=True,
        image="xorcise/mission-hello:0",
    )
    assert CatalogEntry.model_validate(e.model_dump()) == e


def test_entry_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        CatalogEntry(source="premium", mission_id="x", name="x")  # type: ignore[arg-type]


def test_entry_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError) as exc:
        CatalogEntry.model_validate(
            {"source": "library", "mission_id": "x", "name": "x", "bogus": 1}
        )
    assert "bogus" in str(exc.value)


def test_status_round_trips() -> None:
    s = CatalogStatus(state="error", message="boom")
    assert CatalogStatus.model_validate(s.model_dump()) == s
    assert CatalogStatus(state="connected").message is None
    assert CatalogStatus(state="connected").last_sync is None


def test_status_rejects_unknown_state() -> None:
    with pytest.raises(ValidationError):
        CatalogStatus(state="weird")  # type: ignore[arg-type]
