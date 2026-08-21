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
from dataclasses import dataclass, field

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
    # Pull cost, quoted by the catalog before anything is downloaded. See
    # contracts.catalog.CatalogEntry for the full semantics; None means unknown, not zero.
    image_size_bytes: int | None = None
    attachments_size_bytes: int | None = None
    download_size_bytes: int | None = None
    # Artifact identity summary (mission-versioning contract API1). All optional — a
    # pre-contract catalog (prod today) serves none of them and the row must still browse.
    mission_version: str | None = None  # creator SemVer, e.g. "1.4.2"
    mission_base_version: str | None = None  # base SemVer the artifact was fused on
    index_digest: str | None = None  # current OCI index digest (update detection)
    platforms: tuple[str, ...] = ()  # validated platforms, e.g. ("linux/amd64", "linux/arm64")


@dataclass(frozen=True)
class PlatformImage:
    """One validated platform child of a mission's OCI index (detail response entry).

    `variant` is present when the child manifest declares one (buildx stamps arm64 children
    "v8") — without it linux/arm64 and linux/arm64/v8 would be indistinguishable."""

    os: str
    architecture: str
    digest: str | None = None
    variant: str | None = None

    @property
    def platform(self) -> str:
        """The `os/architecture` form Docker speaks (variant deliberately excluded)."""
        return f"{self.os}/{self.architecture}"


@dataclass(frozen=True)
class MissionDetail:
    """GET /v1/missions/{id} — the manifest plus the current-delivery identity siblings.

    Everything beside `manifest` is optional: a pre-contract catalog (prod today) serves only
    the manifest and an image ref, and the client must degrade to exactly the old behaviour.
    `platform_digests` on the base is keyed by BARE architecture ("amd64") and scoped to the
    platforms this mission was actually fused for — deliberately different shapes upstream."""

    manifest: MissionManifest
    mission_version: str | None = None
    mission_base_version: str | None = None
    content_hash: str | None = None  # bare lowercase hex, never sha256:-prefixed
    pull_ref: str | None = None  # the moving :latest pointer — convenience, never identity
    release_ref: str | None = None  # immutable <mission-version>-base<base-version> ref
    index_digest: str | None = None
    platforms: tuple[PlatformImage, ...] = ()
    base_index_digest: str | None = None
    base_platform_digests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MissionBaseRelease:
    """GET /v1/mission-base — the currently promoted mission-base release (contract §27).

    Version visibility for settings/diagnostics; per-mission base identity comes from the
    detail response's mission_base block, NOT from here (the promoted base need not be the
    base a given mission was fused on)."""

    version: str  # e.g. "2.0.0"
    required_base_major: int | None = None
    ref: str | None = None  # ghcr.io/xorcise-ai/mission-base:<version>
    index_digest: str | None = None
    platforms: tuple[PlatformImage, ...] = ()


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

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        """The full current-delivery contract for one mission: manifest + artifact identity.

        Default wraps fetch_manifest with no identity siblings — the honest answer for a
        source that predates the versioning contract (the stub, or any 2.0-era deployment).
        Raises NotFoundError if the id is not in the catalog."""
        return MissionDetail(manifest=self.fetch_manifest(mission_id))

    def pull_token(self, mission_id: str) -> PullToken | None:
        """Registry creds to pull this mission's image. None ⇒ the image needs no auth
        (the stub's fixture images); the real HttpCatalogSource mints a scoped ECR token."""
        return None

    def mission_base(self) -> MissionBaseRelease | None:
        """The currently promoted mission-base release, or None when this source cannot say
        (the stub, a pre-contract deployment whose /v1/mission-base 404s, or a network
        failure). None means UNKNOWN — callers render nothing, never a fabricated version."""
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
