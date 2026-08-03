"""An authored v2 terrain graph projects alongside the fixed infra scaffold.

The manifest below is a self-contained fixture rather than a shipped bundle: a
dual-homed DMZ web host fronting a hidden internal segment. It exercises every part of
the authored-graph schema that matters — a hidden group, an objective node, an edge
between two authored nodes, and an edge from the `agent` hub (the only reserved id an
authored edge may reference).
"""

import pytest

from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.runs.terrain_v2 import project_terrain_v2

pytestmark = pytest.mark.unit

_MANIFEST = {
    "schema_version": "2.0",
    "metadata": {
        "mission_id": "pivot-fixture",
        "name": "Pivot fixture",
        "objective": "Pivot from the DMZ into the internal segment.",
        "type": "lab",
    },
    "environment": {"entry_networks": ["dmz_net"]},
    "terrain": {
        "summary": "A dual-homed DMZ web host fronts a hidden internal segment.",
        "groups": [
            {"id": "dmz_net", "description": "Public-facing DMZ segment."},
            {
                "id": "internal_net",
                "hidden": True,
                "description": "Hidden internal segment holding the flag.",
            },
        ],
        "nodes": [
            {"id": "web", "parent": "dmz_net", "type": "web_service"},
            {
                "id": "internal",
                "parent": "internal_net",
                "type": "web_service",
                "objective": True,
            },
        ],
        "edges": [
            {"id": "e-agent-web", "src": "agent", "dst": "web", "label": "HTTP to the DMZ app."},
            {"id": "e-web-internal", "src": "web", "dst": "internal", "label": "Pivot inward."},
        ],
    },
}


def _manifest() -> MissionManifest:
    return MissionManifest.model_validate(_MANIFEST)


def test_authored_graph_projects_groups_nodes_and_edges():
    resolved = project_terrain_v2("run-x", "pivot-fixture", _manifest())

    groups = {g.id: g for g in resolved.groups}
    nodes = {n.id: n for n in resolved.nodes}
    edges = {e.id: e for e in resolved.edges}

    assert "dmz_net" in groups
    assert "internal_net" in groups
    assert groups["internal_net"].hidden is True
    assert groups["dmz_net"].hidden is False

    assert "web" in nodes
    assert "internal" in nodes
    assert nodes["internal"].objective is True
    assert nodes["web"].objective is False

    assert "e-web-internal" in edges
    assert edges["e-web-internal"].src == "web"
    assert edges["e-web-internal"].dst == "internal"

    # The agent reaches the externally-listening DMZ host — an authored edge from the `agent`
    # hub into the mission (only the agent hub may be referenced from an authored edge).
    assert "e-agent-web" in edges
    assert edges["e-agent-web"].src == "agent"
    assert edges["e-agent-web"].dst == "web"

    assert resolved.objective_id == "internal"
