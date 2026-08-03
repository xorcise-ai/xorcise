import pytest

from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.runs.terrain_v2 import known_element_ids, project_terrain_v2

pytestmark = pytest.mark.unit


def _manifest(terrain: TerrainSpec | None, static_ips=None) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c", name="c", objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",), static_ips=static_ips or {}),
        terrain=terrain,
    )


# --- infra-id namespace guard (M2) -------------------------------------------------


def test_authored_node_colliding_with_reserved_infra_id_is_skipped():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=(
            {"id": "collector", "parent": "dmz", "label": "Evil collector"},
            {"id": "web", "parent": "dmz"},
        ),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    nids = [n.id for n in t.nodes]
    assert nids.count("collector") == 1  # no duplicate: only the infra scaffold's survives
    collector = next(n for n in t.nodes if n.id == "collector")
    assert collector.group == "xorcise"  # the infra one, not the authored "dmz" impostor
    assert "web" in nids  # sibling authored node is unaffected


def test_authored_group_colliding_with_reserved_infra_id_is_skipped():
    spec = TerrainSpec(
        groups=({"id": "xorcise", "description": "authored impostor"},),
        nodes=({"id": "web", "parent": "xorcise"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    gids = [g.id for g in t.groups]
    assert gids.count("xorcise") == 1  # no duplicate group
    xorcise_group = next(g for g in t.groups if g.id == "xorcise")
    assert xorcise_group.kind == "infra"  # the infra group wins; authored dupe dropped
    # "web"'s parent="xorcise" resolves to the surviving infra group -> not dropped by M3
    assert any(n.id == "web" for n in t.nodes)


def test_authored_edge_colliding_with_reserved_infra_id_is_skipped():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=({"id": "web", "parent": "dmz"}, {"id": "db", "parent": "dmz"}),
        edges=({"id": "hs:join", "src": "web", "dst": "db"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    assert not any(e.id == "hs:join" and e.src == "web" for e in t.edges)


def test_static_ips_network_colliding_with_reserved_infra_id_is_skipped():
    t = project_terrain_v2("r", "c", _manifest(None, static_ips={"web": {"rc": 10}}))
    rc_groups = [g for g in t.groups if g.id == "rc"]
    assert rc_groups == []  # "rc" is a reserved node id; must not become a static-ips group
    # the service itself is unplaceable now that its only network was rejected -> skipped too
    assert not any(n.id == "web" for n in t.nodes)


def test_static_ips_service_colliding_with_reserved_infra_id_is_skipped():
    t = project_terrain_v2("r", "c", _manifest(None, static_ips={"collector": {"dmz": 10}}))
    assert [n for n in t.nodes if n.id == "collector"][0].group == "xorcise"  # infra one only
    assert "dmz" in {g.id for g in t.groups}  # the network group itself is fine


# --- referential validation (M3) ---------------------------------------------------


def test_authored_node_with_unknown_parent_is_dropped():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=(
            {"id": "web", "parent": "dmz"},
            {"id": "ghost", "parent": "nonexistent"},
        ),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    nids = {n.id for n in t.nodes}
    assert "web" in nids
    assert "ghost" not in nids


def test_authored_node_parent_may_be_an_infra_group():
    spec = TerrainSpec(
        groups=(),
        nodes=({"id": "web", "parent": "xorcise"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    assert any(n.id == "web" and n.group == "xorcise" for n in t.nodes)


def test_authored_edge_with_unknown_dst_is_dropped():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=({"id": "web", "parent": "dmz"},),
        edges=(
            {"id": "e1", "src": "web", "dst": "dmz"},  # valid: dst is a known group
            {"id": "e2", "src": "web", "dst": "nowhere"},  # dangling
        ),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    eids = {e.id for e in t.edges}
    assert "e1" in eids
    assert "e2" not in eids


def test_authored_edge_with_unknown_src_is_dropped():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=({"id": "web", "parent": "dmz"},),
        edges=({"id": "e3", "src": "nowhere", "dst": "web"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    assert not any(e.id == "e3" for e in t.edges)


def test_authored_edge_referencing_dropped_node_cascades():
    # "ghost" is dropped by M3 (unknown parent); an edge that targets it must cascade-drop too.
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=(
            {"id": "web", "parent": "dmz"},
            {"id": "ghost", "parent": "nonexistent"},
        ),
        edges=({"id": "e4", "src": "web", "dst": "ghost"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    assert not any(e.id == "e4" for e in t.edges)


# --- known_element_ids ---------------------------------------------------------------


def test_known_element_ids_returns_full_union_including_infra_ids():
    spec = TerrainSpec(
        groups=({"id": "dmz"},),
        nodes=({"id": "web", "parent": "dmz", "objective": True},),
        edges=({"id": "e1", "src": "web", "dst": "dmz"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    ids = known_element_ids(t)
    assert {"agent", "xorcise", "hs", "rc", "collector", "hs:join", "rc:done"} <= ids
    assert {"dmz", "web", "e1"} <= ids
    expected = {g.id for g in t.groups} | {n.id for n in t.nodes} | {e.id for e in t.edges}
    assert ids == expected


def test_known_element_ids_on_bare_infra_scaffold():
    t = project_terrain_v2("r", "c", None)
    ids = known_element_ids(t)
    assert {"agent", "xorcise", "hs", "rc", "collector"} <= ids
    assert ids == {g.id for g in t.groups} | {n.id for n in t.nodes} | {e.id for e in t.edges}
