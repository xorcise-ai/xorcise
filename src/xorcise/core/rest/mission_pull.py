"""Mission pull spine (delivery/rest layer).

Acquires a free-library mission as a prebuilt fused image: local-store-first
(`driver.image_exists`) → `driver.pull` → record the catalog manifest + image ref via
`missions.install_pulled` (no builder; the library path is NOT the Your-Own ingest
path). Coordinates three part-islands (catalog + runner + missions) from
the delivery layer, the same reasoning as `rest/run_create.py` / `rest/catalog_view.py`.

Backs both callers: the explicit pull (REST/CLI) and run-create's auto-pull-on-run.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xorcise.core.config import Settings
from xorcise.core.contracts.control import (
    InstalledBaseIdentity,
    InstalledImageIdentity,
    MissionInstallIdentity,
    MissionRef,
)
from xorcise.core.contracts.errors import (
    MissionNotInCatalogError,
    NotFoundError,
    PullCancelled,
    PullError,
)

if TYPE_CHECKING:
    # Type-only: keep the part-islands off the control-plane boot path (role isolation),
    # same trick as run_create.py. Concrete island imports stay lazy, inside the functions.
    from xorcise.core.catalog.source import CatalogSource, MissionDetail
    from xorcise.core.contracts.mission import MissionManifest
    from xorcise.core.missions.runtime import InstalledMission
    from xorcise.core.runner.docker import DockerDriver, PullProgress


# PullError / MissionNotInCatalogError now live in contracts/errors.py (LEAF) so the catalog
# island can raise them without importing this delivery module. Re-exported here (via
# __all__, for strict mypy no_implicit_reexport) so existing call sites keep importing them here.
__all__ = [
    "MissionNotInCatalogError",
    "PullCancelled",
    "PHASE_EXTRACTING_IMAGE",
    "PHASE_PREPARING_IMAGE",
    "PHASE_PULLING_IMAGE",
    "PullDeps",
    "PullError",
    "PullProgressSink",
    "build_pull_deps",
    "pull_mission",
]

# Progress callback for a pull: (phase, bytes_current, bytes_total). Phases:
# resolving → preparing_image → pulling_image → extracting_image → downloading_bundle →
# installing. Byte counts cover the image download (pulling_image) or its extraction
# (extracting_image); totals GROW as docker discovers layers, so consumers must tolerate
# non-monotonic percent.
PullProgressSink = Callable[[str, int, int], None]

# Image phases. `preparing_image` covers everything before the first byte moves — docker's opening
# negotiation and layers already in the local store, which emit no byte events at all. Naming it
# separately is what stops a cached or negotiating pull from being displayed as a stalled download.
PHASE_PREPARING_IMAGE = "preparing_image"
PHASE_PULLING_IMAGE = "pulling_image"
PHASE_EXTRACTING_IMAGE = "extracting_image"


def _pull_phase(
    layers: dict[str, tuple[int, int]], extracted: dict[str, tuple[int, int]]
) -> tuple[str, int, int]:
    """Derive (phase, current, total) from the per-layer download/extraction tallies.

    Download wins while it is still incomplete, so interleaved Extracting events (docker unpacks
    one layer while others download) cannot flip the phase back and forth. Once the bytes are all
    in, extraction owns the readout — that is the long tail on a multi-GB image. With neither
    tally populated the pull is preparing: negotiating, or finding every layer already local.
    """
    down_cur = sum(c for c, _ in layers.values())
    down_tot = sum(t for _, t in layers.values())
    if layers and down_cur < down_tot:
        return PHASE_PULLING_IMAGE, down_cur, down_tot
    if extracted:
        return (
            PHASE_EXTRACTING_IMAGE,
            sum(c for c, _ in extracted.values()),
            sum(t for _, t in extracted.values()),
        )
    if layers:
        return PHASE_PULLING_IMAGE, down_cur, down_tot
    return PHASE_PREPARING_IMAGE, 0, 0


@dataclass(frozen=True)
class PullDeps:
    source: CatalogSource
    driver: DockerDriver
    install_root: Path


def _use_real_docker(settings: Settings) -> bool:
    """Real Docker iff this process owns the Docker plane and stubs aren't forced."""
    return not settings.use_stubs and settings.role in {"all", "runner"}


def _real_docker_driver() -> DockerDriver:
    """Construct the real DockerSdkDriver, failing loud (not stubbing) when Docker is absent.

    Two distinct failures get two distinct remediations: a missing `runner` extra (the
    `docker` SDK) vs. an unreachable daemon. We probe the SDK with find_spec rather than catching
    the driver-module import — the SDK import is lazy inside DockerSdkDriver.__init__, so the
    module imports cleanly without the extra and an ImportError-on-import never fires."""
    if importlib.util.find_spec("docker") is None:  # docker SDK: a BASE dependency
        raise RuntimeError(
            "the Docker SDK (docker>=7.0) is missing — this XORCISE install is incomplete. "
            "Reinstall it: pip install --force-reinstall xorcise "
            "(check you are in the right virtualenv: python -c 'import docker')"
        )
    from xorcise.core.config import get_settings
    from xorcise.core.runner.docker.driver import DockerSdkDriver

    try:
        # pin the pull/run platform (default linux/amd64) so amd64-only mission images
        # resolve on an arm64 host; overridable via XORCISE_DOCKER_PLATFORM.
        return DockerSdkDriver(platform=get_settings().docker_platform)
    except Exception as exc:  # daemon unreachable / docker.from_env failure
        raise RuntimeError(
            "Docker daemon is not reachable — start Docker or set XORCISE_USE_STUBS=1"
        ) from exc


def build_pull_deps(settings: Settings, *, use_docker: bool | None = None) -> PullDeps:
    """Wire the pull deps. The catalog source is the real HTTP client when `catalog_url` is set,
    else the offline stub — no silent stub fallback when a remote is configured. The
    driver defaults to REAL on a Docker-owning role: `use_docker=None` derives from
    settings (role + use_stubs); pass an explicit bool to override (tests inject False)."""
    from xorcise.core.rest.catalog_view import build_catalog_source
    from xorcise.core.runner.docker import StubDockerDriver

    real = _use_real_docker(settings) if use_docker is None else use_docker
    driver = _real_docker_driver() if real else StubDockerDriver()
    return PullDeps(
        source=build_catalog_source(settings),
        driver=driver,
        install_root=Path(settings.missions_root),
    )


def pull_mission(
    mission_id: str,
    deps: PullDeps,
    *,
    progress: PullProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
    precheck: Callable[[], None] | None = None,
) -> InstalledMission:
    """Acquire + install a library mission. Idempotent; failure leaves it not-installed.

    `progress` (keyword-only, default None ⇒ backward-compatible for run-create's auto-pull)
    receives (phase, bytes_current, bytes_total) as the pull advances.

    `should_cancel` (keyword-only, default None ⇒ never cancels — run-create's auto-pull is not
    cancellable) is polled at every phase boundary AND inside the per-layer download callback; a
    True result raises PullCancelled to abort the download promptly. Cancel is only honoured up to
    the install step (install_pulled is atomic and the sole writer): a cancel checked before it
    leaves the mission cleanly not-installed; once install begins it runs to completion.

    `precheck` (keyword-only, default None) runs after the cheap manifest fetch identifies a LAB
    mission but BEFORE the multi-GB image download — run-create wires the nesting gate here so a
    host that cannot run the mission is refused without first paying the pull. The explicit
    `xorcise mission pull` passes None: pre-staging a mission on a non-nesting host is allowed."""
    from xorcise.core.missions import get_installed, install_pulled
    from xorcise.core.missions.errors import MissionCollisionError

    def report(phase: str, current: int = 0, total: int = 0) -> None:
        if progress is not None:
            progress(phase, current, total)

    def abort_if_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise PullCancelled(f"pull of '{mission_id}' was cancelled")

    existing = get_installed(mission_id, deps.install_root)
    if existing is not None:
        # a mission_id belongs to one source. A your_own install of this id must not be
        # silently returned as if it were the library mission — that would hand back the wrong
        # mission. Only a library-origin install is the idempotent no-op the pull expects.
        if existing.origin != "library":
            raise MissionCollisionError(
                f"mission_id '{mission_id}' is already installed as your own mission; "
                f"pulling the library version would collide. Remove your local copy first."
            )
        return existing  # already pulled (docker-pull on a present image is a no-op)

    report("resolving")
    abort_if_cancelled()
    try:
        detail = deps.source.fetch_detail(mission_id)
    except NotFoundError as exc:
        raise MissionNotInCatalogError(
            f"mission '{mission_id}' is not installed and not in the catalog"
        ) from exc
    manifest = detail.manifest

    # None ⇒ an attachment-only (static) mission: no image to pull. MissionRef.image is a
    # plain str, so record "" — the run path branches on the manifest (installed.is_static) and
    # never reads this ref for a static run, so the empty image is inert.
    image = _image_for(deps.source, mission_id)
    ref = MissionRef(mission_id=mission_id, image=image or "")
    if image is not None and not deps.driver.image_exists(image):
        # A lab mission with a download ahead of it — gate now (nesting), before the pull.
        if precheck is not None:
            precheck()
        token = deps.source.pull_token(mission_id)  # None ⇒ image needs no registry auth
        report(PHASE_PREPARING_IMAGE)
        # Aggregate docker's per-layer events into running totals. The sum of layer totals
        # GROWS as layers are discovered mid-pull — consumers must tolerate a growing total.
        #
        # Download and extraction are tracked SEPARATELY, and the reported phase is derived from
        # which one is live, because a docker pull spends long stretches doing neither. Counting
        # only "Downloading" (the previous behaviour) meant three healthy situations were
        # indistinguishable from a hung pull, all reported as "Downloading… 0 B":
        #   - every layer already in the local store ("Already exists", zero byte events);
        #   - the opening negotiation ("Pulling fs layer" / "Waiting");
        #   - extraction, which for a multi-GB image runs for minutes AFTER the last byte lands.
        # Measured on a live pull: 344 Downloading events but also 6 Extracting ones, all dropped;
        # a fully-cached pull emitted three status-only events and no byte events at all. An
        # operator watching a frozen 0 B readout cancelled a pull that was working fine.
        #
        # Extraction is kept out of the DOWNLOAD totals (it reuses the same layer ids with
        # extraction byte counts, which would make the download bar jump backwards) — it gets its
        # own phase and its own counts instead.
        layers: dict[str, tuple[int, int]] = {}
        extracted: dict[str, tuple[int, int]] = {}

        def on_layer(evt: PullProgress) -> None:
            if evt.status == "Downloading":
                layers[evt.layer_id] = (evt.current, evt.total)
            elif evt.status == "Extracting":
                extracted[evt.layer_id] = (evt.current, evt.total)
            phase, current, total = _pull_phase(layers, extracted)
            report(phase, current, total)
            # Cancel is checked on EVERY event, not just download ones: a cancel during extraction
            # or during a fully-cached pull previously had no checkpoint to land on and was not
            # honoured until the next phase boundary. Raising breaks docker's streaming pull loop.
            abort_if_cancelled()

        try:
            # The progress kwarg is forwarded when a sink is wired OR cancellation is possible:
            # pre-existing driver fakes/subclasses override pull() without it and must keep
            # working, but on_layer is also the per-event cancel checkpoint, so a cancellable
            # pull needs it wired even when the caller wants no progress reporting.
            kwargs: dict[str, Any] = {}
            if token is not None:
                kwargs["auth"] = (token.username, token.password)
            if progress is not None or should_cancel is not None:
                kwargs["progress"] = on_layer
            deps.driver.pull(image, **kwargs)  # real registry; nothing written yet on failure
        except PullCancelled:
            raise  # NOT a pull failure — let the worker record it as `cancelled`, not `error`
        except Exception as exc:
            raise PullError(f"could not pull {image}: {exc}") from exc

    # Attachments travel out-of-band in the delivery bundle, not the image: fetch +
    # integrity-check the zip so install_pulled can materialize the declared files. The cloud
    # fail-closes on materialization, so a mission that declares attachments must have a bundle.
    abort_if_cancelled()
    report("downloading_bundle")
    delivery_zip = _fetch_verified_delivery(deps.source, mission_id, manifest)
    abort_if_cancelled()  # last chance — install_pulled is atomic and runs to completion
    report("installing")
    return install_pulled(
        manifest=manifest,
        mission_ref=ref,
        install_root=deps.install_root,
        delivery_zip=delivery_zip,
        # Read AFTER the image pull so the inspect sees the local copy that actually landed.
        identity=_install_identity(detail, deps.driver, image),
    )


def _fetch_verified_delivery(
    source: CatalogSource, mission_id: str, manifest: MissionManifest
) -> bytes | None:
    """Return the integrity-checked delivery-bundle bytes, or None when the mission declares
    no attachments. Raises PullError on a missing bundle or a sha256 mismatch."""
    if not manifest.attachments:
        return None
    bundle = source.fetch_delivery(mission_id)
    if bundle is None:
        raise PullError(f"mission '{mission_id}' declares attachments but has no delivery bundle")
    if bundle.sha256 is not None:  # legacy rows may predate the stamp → can't verify, accept
        actual = hashlib.sha256(bundle.content).hexdigest()
        if actual != bundle.sha256:
            raise PullError(
                f"delivery bundle integrity check failed for '{mission_id}' "
                f"(expected {bundle.sha256}, got {actual})"
            )
    return bundle.content


def _install_identity(
    detail: MissionDetail, driver: DockerDriver, image: str | None
) -> MissionInstallIdentity | None:
    """The §30 identity slice for this install, or None from a pre-contract catalog.

    Values come from the catalog detail response — the identity of the CURRENT artifact, i.e.
    the one just pulled — plus a post-pull inspect of the local image for the platform that
    actually landed on this machine. Recorded once at install; run evidence copies it at create
    time and never re-resolves it (a floating tag must not be able to rewrite history)."""
    if (
        detail.mission_version is None
        and detail.index_digest is None
        and detail.content_hash is None
    ):
        return None  # pre-contract catalog (prod today): keep the record legacy-shaped
    platform = driver.image_platform(image) if image else None
    platform_digest = next((p.digest for p in detail.platforms if p.platform == platform), None)
    arch = platform.rsplit("/", 1)[-1] if platform else None
    mission_base = None
    if detail.mission_base_version is not None or detail.base_index_digest is not None:
        mission_base = InstalledBaseIdentity(
            version=detail.mission_base_version,
            index_digest=detail.base_index_digest,
            # Keyed by BARE architecture, scoped to the platforms this mission was fused for.
            platform_digest=detail.base_platform_digests.get(arch) if arch else None,
        )
    return MissionInstallIdentity(
        mission_version=detail.mission_version,
        mission_base_version=detail.mission_base_version,
        content_hash=detail.content_hash,
        image=InstalledImageIdentity(
            pull_ref=detail.pull_ref,
            release_ref=detail.release_ref or (image or None),
            index_digest=detail.index_digest,
            platform=platform,
            platform_digest=platform_digest,
        ),
        mission_base=mission_base,
        pulled_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _image_for(source: CatalogSource, mission_id: str) -> str | None:
    """The mission's OCI image, or None for an attachment-only (static) mission that has no
    image. Raises MissionNotInCatalogError only when the id is not in the catalog at all — a
    static mission is legitimately imageless (its files travel in the delivery bundle), so a
    present-but-imageless row must NOT be reported as "no image" (that broke static pulls)."""
    for item in source.list_library():
        if item.mission_id == mission_id:
            return item.image or None
    raise MissionNotInCatalogError(f"mission '{mission_id}' is not in the catalog")
