"""A mission_id belongs to one source; cross-source installs never clobber."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.missions import (
    MissionCollisionError,
    get_installed,
    ingest,
    install_pulled,
)
from xorcise.core.missions.builder import StubBundleBuilder

pytestmark = pytest.mark.unit


def _manifest(slug: str) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="o", type="lab"),
        environment=EnvironmentSpec(),
    )


def _write_bundle(root: Path, slug: str) -> Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "2.0",
        "metadata": {"mission_id": slug, "name": slug, "objective": "o", "type": "lab"},
        "environment": {"compose_file": "docker-compose.yml", "entry_networks": ["default"]},
    }
    (bundle / "mission.json").write_text(json.dumps(manifest))
    (bundle / "docker-compose.yml").write_text("services: {}\n")
    return bundle


def test_ingest_over_installed_library_raises_and_preserves_it(tmp_path: Path) -> None:
    """The ticket's exact repro: library installed, then a local ingest of the same id."""
    store = tmp_path / "store"
    install_pulled(
        manifest=_manifest("sqli-login"),
        mission_ref=MissionRef(mission_id="sqli-login", image="xorcise/lib:1"),
        install_root=store,
    )
    bundle = _write_bundle(tmp_path, "sqli-login")

    with pytest.raises(MissionCollisionError) as exc:
        ingest(bundle, builder=StubBundleBuilder(), install_root=store)

    assert "sqli-login" in str(exc.value) and "library" in str(exc.value)
    # No silent data loss: the library install is byte-for-byte intact.
    survivor = get_installed("sqli-login", store)
    assert survivor is not None
    assert survivor.origin == "library"
    assert survivor.mission_ref.image == "xorcise/lib:1"


def test_install_pulled_over_installed_your_own_raises(tmp_path: Path) -> None:
    """Reverse direction, exercising Layer 1 symmetry via install_pulled directly."""
    store = tmp_path / "store"
    bundle = _write_bundle(tmp_path, "dup")
    ingest(bundle, builder=StubBundleBuilder(), install_root=store)  # origin=your_own

    with pytest.raises(MissionCollisionError):
        install_pulled(
            manifest=_manifest("dup"),
            mission_ref=MissionRef(mission_id="dup", image="xorcise/lib:9"),
            install_root=store,
        )
    survivor = get_installed("dup", store)
    assert survivor is not None and survivor.origin == "your_own"


def _install_record(store: Path, slug: str, *, origin: str) -> None:
    d = store / slug
    d.mkdir(parents=True)
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    ic = InstalledMission(
        slug=slug,
        root=d,
        manifest=_manifest(slug),
        mission_ref=MissionRef(mission_id=slug, image=f"xorcise/{slug}:0"),
        origin=origin,  # type: ignore[arg-type]
    )
    (d / INSTALLED_FILE).write_text(ic.to_record())


def test_pull_over_installed_your_own_raises(tmp_path: Path) -> None:
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    store = tmp_path / "store"
    _install_record(store, "sqli-login", origin="your_own")
    deps = PullDeps(
        source=StubCatalogSource(enabled=True),
        driver=StubDockerDriver(),
        install_root=store,
    )
    with pytest.raises(MissionCollisionError):
        pull_mission("sqli-login", deps)


def test_pull_over_installed_library_is_idempotent(tmp_path: Path) -> None:
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.mission_pull import PullDeps, pull_mission
    from xorcise.core.runner.docker import StubDockerDriver

    store = tmp_path / "store"
    _install_record(store, "sqli-login", origin="library")
    deps = PullDeps(
        source=StubCatalogSource(enabled=True),
        driver=StubDockerDriver(),
        install_root=store,
    )
    ic = pull_mission("sqli-login", deps)  # already library → returns existing, no raise
    assert ic.origin == "library"


def test_guard_raises_when_fresh_id_is_in_library(tmp_path: Path) -> None:
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.ingest import guard_local_ingest_collision

    # StubCatalogSource(enabled=True) fixture library contains "sqli-login".
    with pytest.raises(MissionCollisionError):
        guard_local_ingest_collision(
            "sqli-login", install_root=tmp_path / "empty", source=StubCatalogSource(enabled=True)
        )


def test_guard_allows_id_absent_from_library(tmp_path: Path) -> None:
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.ingest import guard_local_ingest_collision

    guard_local_ingest_collision(  # no raise
        "brand-new-local-id",
        install_root=tmp_path / "empty",
        source=StubCatalogSource(enabled=True),
    )


def test_guard_allows_when_id_already_installed(tmp_path: Path) -> None:
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest.ingest import guard_local_ingest_collision

    store = tmp_path / "store"
    _install_record(store, "sqli-login", origin="your_own")
    guard_local_ingest_collision(  # already installed → Layer 1 owns the decision; no raise here
        "sqli-login", install_root=store, source=StubCatalogSource(enabled=True)
    )


def test_guard_degrades_when_catalog_probe_fails(tmp_path: Path) -> None:
    from xorcise.core.rest.ingest import guard_local_ingest_collision

    class _Boom:
        def list_library(self):
            raise RuntimeError("catalog unreachable")

    guard_local_ingest_collision(  # probe failure swallowed → no raise (Layer 1 still guards)
        "sqli-login",
        install_root=tmp_path / "empty",
        source=_Boom(),  # type: ignore[arg-type]
    )
