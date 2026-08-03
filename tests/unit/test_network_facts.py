from __future__ import annotations

import pytest

from xorcise.core.headscale import RunNetwork
from xorcise.core.runs.observed import network_facts


@pytest.mark.unit
def test_network_facts_capture_boundary_not_secrets():
    # Build a realistic RunNetwork, then pass only its non-secret primitives to the helper —
    # runs/observed.py never imports the headscale part-island (the delivery caller extracts).
    rn = RunNetwork(
        agent_user="agent-r1",
        auth_key="tskey-auth-SECRET-do-not-record",
        entry_cidrs=("10.0.0.0/24", "10.1.0.0/24"),
        router_key="tskey-router-SECRET",
    )
    facts = network_facts("r1", agent_user=rn.agent_user, entry_cidrs=rn.entry_cidrs)

    by_name = {f.name: f for f in facts}
    assert by_name["entry-cidrs"].value == "10.0.0.0/24,10.1.0.0/24"
    assert by_name["entry-cidrs"].kind == "acl-config"
    assert "join" in by_name
    assert all(f.run_id == "r1" for f in facts)
    # the pre-auth keys are secrets and must NEVER appear in any recorded fact value
    assert all("tskey" not in f.value.lower() for f in facts)
