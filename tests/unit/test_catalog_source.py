"""The catalog source seam + bundled free-library fixture."""

from __future__ import annotations

import pytest

from xorcise.core.catalog import FREE_LIBRARY, StubCatalogSource


def test_enabled_returns_fixture() -> None:
    items = StubCatalogSource(enabled=True).list_library()
    assert items == FREE_LIBRARY and len(items) >= 1


def test_library_items_host_images() -> None:
    # the free library hosts prebuilt fused OCI images, not bundles
    assert all(i.image for i in FREE_LIBRARY)


def test_disabled_returns_empty() -> None:
    assert StubCatalogSource(enabled=False).list_library() == ()


def test_enabled_defaults_true() -> None:
    assert StubCatalogSource().list_library() == FREE_LIBRARY


def test_status_connected_when_enabled() -> None:
    assert StubCatalogSource(enabled=True).status().state == "connected"


def test_status_disconnected_when_disabled() -> None:
    s = StubCatalogSource(enabled=False).status()
    assert s.state == "disconnected" and s.message


def test_fetch_manifest_returns_fixture_manifest() -> None:
    m = StubCatalogSource(enabled=True).fetch_manifest("sqli-login")
    assert m.metadata.mission_id == "sqli-login" and m.metadata.objective


def test_fetch_manifest_unknown_id_raises() -> None:
    from xorcise.core.contracts.errors import NotFoundError

    with pytest.raises(NotFoundError):
        StubCatalogSource(enabled=True).fetch_manifest("nope")
