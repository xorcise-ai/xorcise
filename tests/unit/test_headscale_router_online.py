"""Router-online probe: is the run's per-run subnet router actually ON the tailnet?

The readiness gate's second half. The mission stack can be up while the router that advertises its
CIDR never joined — the agent then joins the tailnet, gets an IP, and still cannot reach any target
(the observed failure). The router joins as the shared orchestrator user, so it is addressable only
by its run-derived node NAME, not via the per-user probe.
"""

from __future__ import annotations

from xorcise.core.headscale import NetworkController, StubHeadscaleCli
from xorcise.core.headscale.cli import parse_node_online_by_name
from xorcise.core.orchestration.clients.headscale_client import HeadscaleFenceClient
from xorcise.core.orchestration.ports import NetworkFencePort


def test_parse_node_online_by_name_matches_name_or_given_name():
    nodes = [
        {"name": "run-1-router", "online": True},
        {"given_name": "run-2-router", "online": False},
    ]
    assert parse_node_online_by_name(nodes, "run-1-router") is True
    assert parse_node_online_by_name(nodes, "run-2-router") is False


def test_parse_node_online_by_name_is_false_for_absent_and_malformed():
    # Never raises: an absent node, a non-mapping entry, or a missing "online" key is just offline.
    assert parse_node_online_by_name([], "run-1-router") is False
    assert parse_node_online_by_name(["junk", {"name": "run-1-router"}], "run-1-router") is False


def test_controller_probes_the_run_derived_router_node_name():
    # The router node name convention ({run_id}-router) is owned by the controller — the same one
    # teardown deletes by — so callers never re-derive it.
    cli = StubHeadscaleCli()
    controller = NetworkController(cli, router_tag="tag:router", orchestrator_user="orchestrator")
    assert controller.router_online("run-1") is False
    cli.online_nodes.add("run-1-router")
    assert controller.router_online("run-1") is True


def test_fence_client_exposes_router_online():
    cli = StubHeadscaleCli()
    fence = HeadscaleFenceClient(
        NetworkController(cli, router_tag="tag:router", orchestrator_user="orchestrator")
    )
    assert fence.router_online("run-9") is False
    cli.online_nodes.add("run-9-router")
    assert fence.router_online("run-9") is True


def test_fence_port_default_is_true_so_existing_fences_never_block_readiness():
    # router_online is NON-abstract with a permissive default: a fence that cannot report (stub /
    # in-process test doubles) must degrade to "assume ready", never wedge every run at PENDING.
    class _MinimalFence(NetworkFencePort):
        def create_run_network(self, run_id, agent_user, entry_cidrs): ...
        def teardown_run_network(self, run_id): ...
        def reconcile_acl(self): ...

    assert _MinimalFence().router_online("any-run") is True
