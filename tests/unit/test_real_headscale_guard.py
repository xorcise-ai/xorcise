"""Unit test for the real-Headscale test guard's pure detection."""

from __future__ import annotations

import json
import subprocess

from tests._helpers import agent_nodes, stray_agent_nodes


def test_agent_nodes_detects_agents_ignores_orchestrator_and_routers():
    nodes = [
        {"name": "r1-router", "user": {"name": "tagged-devices"}, "tags": ["tag:router"]},
        {"name": "orch-node", "user": {"name": "orchestrator"}, "tags": []},
        {"name": "agentnode", "user": {"name": "run-abc123-agent"}, "tags": []},
    ]
    assert agent_nodes(nodes, "orchestrator") == ["agentnode"]


def test_agent_nodes_empty_when_clean():
    nodes = [{"name": "r1-router", "user": {"name": "tagged-devices"}, "tags": ["tag:router"]}]
    assert agent_nodes(nodes, "orchestrator") == []


def test_stray_agent_nodes_parses_and_filters():
    nodes = [
        {"name": "r1-router", "user": {"name": "tagged-devices"}, "tags": ["tag:router"]},
        {"name": "agentnode", "user": {"name": "run-abc123-agent"}, "tags": []},
    ]

    def fake_runner(cmd, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(nodes))

    assert stray_agent_nodes("headscale", runner=fake_runner) == ["agentnode"]


def test_stray_agent_nodes_empty_when_control_plane_unreachable():
    def fake_runner(cmd, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="")

    assert stray_agent_nodes("headscale", runner=fake_runner) == []
