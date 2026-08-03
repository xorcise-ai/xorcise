import pytest

from xorcise.core.contracts.mission import TerrainSpec

pytestmark = pytest.mark.unit


def test_terrain_spec_accepts_authored_groups_and_edges():
    spec = TerrainSpec(
        summary="s",
        groups=(
            {"id": "dmz", "description": "d", "discovery_condition": "probe", "hidden": False},
        ),
        nodes=(
            {
                "id": "web",
                "parent": "dmz",
                "type": "web_service",
                "objective": True,
                "discovery_condition": "reach",
                "completion_condition": "own",
            },
        ),
        edges=({"id": "e1", "src": "web", "dst": "dmz", "label": "pivot"},),
    )
    assert spec.groups[0]["id"] == "dmz"
    assert spec.edges[0]["dst"] == "dmz"
    assert spec.nodes[0]["objective"] is True


def test_terrain_spec_groups_edges_default_empty():
    spec = TerrainSpec(summary="s")
    assert spec.groups == () and spec.edges == ()


def test_resolved_terrain_v2_defaults_and_shape():
    from xorcise.core.contracts.terrain import (
        ResolvedTerrainV2,
        TerrainEdgeV2,
        TerrainGroup,
        TerrainNodeV2,
        TerrainUpdate,
    )

    t = ResolvedTerrainV2(
        run_id="r",
        mission_id="c",
        groups=(TerrainGroup(id="dmz", label="dmz"),),
        nodes=(TerrainNodeV2(id="web", label="web", group="dmz", objective=True),),
        edges=(TerrainEdgeV2(id="e1", src="web", dst="dmz"),),
        updates=(TerrainUpdate(seq=0, target_kind="node", target_id="web", state="discovered"),),
    )
    assert t.nodes[0].state == "defined"  # base state; fold is client-side
    assert t.groups[0].discovered is False
    assert t.edges[0].active is False
    assert t.updates[0].state == "discovered"
    assert t.attribution is None
    # discovery/completion conditions default to None (unauthored) — the wire DTO still validates
    assert t.groups[0].discovery_condition is None
    assert t.nodes[0].discovery_condition is None and t.nodes[0].completion_condition is None
