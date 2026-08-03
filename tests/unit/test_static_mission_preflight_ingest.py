"""Preflight + ingest for static (attachment-only) missions (static-mission-support).

A static bundle has no environment and no docker-compose.yml; preflight must accept it (checking
only that its declared attachment files exist) and ingest must install it WITHOUT invoking the
fused-image builder. The lab path is unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions.builder import StubBundleBuilder
from xorcise.core.missions.errors import PreflightError
from xorcise.core.missions.ingest import ingest
from xorcise.core.missions.preflight import preflight

_STATIC_META = {
    "mission_id": "static-chal",
    "name": "Static",
    "objective": "solve",
    "type": "static",
}
_LAB_META = {"mission_id": "lab-chal", "name": "Lab", "objective": "pwn", "type": "lab"}
_ATT = [{"name": "attachment.zip", "path": "attachment.zip", "media_type": "application/zip"}]


def _write(root: Path, manifest: dict[str, object], *, files: dict[str, str] | None = None) -> Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "mission.json").write_text(json.dumps(manifest))
    for name, body in (files or {}).items():
        (bundle / name).write_text(body)
    return bundle


class _BoomBuilder:
    """A builder that explodes if build() is ever called — proves static skips the fused build."""

    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef:
        raise AssertionError("FusedImageBuilder.build must not run for a static mission")


def test_static_bundle_preflights_without_compose(tmp_path: Path) -> None:
    bundle = _write(
        tmp_path,
        {"schema_version": "2.0", "metadata": _STATIC_META, "attachments": _ATT},
        files={"attachment.zip": "zipbytes"},
    )
    manifest = preflight(bundle)
    assert manifest.is_static and manifest.environment is None


def test_static_bundle_missing_attachment_file_raises(tmp_path: Path) -> None:
    bundle = _write(
        tmp_path, {"schema_version": "2.0", "metadata": _STATIC_META, "attachments": _ATT}
    )
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "attachment" in str(exc.value).lower()


def test_static_bundle_without_attachment_declaration_raises(tmp_path: Path) -> None:
    bundle = _write(tmp_path, {"schema_version": "2.0", "metadata": _STATIC_META})
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "attachment" in str(exc.value).lower()


def test_lab_bundle_missing_compose_still_raises(tmp_path: Path) -> None:
    bundle = _write(tmp_path, {"schema_version": "2.0", "metadata": _LAB_META, "environment": {}})
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "docker-compose.yml" in str(exc.value)


def test_static_ingest_skips_the_fused_build(tmp_path: Path) -> None:
    bundle = _write(
        tmp_path,
        {"schema_version": "2.0", "metadata": _STATIC_META, "attachments": _ATT},
        files={"attachment.zip": "zipbytes"},
    )
    installed = ingest(bundle, builder=_BoomBuilder(), install_root=tmp_path / "store")
    assert installed.manifest.is_static
    assert installed.mission_ref.image == ""  # no fused image for a static mission


def test_lab_ingest_still_calls_the_builder(tmp_path: Path) -> None:
    bundle = _write(
        tmp_path,
        {"schema_version": "2.0", "metadata": _LAB_META, "environment": {}},
        files={"docker-compose.yml": "services: {}\n"},
    )
    installed = ingest(bundle, builder=StubBundleBuilder(), install_root=tmp_path / "store")
    assert installed.manifest.is_lab
    assert "lab-chal" in installed.mission_ref.image  # StubBundleBuilder tagged it
