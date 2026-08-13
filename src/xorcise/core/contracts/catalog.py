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
    # What pulling this mission COSTS, so the browse card can say so before the user
    # commits. Two independent parts — the OCI image the runner pulls and the attachment
    # bundle fetched out-of-band — either of which may be absent (a static mission has no
    # image; a lab may declare no attachments). `download_size_bytes` is their sum.
    #
    # None means UNKNOWN, never zero: an installed row carries no size (the download is
    # already done) and a catalog that predates these fields serves none, so the UI must
    # render "size unknown" rather than a confident "0 B".
    #
    # These are COMPRESSED transfer bytes, not extracted disk footprint, and shared base
    # layers mean a real pull often transfers less — present them as "up to".
    image_size_bytes: int | None = None
    attachments_size_bytes: int | None = None
    download_size_bytes: int | None = None
    # Base-generation compatibility, so the browse card and detail page can warn BEFORE a run is
    # attempted — the incompatibility used to surface only as a failed run-create. Derived from
    # the image ref's `-baseN` generation vs what this XORCISE runs (rest layer fills it in).
    #
    # `compatible` is None when undeterminable (a local `:local` fuse carries no generation in its
    # tag) — the UI shows no warning, and the run-create gate still guards via the image label.
    # `base_major` is the artifact's base-generation MAJOR (the only component that gates
    # compatibility); `compat_hint` is a short remediation shown when `compatible` is False.
    #
    # Named `base_major`, NOT `base_version`: the upcoming remote versioning contract reserves the
    # public field `mission_base_version` for the full base SemVer (e.g. "2.4.1") and explicitly
    # discourages `base_version` as a competing public name. This int is just the compat major.
    base_major: int | None = None
    compatible: bool | None = None
    compat_hint: str | None = None


class CatalogStatus(_Frozen):
    """Free-library reachability. `connected`/`error` come from the source;
    `disconnected` means the catalog is disabled (no `catalog_url`). `last_sync` is set
    only by the real HttpCatalogSource; the stub leaves it None (nothing synced yet)."""

    state: Literal["connected", "error", "disconnected"]
    message: str | None = None
    last_sync: str | None = None
