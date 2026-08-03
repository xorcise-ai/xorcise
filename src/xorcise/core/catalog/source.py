"""Catalog source seam (PART-ISLAND). Imports only contracts + kernel.

`CatalogSource` is the injected free-library driver — the stub-seam pattern used by
`headscale.HeadscaleCli` / `runner.DockerDriver`. The real `HttpCatalogSource` (hitting
the catalog API + image registry) is deferred behind this ABC; `StubCatalogSource` serves
a bundled fixture so browse works Docker-/network-free.

The free library hosts prebuilt fused OCI images, so a `LibraryItem`
carries its `image` ref — the controller pulls and runs that image; it does NOT
go through the ingest/builder path (that is reserved for Your Own custom missions)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from xorcise.core.contracts.catalog import CatalogStatus
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.contracts.mission import MissionManifest


@dataclass(frozen=True)
class LibraryItem:
    mission_id: str
    name: str
    summary: str = ""
    proficiency: str | None = None
    specialty: str | None = None
    type: str | None = None
    skills: tuple[str, ...] = ()
    technologies: tuple[str, ...] = ()
    image: str | None = None


@dataclass(frozen=True)
class PullToken:
    """Short-lived, single-repo ECR docker-login creds minted by the catalog's pull-token broker.

    Returned by HttpCatalogSource.pull_token so the delivery layer can `docker login` before
    pulling a private image; the client needs no static AWS credentials."""

    registry: str
    username: str
    password: str
    image_ref: str
    expires_at: str


@dataclass(frozen=True)
class DeliveryBundle:
    """The materialized attachment bundle (`mission.zip`) for a library mission.

    The fused image carries the mission *environment*, not the companion attachment files —
    those travel out-of-band in this zip. `content` is the raw zip bytes; `sha256` is the
    expected digest the delivery layer verifies before unpacking (None on legacy rows that
    predate the stamp); `delivery_version` is the projection version it was built with."""

    content: bytes
    sha256: str | None = None
    delivery_version: int | None = None


class CatalogSource(ABC):
    @abstractmethod
    def list_library(self) -> tuple[LibraryItem, ...]:
        """The free-library missions available to pull. Empty ⇒ nothing to browse."""

    @abstractmethod
    def status(self) -> CatalogStatus:
        """Reachability of the free library: connected / error / disconnected."""

    @abstractmethod
    def fetch_manifest(self, mission_id: str) -> MissionManifest:
        """The full mission.json for a library id (pulled alongside the image).

        Raises NotFoundError if the id is not in the catalog."""

    def pull_token(self, mission_id: str) -> PullToken | None:
        """Registry creds to pull this mission's image. None ⇒ the image needs no auth
        (the stub's fixture images); the real HttpCatalogSource mints a scoped ECR token."""
        return None

    def fetch_delivery(self, mission_id: str) -> DeliveryBundle | None:
        """The out-of-band attachment bundle for a library mission.

        None ⇒ no attachment bundle to fetch. The stub's fixtures declare no attachments,
        so the default is None; the real HttpCatalogSource downloads the materialized zip
        via GET /v1/missions/{id}/download."""
        return None


@dataclass(frozen=True)
class StubCatalogSource(CatalogSource):
    """Fixture-backed free library. Disabled (catalog_url empty) ⇒ () so the view degrades."""

    enabled: bool = True

    def list_library(self) -> tuple[LibraryItem, ...]:
        from xorcise.core.catalog._fixture import FREE_LIBRARY

        return FREE_LIBRARY if self.enabled else ()

    def status(self) -> CatalogStatus:
        if self.enabled:
            return CatalogStatus(state="connected")
        return CatalogStatus(
            state="disconnected", message="catalog disabled (no catalog_url configured)"
        )

    def fetch_manifest(self, mission_id: str) -> MissionManifest:
        from xorcise.core.catalog._fixture import FREE_LIBRARY_MANIFESTS

        manifest = FREE_LIBRARY_MANIFESTS.get(mission_id) if self.enabled else None
        if manifest is None:
            raise NotFoundError(mission_id)
        return manifest
