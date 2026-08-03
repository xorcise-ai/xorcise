"""Catalog router — free-library reachability status."""

from __future__ import annotations

from fastapi import APIRouter

from xorcise.core.config import get_settings
from xorcise.core.contracts.catalog import CatalogStatus
from xorcise.core.rest.catalog_view import build_catalog_view_deps, catalog_status

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/status")
def status() -> CatalogStatus:
    """Whether the XORCISE.AI catalog is connected / error / disconnected."""
    return catalog_status(build_catalog_view_deps(get_settings()))
