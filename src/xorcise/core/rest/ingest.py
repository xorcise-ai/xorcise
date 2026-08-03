"""Mission ingestion spine (delivery/rest layer).

The Your-Own acquisition path: preflight → build the fused image → atomic install. The
builder is **real** by default (`FusedImageBuilder`) on a Docker-owning role, the
`StubBundleBuilder` under `use_stubs`/a non-Docker role — reusing the
`_use_real_docker` selection. Distinct from the free-library image-pull path
(`mission_pull`); the two never cross.

Coordinates `missions` (ingest) + `runner` (FusedImageBuilder) from the delivery layer,
the same reasoning as `run_create.py` / `mission_pull.py`. Island imports stay lazy.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from xorcise.core.config import Settings
from xorcise.core.rest.mission_pull import _use_real_docker

if TYPE_CHECKING:
    from xorcise.core.catalog.source import CatalogSource
    from xorcise.core.missions import BundleBuilder, InstalledMission


def _real_bundle_builder() -> BundleBuilder:
    """Construct the real FusedImageBuilder, failing loud (not stubbing) when the
    host cannot build. A local build drives Docker via subprocess, so the binary —
    not a pip extra — is what has to be there."""
    import shutil

    if shutil.which("docker") is None:
        raise RuntimeError(
            "Local mission build needs the Docker CLI on PATH — install Docker, "
            "then confirm with 'xorcise doctor'"
        )
    try:
        from xorcise.core.runner.docker.build import FusedImageBuilder
    except ImportError as exc:  # PyYAML is a BASE dep — its absence means a broken install
        raise RuntimeError(
            "Local mission build is unavailable — this XORCISE install is incomplete. "
            "Reinstall it: pip install --force-reinstall xorcise"
        ) from exc
    return FusedImageBuilder()


def build_bundle_builder(settings: Settings) -> BundleBuilder:
    """Real FusedImageBuilder on a Docker-owning role; StubBundleBuilder under use_stubs."""
    from xorcise.core.missions import StubBundleBuilder

    return _real_bundle_builder() if _use_real_docker(settings) else StubBundleBuilder()


def guard_local_ingest_collision(
    mission_id: str, *, install_root: Path, source: CatalogSource
) -> None:
    """Block a FRESH your_own ingest whose id the remote library already owns.

    Best-effort and upstream of the build: only fires for an id not already installed (a
    re-ingest falls through to Layer 1 in _atomic_install), and only when the library is
    reachable and claims the id. A catalog probe failure is swallowed — the on-disk Layer 1
    guard remains the hard no-data-loss guarantee, so we degrade rather than block ingest."""
    from xorcise.core.missions import get_installed
    from xorcise.core.missions.errors import MissionCollisionError

    if get_installed(mission_id, install_root) is not None:
        return  # already installed → _atomic_install (Layer 1) decides bump vs. collision
    try:
        library_ids = {item.mission_id for item in source.list_library()}
    except Exception:  # noqa: BLE001 — catalog unreachable ⇒ degrade, don't block ingest
        return
    if mission_id in library_ids:
        raise MissionCollisionError(
            f"mission_id '{mission_id}' is already published in the XORCISE library; "
            f"rename your bundle's mission_id (a local ingest would shadow the remote mission)."
        )


def ingest_bundle(bundle_dir: Path, settings: Settings) -> InstalledMission:
    """Preflight → collision guard → build (real/stub per settings) → atomic install."""
    from xorcise.core.missions import ingest, preflight
    from xorcise.core.rest.catalog_view import build_catalog_source

    install_root = Path(settings.missions_root)
    # Preflight up front so we have the id for the catalog guard; ingest() re-preflights (cheap,
    # side-effect-free) — keeps the core ingest() contract untouched.
    manifest = preflight(bundle_dir)
    guard_local_ingest_collision(
        manifest.metadata.mission_id,
        install_root=install_root,
        source=build_catalog_source(settings),
    )
    return ingest(
        bundle_dir,
        builder=build_bundle_builder(settings),
        install_root=install_root,
    )
