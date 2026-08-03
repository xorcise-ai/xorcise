"""Catalog browse wire DTO (LEAF). Imports nothing internal.

A CatalogEntry is one row in the browse view: either a local installed mission
(`source="your_own"`) or a free-library mission (`source="library"`). The free
library hosts prebuilt fused OCI images, so a library entry carries
its `image` ref; `installed` marks whether the local store already has this id."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CatalogEntry(_Frozen):
    source: Literal["your_own", "library"]
    mission_id: str  # == MissionManifest.metadata.mission_id / MissionRef.mission_id
    name: str
    summary: str = ""  # USER-facing browse blurb; never the agent objective
    proficiency: str | None = None
    specialty: str | None = None
    type: str | None = None
    skills: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    installed: bool = False
    image: str | None = None


class CatalogStatus(_Frozen):
    """Free-library reachability. `connected`/`error` come from the source;
    `disconnected` means the catalog is disabled (no `catalog_url`). `last_sync` is set
    only by the real HttpCatalogSource; the stub leaves it None (nothing synced yet)."""

    state: Literal["connected", "error", "disconnected"]
    message: str | None = None
    last_sync: str | None = None
