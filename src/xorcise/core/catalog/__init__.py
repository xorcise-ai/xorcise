"""xorcise.core.catalog — free-library catalog client.

LAYER: PART-ISLAND. Imports only contracts + kernel; never imports the missions
island or the application layer. Exposes the CatalogSource seam (fixture- or HTTP-backed).
"""

from __future__ import annotations

from xorcise.core.catalog._fixture import FREE_LIBRARY
from xorcise.core.catalog.http import HttpCatalogSource
from xorcise.core.catalog.source import (
    CatalogSource,
    LibraryItem,
    PullToken,
    StubCatalogSource,
)

__all__ = [
    "FREE_LIBRARY",
    "CatalogSource",
    "HttpCatalogSource",
    "LibraryItem",
    "PullToken",
    "StubCatalogSource",
]
