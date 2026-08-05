"""GET /api/missions/{id}/terrain — the mission's projected v2 base terrain.

The endpoint runs the run-scoped projector over the manifest with no run attached, so the
mission detail page can draw the ACTUAL terrain map (infra scaffold + authored mission
plane) before a run — or a pull — exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _install_with_terrain(home: Path, slug: str = "terr") -> None:
    """An installed mission whose manifest authors a two-node terrain with an objective."""
    root = Path(home) / "missions" / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
        terrain=TerrainSpec(
            summary="Reach the vault.",
            groups=({"id": "dmz", "label": "DMZ"},),
            nodes=(
                {"id": "web", "parent": "dmz", "label": "web"},
                {"id": "vault", "parent": "dmz", "label": "vault", "objective": True},
            ),
            edges=({"id": "e-agent-web", "src": "agent", "dst": "web"},),
        ),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def test_terrain_projects_scaffold_plus_authored_graph(migrated_home):
    _install_with_terrain(migrated_home)
    r = _client().get("/api/missions/terr/terrain")
    assert r.status_code == 200
    body = r.json()
    node_ids = {n["id"] for n in body["nodes"]}
    # The fixed infra scaffold AND the authored mission plane — the same graph a run starts from.
    assert {"agent", "collector", "web", "vault"} <= node_ids
    assert body["objective_id"] == "vault"
    assert body["summary"] == "Reach the vault."
    assert body["mission_id"] == "terr"
    # Mission-scoped: no run, nothing folded.
    assert body["run_id"] == ""
    assert body["updates"] == []
    assert {e["id"] for e in body["edges"]} >= {"e-agent-web"}


def test_terrain_for_library_mission_when_connected(migrated_home, monkeypatch):
    # Hermetic: the fixture library resolves the manifest, no live remote (mirrors the
    # manifest endpoint's test).
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest import catalog_view

    monkeypatch.setattr(
        catalog_view, "build_catalog_source", lambda settings: StubCatalogSource(enabled=True)
    )
    r = _client().get("/api/missions/sqli-login/terrain")
    assert r.status_code == 200
    body = r.json()
    assert body["mission_id"] == "sqli-login"
    # Even with no authored terrain the projector degrades to the infra scaffold, never 500s.
    assert {"agent", "collector"} <= {n["id"] for n in body["nodes"]}


def test_terrain_unknown_is_404(migrated_home):
    r = _client().get("/api/missions/does-not-exist/terrain")
    assert r.status_code == 404
