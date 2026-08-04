"""Ingest a bundle into a run-ready, installed mission.

preflight -> builder.build (the seam; the real build is the runner's) -> ATOMIC install under
install_root/<slug>/. Any failure (preflight or build) leaves NO half-installed state.
Shipped/pulled/your-own bundles all use this one path. `install_root` is injected so this
part-island never imports the `home` domain module."""

from __future__ import annotations

import io
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions.builder import BundleBuilder
from xorcise.core.missions.errors import (
    AttachmentBundleError,
    MissionCollisionError,
    PreflightError,
)
from xorcise.core.missions.preflight import preflight
from xorcise.core.missions.runtime import (
    INSTALLED_FILE,
    InstalledMission,
    Origin,
    resolve_install_dir,
)


def _copytree(src: Path, dst: Path) -> None:
    """Wrap shutil.copytree so the return value is None (satisfies Callable[[Path], None])."""
    shutil.copytree(src, dst)


def _unpack_delivery(zip_bytes: bytes, manifest: MissionManifest) -> Callable[[Path], None]:
    """Populate staging with the mission's declared attachments from the delivery zip.

    Writes ONLY the members named by `manifest.attachments`, each to its declared `att.path`
    under staging — so runtime `get_attachment` (which reads `inst.root / att.path`) resolves,
    exactly as a locally-ingested bundle would. Extraction is ONE level: each member's raw bytes
    are written verbatim, so a zip-typed attachment (e.g. packet.zip) stays sealed rather than
    being recursively exploded. Missing/unsafe members fail closed (AttachmentBundleError) and
    _atomic_install rolls staging back."""

    def populate(staging: Path) -> None:
        staging.mkdir()
        root = staging.resolve()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            members = set(zf.namelist())
            for att in manifest.attachments:
                if att.path not in members:
                    raise AttachmentBundleError(
                        f"delivery bundle is missing declared attachment '{att.path}'"
                    )
                target = staging / att.path
                if not target.resolve().is_relative_to(root):  # zip-slip / traversal guard
                    raise AttachmentBundleError(f"unsafe attachment path '{att.path}'")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(att.path))

    return populate


def _atomic_install(
    *,
    slug: str,
    manifest: MissionManifest,
    mission_ref: MissionRef,
    install_root: Path,
    populate_staging: Callable[[Path], None],
    origin: Origin,
) -> InstalledMission:
    """Shared staging → atomic swap for both install paths.

    ``populate_staging(staging)`` is called AFTER leftovers are cleaned but BEFORE
    installed.json is written; it should create/populate the staging directory.
    Version/origin are read from any existing install BEFORE the swap so the read is safe."""
    install_root.mkdir(parents=True, exist_ok=True)
    # mission_id is third-party content (bundle/catalog manifest); a separator or '..' in it
    # would put final — and staging/backup with their rmtree cleanups — outside the install root.
    final = resolve_install_dir(slug, install_root)
    if final is None:
        raise PreflightError(
            f"metadata.mission_id {slug!r} is not a usable install name "
            "(must be a plain directory name: no separators, no '..')"
        )
    staging = install_root / f".{slug}.tmp"
    backup = install_root / f".{slug}.bak"

    # a mission_id belongs to one source. Read the existing record BEFORE we disturb
    # anything, so a cross-source collision leaves the prior install byte-for-byte intact (no
    # rename, no delete) — the hard no-data-loss guarantee. Same-origin re-install bumps version.
    existing = InstalledMission.from_root(final) if (final / INSTALLED_FILE).is_file() else None
    if existing is not None and existing.origin != origin:
        raise MissionCollisionError(
            f"mission_id '{slug}' is already installed from source '{existing.origin}'; "
            f"installing it from '{origin}' would overwrite a different mission. "
            f"Remove the existing install or use a different mission_id."
        )

    for leftover in (staging, backup):
        if leftover.exists():
            shutil.rmtree(leftover)
    version = existing.version + 1 if existing is not None else 1  # monotonic bump
    try:
        populate_staging(staging)
        record = InstalledMission(
            slug=slug,
            root=final,
            manifest=manifest,
            mission_ref=mission_ref,
            version=version,
            origin=origin,
        )
        (staging / INSTALLED_FILE).write_text(record.to_record())
        if final.exists():
            final.rename(backup)
        staging.rename(final)
        if backup.exists():
            shutil.rmtree(backup)
        return record
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def ingest(bundle_dir: Path, *, builder: BundleBuilder, install_root: Path) -> InstalledMission:
    manifest = preflight(bundle_dir)  # raises PreflightError; nothing written
    slug = manifest.metadata.mission_id
    if manifest.is_static:
        # A static mission has no runtime, so there is nothing to fuse — skip the builder seam and
        # install with an empty image ref (never deployed; sandbox_ref stays "" at run-create).
        mission_ref = MissionRef(mission_id=slug, image="")
    else:
        mission_ref = builder.build(bundle_dir, manifest)  # may raise; nothing written yet
    return _atomic_install(
        slug=slug,
        manifest=manifest,
        mission_ref=mission_ref,
        install_root=install_root,
        populate_staging=lambda staging: _copytree(bundle_dir, staging),
        origin="your_own",  # built locally from a bundle
    )


def install_pulled(
    *,
    manifest: MissionManifest,
    mission_ref: MissionRef,
    install_root: Path,
    delivery_zip: bytes | None = None,
) -> InstalledMission:
    """Record a pulled prebuilt-image mission — manifest from the catalog, no builder.

    The fused image is already built/pulled and the manifest comes from the catalog, so
    this skips preflight + the BundleBuilder seam (that path is Your-Own only).
    When the manifest declares attachments, `delivery_zip` (the materialized
    `mission.zip`, already integrity-checked by the delivery layer) is unpacked into the
    install so `get_attachment` can serve the files; the image carries the
    environment, not these companion files. Atomic: any failure leaves no half-installed state."""
    slug = manifest.metadata.mission_id
    if delivery_zip is not None and manifest.attachments:
        populate = _unpack_delivery(delivery_zip, manifest)
    else:
        populate = lambda staging: staging.mkdir()  # noqa: E731 - trivial staging create
    return _atomic_install(
        slug=slug,
        manifest=manifest,
        mission_ref=mission_ref,
        install_root=install_root,
        populate_staging=populate,
        origin="library",  # pulled from the remote XORCISE catalog
    )
