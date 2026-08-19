# tests/unit/test_install_identity.py
"""The §30 install-identity slice: recorded at pull, written to installed.json, surfaced
on the browse row. Contract: mission-versioning §30 (installed.json), API1 (list row)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core.catalog import LibraryItem, StubCatalogSource
from xorcise.core.catalog.source import MissionDetail, PlatformImage
from xorcise.core.contracts.control import (
    InstalledBaseIdentity,
    InstalledImageIdentity,
    MissionInstallIdentity,
    MissionRef,
)
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.missions import get_installed
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.rest.catalog_view import CatalogViewDeps, list_catalog
from xorcise.core.rest.mission_pull import PullDeps, pull_mission
from xorcise.core.runner.docker import StubDockerDriver

pytestmark = pytest.mark.unit

_HASH = "f401724435f303e76bae78b940f3b6166078878f3c76747b2b9a7738a5212a40"


def _manifest(slug: str = "c1") -> MissionManifest:
    return MissionManifest(
        schema_version="3.0",
        version="1.0.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="obj", type="lab"),
        environment=EnvironmentSpec(),
    )


def _identity() -> MissionInstallIdentity:
    return MissionInstallIdentity(
        mission_version="1.0.0",
        mission_base_version="2.0.0",
        content_hash=_HASH,
        image=InstalledImageIdentity(
            pull_ref="reg/xorcise/mis-c1:latest",
            release_ref="reg/xorcise/mis-c1:1.0.0-base2.0.0",
            index_digest="sha256:idx",
            platform="linux/arm64",
            platform_digest="sha256:arm",
        ),
        mission_base=InstalledBaseIdentity(
            version="2.0.0", index_digest="sha256:base", platform_digest="sha256:barm"
        ),
        pulled_at="2026-08-19T00:00:00+00:00",
    )


def _write(root: Path, slug: str, record: InstalledMission) -> Path:
    d = root / slug
    d.mkdir(parents=True)
    (d / INSTALLED_FILE).write_text(record.to_record())
    return d


# ── installed.json record shape ──────────────────────────────────────────────────────────────


def test_record_round_trips_the_identity(tmp_path: Path) -> None:
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="reg/xorcise/mis-c1:1.0.0-base2.0.0")
    d = _write(
        tmp_path,
        slug,
        InstalledMission(slug, tmp_path / slug, _manifest(), ref, identity=_identity()),
    )
    loaded = InstalledMission.from_root(d)
    assert loaded.identity == _identity()
    # convenience projections used by browse + run evidence
    assert loaded.mission_version == "1.0.0"
    assert loaded.mission_base_version == "2.0.0"
    assert loaded.index_digest == "sha256:idx"
    assert loaded.platform == "linux/arm64"


def test_record_lays_out_identity_at_top_level_per_contract(tmp_path: Path) -> None:
    # §30's recommended layout: the identity keys sit BESIDE the legacy keys, not nested
    # under some wrapper — a human or external tool reads the contract shape off disk.
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="img")
    d = _write(
        tmp_path,
        slug,
        InstalledMission(slug, tmp_path / slug, _manifest(), ref, identity=_identity()),
    )
    data = json.loads((d / INSTALLED_FILE).read_text())
    assert data["mission_version"] == "1.0.0"
    assert data["mission_base_version"] == "2.0.0"
    assert data["content_hash"] == _HASH
    assert data["image"]["index_digest"] == "sha256:idx"
    assert data["image"]["platform"] == "linux/arm64"
    assert data["mission_base"]["platform_digest"] == "sha256:barm"
    assert data["pulled_at"] == "2026-08-19T00:00:00+00:00"
    assert data["install_revision"] == 1


def test_record_without_identity_writes_no_identity_keys(tmp_path: Path) -> None:
    # A your_own fuse / pre-contract install keeps the legacy record shape — no null spam.
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="img")
    d = _write(tmp_path, slug, InstalledMission(slug, tmp_path / slug, _manifest(), ref))
    data = json.loads((d / INSTALLED_FILE).read_text())
    assert "mission_version" not in data and "image" not in data
    loaded = InstalledMission.from_root(d)
    assert loaded.identity is None
    assert loaded.mission_version is None
    assert loaded.index_digest is None


def test_legacy_version_key_reads_as_install_revision(tmp_path: Path) -> None:
    # Records written before the rename carry the monotonic counter as "version".
    slug = "c1"
    d = tmp_path / slug
    d.mkdir(parents=True)
    legacy = {
        "version": 3,
        "origin": "library",
        "manifest": _manifest().model_dump(mode="json"),
        "mission_ref": {"mission_id": slug, "image": "img"},
    }
    (d / INSTALLED_FILE).write_text(json.dumps(legacy))
    loaded = InstalledMission.from_root(d)
    assert loaded.install_revision == 3
    assert loaded.identity is None


# ── pull records the identity ────────────────────────────────────────────────────────────────


class _ContractSource(StubCatalogSource):
    """Fixture library, but the detail response carries the contract identity siblings."""

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        return MissionDetail(
            manifest=self.fetch_manifest(mission_id),
            mission_version="1.0.0",
            mission_base_version="2.0.0",
            content_hash=_HASH,
            pull_ref="reg/xorcise/mis-sqli-login:latest",
            release_ref="reg/xorcise/mis-sqli-login:1.0.0-base2.0.0",
            index_digest="sha256:idx",
            platforms=(
                PlatformImage(os="linux", architecture="amd64", digest="sha256:amd"),
                PlatformImage(os="linux", architecture="arm64", digest="sha256:arm", variant="v8"),
            ),
            base_index_digest="sha256:base",
            base_platform_digests={"amd64": "sha256:bamd", "arm64": "sha256:barm"},
        )


class _ArmDriver(StubDockerDriver):
    def image_platform(self, image: str) -> str | None:
        return "linux/arm64"


def test_pull_records_the_install_identity(tmp_path: Path) -> None:
    deps = PullDeps(
        source=_ContractSource(enabled=True), driver=_ArmDriver(), install_root=tmp_path
    )
    ic = pull_mission("sqli-login", deps)
    assert ic.identity is not None
    assert ic.mission_version == "1.0.0"
    assert ic.identity.content_hash == _HASH
    img = ic.identity.image
    assert img is not None
    assert img.release_ref == "reg/xorcise/mis-sqli-login:1.0.0-base2.0.0"
    assert img.index_digest == "sha256:idx"
    # platform is what ACTUALLY landed (driver inspect), and the digests follow it
    assert img.platform == "linux/arm64"
    assert img.platform_digest == "sha256:arm"
    base = ic.identity.mission_base
    assert base is not None
    assert base.version == "2.0.0"
    assert base.platform_digest == "sha256:barm"  # bare-arch keyed upstream
    assert ic.identity.pulled_at  # stamped
    # and it survives the round trip through installed.json
    again = get_installed("sqli-login", tmp_path)
    assert again is not None and again.identity == ic.identity


def test_pull_from_pre_contract_source_records_no_identity(tmp_path: Path) -> None:
    # The stub's default fetch_detail carries no identity siblings (a 2.0-era deployment):
    # the record must stay legacy-shaped, exactly as before this feature.
    deps = PullDeps(
        source=StubCatalogSource(enabled=True), driver=StubDockerDriver(), install_root=tmp_path
    )
    ic = pull_mission("sqli-login", deps)
    assert ic.identity is None


# ── browse rows carry the identity ───────────────────────────────────────────────────────────


class _RowSource(StubCatalogSource):
    def list_library(self) -> tuple[LibraryItem, ...]:
        return (
            LibraryItem(
                mission_id="vp",
                name="Vanishing Point",
                image="reg/xorcise/mis-vp:1.4.2-base2.4.1",
                mission_version="1.4.2",
                mission_base_version="2.4.1",
                index_digest="sha256:cat",
                platforms=("linux/amd64", "linux/arm64"),
            ),
        )


def test_library_row_carries_catalog_identity(tmp_path: Path) -> None:
    deps = CatalogViewDeps(source=_RowSource(enabled=True), install_root=tmp_path)
    row = next(e for e in list_catalog(deps) if e.mission_id == "vp")
    assert row.mission_version == "1.4.2"
    assert row.mission_base_version == "2.4.1"
    assert row.index_digest == "sha256:cat"
    assert row.platforms == ("linux/amd64", "linux/arm64")


def test_installed_row_carries_recorded_identity(tmp_path: Path) -> None:
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="reg/xorcise/mis-c1:1.0.0-base2.0.0")
    _write(
        tmp_path,
        slug,
        InstalledMission(
            slug, tmp_path / slug, _manifest(), ref, origin="library", identity=_identity()
        ),
    )
    deps = CatalogViewDeps(source=StubCatalogSource(enabled=False), install_root=tmp_path)
    row = next(e for e in list_catalog(deps) if e.mission_id == slug)
    assert row.mission_version == "1.0.0"
    assert row.mission_base_version == "2.0.0"
    assert row.index_digest == "sha256:idx"
    assert row.platforms == ()  # an install records ONE platform; the offer is the library's
