"""The rest catalog_view spine: assemble Your Own + the free library."""

from __future__ import annotations

from pathlib import Path

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission, Origin
from xorcise.core.rest.catalog_view import CatalogViewDeps, list_catalog


def _install(root: Path, slug: str, name: str, *, origin: Origin = "your_own") -> None:
    d = root / slug
    d.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=name, objective="obj", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (d / INSTALLED_FILE).write_text(
        InstalledMission(slug, d, manifest, ref, origin=origin).to_record()
    )


def _deps(root: Path, *, enabled: bool = True) -> CatalogViewDeps:
    return CatalogViewDeps(source=StubCatalogSource(enabled=enabled), install_root=root)


def test_lists_your_own_and_library(tmp_path: Path) -> None:
    _install(tmp_path, "myown", "My Own")
    entries = list_catalog(_deps(tmp_path))
    assert {e.source for e in entries} == {"your_own", "library"}
    own = next(e for e in entries if e.mission_id == "myown")
    assert own.source == "your_own" and own.installed is True
    lib = next(e for e in entries if e.source == "library")
    assert lib.installed is False and lib.image  # library entries carry their image ref


def test_library_item_already_installed_is_flagged_and_deduped(tmp_path: Path) -> None:
    _install(tmp_path, "sqli-login", "SQLi")  # same id as a fixture library item
    matches = [e for e in list_catalog(_deps(tmp_path)) if e.mission_id == "sqli-login"]
    assert len(matches) == 1
    assert matches[0].source == "your_own" and matches[0].installed is True


def test_pulled_library_mission_stays_under_library(tmp_path: Path) -> None:
    # A library mission you pulled must stay in the XORCISE Remote tab (installed), NOT move to
    # Your Own — Your Own is only locally-ingested bundles.
    _install(tmp_path, "sqli-login", "SQLi", origin="library")
    matches = [e for e in list_catalog(_deps(tmp_path)) if e.mission_id == "sqli-login"]
    assert len(matches) == 1
    assert matches[0].source == "library" and matches[0].installed is True


def test_catalog_disabled_degrades_to_your_own(tmp_path: Path) -> None:
    _install(tmp_path, "myown", "My Own")
    entries = list_catalog(_deps(tmp_path, enabled=False))
    assert {e.source for e in entries} == {"your_own"}


def test_empty_store_disabled_catalog_is_empty(tmp_path: Path) -> None:
    assert list_catalog(_deps(tmp_path, enabled=False)) == ()


def test_missing_install_root_does_not_break(tmp_path: Path) -> None:
    # store dir absent ⇒ no Your Own, library still lists (degrade, don't crash)
    entries = list_catalog(_deps(tmp_path / "absent", enabled=True))
    assert {e.source for e in entries} == {"library"}


def test_catalog_status_reflects_source(tmp_path: Path) -> None:
    from xorcise.core.rest.catalog_view import catalog_status

    assert catalog_status(_deps(tmp_path, enabled=True)).state == "connected"
    assert catalog_status(_deps(tmp_path, enabled=False)).state == "disconnected"


def test_catalog_status_maps_source_failure_to_error(tmp_path: Path) -> None:
    from xorcise.core.contracts.catalog import CatalogStatus
    from xorcise.core.rest.catalog_view import CatalogViewDeps, catalog_status

    class _Boom:
        def list_library(self) -> tuple[()]:
            return ()

        def status(self) -> CatalogStatus:
            raise RuntimeError("unreachable host")

    s = catalog_status(CatalogViewDeps(source=_Boom(), install_root=tmp_path))  # type: ignore[arg-type]
    assert s.state == "error" and "unreachable host" in (s.message or "")
