# tests/unit/test_mission_update.py
"""The ONE mission-update action (contract §34/§35): digest-driven detection on the browse
row, and an in-place atomic re-pull as the single user-facing operation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from xorcise.core.catalog import LibraryItem, StubCatalogSource
from xorcise.core.catalog.source import MissionDetail
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.missions import get_installed
from xorcise.core.missions.errors import MissionCollisionError
from xorcise.core.rest.catalog_view import CatalogViewDeps, list_catalog
from xorcise.core.rest.mission_pull import PullDeps, pull_mission, update_mission
from xorcise.core.runner.docker import StubDockerDriver

pytestmark = pytest.mark.unit

_IMAGE_V1 = "reg/xorcise/mis-sqli-login:1.0.0-base2.0.0"
_IMAGE_V2 = "reg/xorcise/mis-sqli-login:1.1.0-base2.0.0"


class _Catalog(StubCatalogSource):
    """Fixture library whose current artifact identity is adjustable per test."""

    # frozen dataclass parent → configure via class attributes on subclasses/instances
    image: str = _IMAGE_V1
    index_digest: str | None = "sha256:one"
    mission_version: str = "1.0.0"

    def list_library(self) -> tuple[LibraryItem, ...]:
        return (
            LibraryItem(
                mission_id="sqli-login",
                name="SQLi Login",
                image=self.image,
                mission_version=self.mission_version,
                mission_base_version="2.0.0",
                index_digest=self.index_digest,
            ),
        )

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        return MissionDetail(
            manifest=self.fetch_manifest(mission_id),
            mission_version=self.mission_version,
            mission_base_version="2.0.0",
            content_hash="ab" * 32,
            release_ref=self.image,
            index_digest=self.index_digest,
        )


def _deps(tmp_path: Path, source: StubCatalogSource) -> PullDeps:
    return PullDeps(source=source, driver=StubDockerDriver(), install_root=tmp_path)


def _v2(source_cls: type[_Catalog] = _Catalog) -> _Catalog:
    src = source_cls(enabled=True)
    object.__setattr__(src, "image", _IMAGE_V2)
    object.__setattr__(src, "index_digest", "sha256:two")
    object.__setattr__(src, "mission_version", "1.1.0")
    return src


# ── update_mission (the spine) ───────────────────────────────────────────────────────────────


def test_update_not_installed_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        update_mission("sqli-login", _deps(tmp_path, _Catalog(enabled=True)))


def test_update_your_own_install_is_a_collision(tmp_path: Path) -> None:
    # A your_own install updates by re-ingesting its bundle, never from the catalog.
    from xorcise.core.contracts.control import MissionRef
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    d = tmp_path / "sqli-login"
    d.mkdir(parents=True)
    manifest = _Catalog(enabled=True).fetch_manifest("sqli-login")
    ref = MissionRef(mission_id="sqli-login", image="xorcise/mission-sqli-login:local")
    (d / INSTALLED_FILE).write_text(
        InstalledMission("sqli-login", d, manifest, ref, origin="your_own").to_record()
    )
    with pytest.raises(MissionCollisionError):
        update_mission("sqli-login", _deps(tmp_path, _Catalog(enabled=True)))


def test_update_is_a_noop_when_digests_match(tmp_path: Path) -> None:
    src = _Catalog(enabled=True)
    pull_mission("sqli-login", _deps(tmp_path, src))
    ic, updated = update_mission("sqli-login", _deps(tmp_path, src))
    assert updated is False
    assert ic.install_revision == 1  # nothing re-installed


def test_update_reinstalls_when_the_catalog_moved(tmp_path: Path) -> None:
    pull_mission("sqli-login", _deps(tmp_path, _Catalog(enabled=True)))
    d = _deps(tmp_path, _v2())
    ic, updated = update_mission("sqli-login", d)
    assert updated is True
    assert ic.install_revision == 2  # atomic in-place re-install bumps the counter
    assert ic.mission_ref.image == _IMAGE_V2
    assert ic.index_digest == "sha256:two"
    assert ic.mission_version == "1.1.0"
    # the record on disk is the new one
    again = get_installed("sqli-login", tmp_path)
    assert again is not None and again.index_digest == "sha256:two"


def test_update_falls_back_to_the_release_ref_without_digests(tmp_path: Path) -> None:
    # A pre-digest catalog still MOVES the immutable release ref whenever anything changes.
    class _NoDigest(_Catalog):
        index_digest = None

    pull_mission("sqli-login", _deps(tmp_path, _NoDigest(enabled=True)))
    ic, updated = update_mission("sqli-login", _deps(tmp_path, _NoDigest(enabled=True)))
    assert updated is False  # same ref ⇒ current
    moved = _v2(_NoDigest)
    object.__setattr__(moved, "index_digest", None)
    ic, updated = update_mission("sqli-login", _deps(tmp_path, moved))
    assert updated is True
    assert ic.mission_ref.image == _IMAGE_V2


# ── browse-row detection (§34) ───────────────────────────────────────────────────────────────


def test_installed_row_flags_update_available(tmp_path: Path) -> None:
    pull_mission("sqli-login", _deps(tmp_path, _Catalog(enabled=True)))
    row = next(
        e
        for e in list_catalog(CatalogViewDeps(source=_v2(), install_root=tmp_path))
        if e.mission_id == "sqli-login"
    )
    assert row.update_available is True
    assert row.mission_version == "1.0.0"  # what THIS install recorded
    assert row.current_mission_version == "1.1.0"  # what the catalog serves now
    assert row.installed is True


def test_installed_row_up_to_date_is_false_not_none(tmp_path: Path) -> None:
    src = _Catalog(enabled=True)
    pull_mission("sqli-login", _deps(tmp_path, src))
    row = next(
        e
        for e in list_catalog(CatalogViewDeps(source=src, install_root=tmp_path))
        if e.mission_id == "sqli-login"
    )
    assert row.update_available is False


def test_pre_contract_install_update_state_is_unknown(tmp_path: Path) -> None:
    # The plain stub records no identity AND its fixture image never moves: with digests on
    # neither side and an unchanged ref, ref-compare answers False (honestly current).
    # A your_own row, by contrast, has no upstream at all ⇒ None.
    src = StubCatalogSource(enabled=True)
    pull_mission(
        "sqli-login", PullDeps(source=src, driver=StubDockerDriver(), install_root=tmp_path)
    )
    row = next(
        e
        for e in list_catalog(CatalogViewDeps(source=src, install_root=tmp_path))
        if e.mission_id == "sqli-login"
    )
    assert row.update_available is False


# ── REST route ───────────────────────────────────────────────────────────────────────────────


def test_rest_update_unknown_mission_404(migrated_home) -> None:
    from fastapi.testclient import TestClient

    from xorcise.core.roles.boot.role_all import build_rest_app

    resp = TestClient(build_rest_app()).post("/api/missions/ghost/update")
    assert resp.status_code == 404


# ── CLI thin client ──────────────────────────────────────────────────────────────────────────


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", text))


def _cli_app() -> typer.Typer:
    import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
    from xorcise.core.cli._shared import app

    return app


def test_cli_mission_update_posts_to_the_rest_server(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(self, path: str) -> Any:  # noqa: ANN001 — test stub
        assert path == "/missions"
        return [{"mission_id": "sqli-login", "name": "SQLi Login", "installed": True}]

    def fake_post(self, path: str, json: Any, timeout: float | None = None) -> Any:  # noqa: ANN001
        captured["path"] = path
        return {"updated": True, "entry": {"name": "SQLi Login"}}

    monkeypatch.setattr("xorcise.core.cli.commands.mission.RestClient.get", fake_get)
    monkeypatch.setattr("xorcise.core.cli.commands.mission.RestClient.post", fake_post)
    result = CliRunner().invoke(_cli_app(), ["mission", "update", "sqli-login"])
    assert result.exit_code == 0, result.output
    assert captured["path"] == "/missions/sqli-login/update"
    assert "updated 'SQLi Login' (sqli-login)" in _plain(result.output)


def test_cli_mission_update_reports_already_current(monkeypatch) -> None:
    monkeypatch.setattr(
        "xorcise.core.cli.commands.mission.RestClient.get",
        lambda self, path: [{"mission_id": "sqli-login", "name": "SQLi Login", "installed": True}],
    )
    monkeypatch.setattr(
        "xorcise.core.cli.commands.mission.RestClient.post",
        lambda self, path, json, timeout=None: {"updated": False, "entry": {}},
    )
    result = CliRunner().invoke(_cli_app(), ["mission", "update", "sqli-login"])
    assert result.exit_code == 0, result.output
    assert "already up to date" in _plain(result.output)


def test_cli_mission_update_not_installed_fails_with_pull_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        "xorcise.core.cli.commands.mission.RestClient.get",
        lambda self, path: [{"mission_id": "sqli-login", "name": "SQLi Login", "installed": False}],
    )
    result = CliRunner().invoke(_cli_app(), ["mission", "update", "sqli-login"])
    assert result.exit_code == 1
    out = _plain(result.output + str(result.exception or ""))
    assert "not installed" in out
