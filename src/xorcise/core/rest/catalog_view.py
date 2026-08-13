"""Catalog browse spine (delivery/rest layer).

Assembles **Your Own** (the local missions install store) + the **free XORCISE
library** (the catalog source) into a single list of `CatalogEntry` rows. Lives in
rest, not in either island, because it coordinates two mutually-non-importing
part-islands (`catalog` + `missions`); a delivery surface sits above the part layer
and may import both (.importlinter layers). Same pattern as `rest/run_create.py`.

Degrade, don't crash: a disabled/empty catalog still returns Your Own; a missing
install store still returns the library.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from xorcise.core.config import Settings
from xorcise.core.contracts.catalog import CatalogEntry, CatalogStatus
from xorcise.core.contracts.mission import MissionManifest

if TYPE_CHECKING:
    # Type-only: keep the catalog island off the control-plane boot path (role isolation),
    # same trick as run_create.py's ports import. The concrete import is lazy, below.
    from xorcise.core.catalog.source import CatalogSource
    from xorcise.core.runner.docker.build import BaseCompat


@dataclass(frozen=True)
class CatalogViewDeps:
    source: CatalogSource
    install_root: Path


def base_compat_of(image: str | None) -> BaseCompat:
    """Base-generation compatibility for a browse row, from its image ref (pure, no Docker).

    Shares build.base_compat with the run-create gate, so the card's verdict and the run's can
    never disagree. A ref with no `-baseN` generation (a local `:local` fuse) is undeterminable —
    no warning; the run-create gate still guards it via the image label."""
    from xorcise.core.runner.docker.build import base_compat, base_major_from_ref

    return base_compat(base_major_from_ref(image or ""))


def build_catalog_source(settings: Settings) -> CatalogSource:
    """The free-library source, shared by browse (here) and pull (`build_pull_deps`) so the two
    never disagree about what's pullable: the real HTTP client when the remote is
    enabled AND configured, else the disabled stub (empty library — no silent fixture fallback)."""
    from xorcise.core.catalog import HttpCatalogSource, StubCatalogSource

    if settings.catalog_enabled and settings.catalog_url:
        return HttpCatalogSource(settings.catalog_url)
    return StubCatalogSource(enabled=False)


def build_catalog_view_deps(settings: Settings) -> CatalogViewDeps:
    """Wire the browse deps. Switch off (or no url) ⇒ the library degrades to empty."""
    return CatalogViewDeps(
        source=build_catalog_source(settings),
        install_root=Path(settings.missions_root),
    )


def list_catalog(deps: CatalogViewDeps) -> tuple[CatalogEntry, ...]:
    """Your Own (installed local store) + the free library, deduped by id (Your Own wins)."""
    from xorcise.core.missions import get_installed, list_installed

    installed_entries: list[CatalogEntry] = []
    installed_ids: set[str] = set()
    for slug in list_installed(deps.install_root):
        ic = get_installed(slug, deps.install_root)
        if ic is None:
            continue
        meta = ic.manifest.metadata
        installed_ids.add(meta.mission_id)
        compat = base_compat_of(ic.mission_ref.image)
        installed_entries.append(
            CatalogEntry(
                # origin decides the tab: a pulled library mission stays under XORCISE Remote,
                # only locally-ingested bundles are Your Own.
                source=ic.origin,
                mission_id=meta.mission_id,
                name=meta.name,
                summary=meta.summary,
                proficiency=meta.proficiency,
                specialty=meta.specialty,
                type=meta.type,
                skills=meta.skills,
                technologies=meta.technologies,
                installed=True,
                image=ic.mission_ref.image,
                base_major=compat.base_major,
                compatible=compat.compatible,
                compat_hint=compat.hint,
            )
        )

    library: list[CatalogEntry] = []
    for item in deps.source.list_library():
        if item.mission_id in installed_ids:
            continue  # already an installed row (flagged there with its true origin)
        compat = base_compat_of(item.image)
        library.append(
            CatalogEntry(
                source="library",
                mission_id=item.mission_id,
                name=item.name,
                summary=item.summary,
                proficiency=item.proficiency,
                specialty=item.specialty,
                type=item.type,
                skills=item.skills,
                technologies=item.technologies,
                installed=False,
                image=item.image,
                # Only the library rows carry a size: these are the ones still facing a
                # download, which is the decision the number informs. An installed row
                # leaves them None — its bytes are already on disk, so quoting a download
                # there would answer a question nobody is asking.
                image_size_bytes=item.image_size_bytes,
                attachments_size_bytes=item.attachments_size_bytes,
                download_size_bytes=item.download_size_bytes,
                base_major=compat.base_major,
                compatible=compat.compatible,
                compat_hint=compat.hint,
            )
        )

    return tuple(installed_entries) + tuple(library)


def fetch_manifest(mission_id: str, deps: CatalogViewDeps) -> MissionManifest:
    """Full mission.json for an id: the installed copy if present, else the catalog source.

    Mirrors the remote `/v1/missions/{id}` shape (the manifest). Raises NotFoundError when
    neither Your Own nor the (connected) library has the id — the router maps that to 404.
    """
    from xorcise.core.missions import get_installed

    ic = get_installed(mission_id, deps.install_root)
    if ic is not None:
        return ic.manifest
    return deps.source.fetch_manifest(mission_id)  # raises NotFoundError if absent


def catalog_status(deps: CatalogViewDeps) -> CatalogStatus:
    """Reflect the source's reachability; an unexpected source failure maps to error."""
    try:
        return deps.source.status()
    except Exception as exc:  # real HttpCatalogSource probe failure → error pill, never a 500
        return CatalogStatus(state="error", message=str(exc))
