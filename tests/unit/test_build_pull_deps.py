"""build_pull_deps selects the real HTTP source iff catalog_url is set."""

from __future__ import annotations

import pytest

from xorcise.core.catalog import HttpCatalogSource, StubCatalogSource
from xorcise.core.config import Settings
from xorcise.core.rest.mission_pull import build_pull_deps

pytestmark = pytest.mark.unit


def test_selects_http_source_when_catalog_url_set() -> None:
    deps = build_pull_deps(Settings(catalog_url="https://catalog.example.com"), use_docker=False)
    assert isinstance(deps.source, HttpCatalogSource)


def test_selects_stub_source_when_no_catalog_url() -> None:
    deps = build_pull_deps(Settings(catalog_url=None), use_docker=False)
    assert isinstance(deps.source, StubCatalogSource)
