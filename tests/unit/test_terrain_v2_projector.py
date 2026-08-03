import pytest

from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.runs.terrain_v2 import project_terrain_v2

pytestmark = pytest.mark.unit


def _manifest(terrain: TerrainSpec | None, static_ips=None) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c", name="c", objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",), static_ips=static_ips or {}),
        terrain=terrain,
    )


def test_infra_scaffold_always_present():
    t = project_terrain_v2("r", "c", _manifest(None))
    gids = {g.id for g in t.groups}
    nids = {n.id for n in t.nodes}
    assert {"agent", "xorcise"} <= gids
    assert {"agent", "hs", "rc", "collector"} <= nids
    assert all(n.state == "defined" for n in t.nodes)
    # scaffold nodes/groups are never authored -> no conditions to surface
    assert all(n.discovery_condition is None and n.completion_condition is None for n in t.nodes)
    assert all(g.discovery_condition is None for g in t.groups)


def test_authored_edge_may_reference_the_agent_hub():
    # A mission may author an edge from the `agent` hub into an entry node (agent -> web) to
    # express "the agent reaches this externally-listening service". An edge to a NON-existent id
    # is still dropped (dangling-endpoint validation).
    spec = TerrainSpec(
        summary="entry",
        groups=({"id": "dmz", "description": "DMZ", "discovery_condition": "probe"},),
        nodes=(
            {"id": "web", "parent": "dmz", "type": "web_service", "discovery_condition": "reach"},
        ),
        edges=(
            {"id": "e-agent-web", "src": "agent", "dst": "web", "label": "HTTP :80"},
            {"id": "e-bogus", "src": "nope", "dst": "web", "label": "dangling"},
        ),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    eids = {e.id for e in t.edges}
    assert "e-agent-web" in eids  # agent hub is a valid authored-edge endpoint
    edge = next(e for e in t.edges if e.id == "e-agent-web")
    assert edge.src == "agent" and edge.dst == "web"
    assert "e-bogus" not in eids  # unknown endpoint id dropped


def test_authored_graph_projected_with_objective_and_hidden():
    spec = TerrainSpec(
        summary="pivot mission",
        groups=(
            {"id": "dmz", "description": "DMZ", "discovery_condition": "probe dmz"},
            {
                "id": "internal",
                "description": "hidden",
                "discovery_condition": "pivot",
                "hidden": True,
            },
        ),
        nodes=(
            {
                "id": "web",
                "parent": "dmz",
                "type": "web_service",
                "label": "Web",
                "discovery_condition": "reach",
                "completion_condition": "ssrf",
            },
            {
                "id": "internal_svc",
                "parent": "internal",
                "type": "web_service",
                "objective": True,
                "discovery_condition": "reach 8080",
                "completion_condition": "flag",
            },
        ),
        edges=({"id": "e1", "src": "web", "dst": "internal_svc", "label": "SSRF pivot"},),
    )
    t = project_terrain_v2("r", "c", _manifest(spec))
    g = {x.id: x for x in t.groups}
    n = {x.id: x for x in t.nodes}
    assert g["internal"].hidden is True and g["dmz"].hidden is False
    assert n["internal_svc"].objective is True and n["internal_svc"].group == "internal"
    assert t.objective_id == "internal_svc"
    assert any(e.id == "e1" and e.dst == "internal_svc" and e.active is False for e in t.edges)
    assert t.summary == "pivot mission"
    # authored discovery/completion conditions must surface onto the resolved v2 types
    assert g["dmz"].discovery_condition == "probe dmz"
    assert g["internal"].discovery_condition == "pivot"
    assert n["web"].discovery_condition == "reach"
    assert n["web"].completion_condition == "ssrf"
    assert n["internal_svc"].discovery_condition == "reach 8080"
    assert n["internal_svc"].completion_condition == "flag"


def test_static_ips_fallback_when_no_authored_terrain():
    t = project_terrain_v2(
        "r", "c", _manifest(None, static_ips={"web": {"dmz": 10}, "db": {"internal": 20}})
    )
    gids = {g.id for g in t.groups}
    nids = {n.id for n in t.nodes}
    assert {"dmz", "internal"} <= gids  # derived network groups
    assert {"web", "db"} <= nids  # derived service nodes
    assert all(not n.objective for n in t.nodes if n.group in {"dmz", "internal"})
    assert t.edges == () or all(e.src not in {"web", "db"} for e in t.edges)  # no authored edges
    # static_ips fallback carries no authored text -> no conditions on the derived elements
    fallback_groups = [gr for gr in t.groups if gr.id in {"dmz", "internal"}]
    fallback_nodes = [nd for nd in t.nodes if nd.id in {"web", "db"}]
    assert all(gr.discovery_condition is None for gr in fallback_groups)
    assert all(
        nd.discovery_condition is None and nd.completion_condition is None for nd in fallback_nodes
    )
