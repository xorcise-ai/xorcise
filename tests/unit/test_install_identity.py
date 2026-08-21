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
    # An arm64 host whose registry serves what was asked: daemon_platform drives the native
    # selection, image_platform is the post-pull inspect the record and verification read.
    def daemon_platform(self) -> str | None:
        return "linux/arm64"

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


# ── platform surfacing for the UI (browse row `platform`/`emulated`, host exposure) ──────────


def test_installed_row_carries_platform_and_emulated_verdict(tmp_path: Path, monkeypatch) -> None:
    # The install pulled arm64; the daemon is amd64 ⇒ the row says which platform landed AND
    # that it executes under emulation here (server-computed — only the server sees the daemon).
    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.host_platform", lambda settings: "linux/amd64"
    )
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
    assert row.platform == "linux/arm64"
    assert row.emulated is True


def test_installed_row_platform_unknowns_stay_none(tmp_path: Path, monkeypatch) -> None:
    # No recorded platform (pre-contract install) and/or no daemon ⇒ None, never a guess.
    monkeypatch.setattr("xorcise.core.rest.docker_runtime.host_platform", lambda settings: None)
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="img")
    _write(tmp_path, slug, InstalledMission(slug, tmp_path / slug, _manifest(), ref))
    deps = CatalogViewDeps(source=StubCatalogSource(enabled=False), install_root=tmp_path)
    row = next(e for e in list_catalog(deps) if e.mission_id == slug)
    assert row.platform is None
    assert row.emulated is None


def test_installed_row_borrows_the_catalog_platform_offer(tmp_path: Path) -> None:
    # An install records ONE platform; the browse tags still need the catalog's current offer,
    # so an installed library row borrows `platforms` from its current catalog row.
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission

    class _Src(StubCatalogSource):
        def list_library(self) -> tuple[LibraryItem, ...]:
            return (
                LibraryItem(
                    mission_id="sqli-login",
                    name="SQLi Login",
                    image="xorcise/mission-sqli-login:1",
                    platforms=("linux/amd64", "linux/arm64"),
                ),
            )

    src = _Src(enabled=True)
    pull_mission(
        "sqli-login", PullDeps(source=src, driver=StubDockerDriver(), install_root=tmp_path)
    )
    row = next(
        e
        for e in list_catalog(CatalogViewDeps(source=src, install_root=tmp_path))
        if e.mission_id == "sqli-login"
    )
    assert row.installed is True
    assert row.platforms == ("linux/amd64", "linux/arm64")


def test_pre_record_install_falls_back_to_the_local_image_platform(
    tmp_path: Path, monkeypatch
) -> None:
    # An EXISTING installation (made before installed.json recorded a platform) must still get
    # the emulation verdict: the server inspects the local image — which IS what a run of this
    # install executes — instead of staying silent.
    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.host_platform", lambda settings: "linux/arm64"
    )
    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.local_image_platform",
        lambda settings, image: "linux/amd64" if image else None,
    )
    slug = "c1"
    ref = MissionRef(mission_id=slug, image="reg/xorcise/mis-c1:abc123-base2")
    _write(
        tmp_path,
        slug,
        InstalledMission(slug, tmp_path / slug, _manifest(), ref, origin="library"),
    )
    deps = CatalogViewDeps(source=StubCatalogSource(enabled=False), install_root=tmp_path)
    row = next(e for e in list_catalog(deps) if e.mission_id == slug)
    assert row.platform == "linux/amd64"  # read off the local image, not the (absent) record
    assert row.emulated is True  # → the run form's warning fires for this existing install


def test_recorded_platform_wins_over_the_image_inspect(tmp_path: Path, monkeypatch) -> None:
    # A §30 record is authoritative; the inspect is only the fallback for its absence.
    calls: list[str] = []

    def fake_inspect(settings, image):  # noqa: ANN001 — test stub
        calls.append(image)
        return "linux/amd64"

    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.host_platform", lambda settings: "linux/arm64"
    )
    monkeypatch.setattr("xorcise.core.rest.docker_runtime.local_image_platform", fake_inspect)
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
    assert row.platform == "linux/arm64"  # the record, not the inspect
    assert calls == []  # and the fallback never ran


# --- platform probes must not accept docker's empty-field output --------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("linux/amd64", "linux/amd64"),
        ("/", None),  # containerd snapshotter, foreign-arch local image: both fields empty
        ("linux/", None),
        ("/amd64", None),
        ("", None),
    ],
)
def test_local_image_platform_rejects_half_empty_output(monkeypatch, raw, expected):
    """`"/" in raw` accepted a bare "/" — docker exits 0 with both fields empty for a
    foreign-arch image under the containerd snapshotter. That surfaced to the operator as an
    architecture ("This install is /, not native ARM64"). Unknown must stay None."""
    from xorcise.core.config import Settings
    from xorcise.core.rest import docker_runtime

    docker_runtime.reset_host_platform_memo()

    class _Result:
        returncode = 0
        stdout = raw

    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.subprocess.run", lambda *a, **k: _Result()
    )
    assert docker_runtime.local_image_platform(Settings(use_stubs=False), "img:tag") == expected


@pytest.mark.parametrize(("raw", "expected"), [("linux/arm64", "linux/arm64"), ("/", None)])
def test_host_platform_rejects_half_empty_output(monkeypatch, raw, expected):
    """Same guard, same reasoning, on the host probe."""
    from xorcise.core.config import Settings
    from xorcise.core.rest import docker_runtime

    docker_runtime.reset_host_platform_memo()

    class _Result:
        returncode = 0
        stdout = raw

    monkeypatch.setattr(
        "xorcise.core.rest.docker_runtime.subprocess.run", lambda *a, **k: _Result()
    )
    assert docker_runtime.host_platform(Settings(use_stubs=False)) == expected
    docker_runtime.reset_host_platform_memo()
