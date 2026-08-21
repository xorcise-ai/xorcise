import json

import pytest

from xorcise.core.headscale.policy import (
    RunNetwork,
    assert_policy_safe,
    render_policy,
    router_tag_for,
)

ORCH = "orchestrator"
TAG = "tag:router"


def _net(user: str, *cidrs: str) -> RunNetwork:
    return RunNetwork(agent_user=user, auth_key="k", entry_cidrs=tuple(cidrs))


def test_render_is_valid_json_with_expected_structure():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    doc = json.loads(text)
    rtag = router_tag_for("agent-1", TAG)
    # both the base tag (owns the shared collector route) and this run's per-run tag are declared
    assert doc["tagOwners"] == {TAG: [f"{ORCH}@"], rtag: [f"{ORCH}@"]}
    # the run's own subnet is auto-approved for its per-run tag, NOT the shared base tag
    assert doc["autoApprovers"]["routes"] == {"10.200.1.0/24": [rtag]}
    assert {"action": "accept", "src": [f"{ORCH}@"], "dst": [f"{ORCH}@:*"]} in doc["acls"]
    assert {"action": "accept", "src": ["agent-1@"], "dst": ["10.200.1.0/24:*"]} in doc["acls"]
    # ...and the inbound rule back to the agent, sourced from THIS run's per-run router tag
    assert {"action": "accept", "src": [rtag], "dst": ["agent-1@:*"]} in doc["acls"]
    assert len(doc["acls"]) == 3


def test_render_is_deterministic_and_sorted():
    a = [_net("agent-2", "10.200.2.0/24"), _net("agent-1", "10.200.1.0/24")]
    b = list(reversed(a))
    assert render_policy(a, router_tag=TAG, orchestrator_user=ORCH) == render_policy(
        b, router_tag=TAG, orchestrator_user=ORCH
    )


def test_render_no_runs_has_baseline_only():
    doc = json.loads(render_policy([], router_tag=TAG, orchestrator_user=ORCH))
    assert doc["acls"] == [{"action": "accept", "src": [f"{ORCH}@"], "dst": [f"{ORCH}@:*"]}]
    assert doc["autoApprovers"]["routes"] == {}


def test_safe_passes_for_good_policy():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    assert_policy_safe(text, nets, router_tag=TAG)


@pytest.mark.parametrize(
    "evil", ['{"acls":[{"dst":["*:*"]}]}', '{"src": ["*"]}', '{"x":"autogroup:members"}']
)
def test_safe_rejects_allow_all_tokens(evil):
    with pytest.raises(ValueError):
        assert_policy_safe(evil, [], router_tag=TAG)


def test_safe_rejects_missing_run_rule():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy([], router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError):
        assert_policy_safe(text, nets, router_tag=TAG)


def test_safe_rejects_missing_cidr():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy([_net("agent-1", "10.200.9.0/24")], router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError):
        assert_policy_safe(text, nets, router_tag=TAG)


# ---------------------------------------------------------------------------
# Collector egress tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_collector_dst_added_to_each_agent_rule_and_autoapprovers():
    text = render_policy(
        [_net("run-a-agent", "10.9.0.0/24")],
        router_tag="tag:router",
        orchestrator_user="orchestrator",
        collector_addr="172.17.0.1",
        collector_port=4318,
    )
    pol = json.loads(text)
    agent_rule = next(r for r in pol["acls"] if r["src"] == ["run-a-agent@"])
    assert "10.9.0.0/24:*" in agent_rule["dst"]
    assert "172.17.0.1:4318" in agent_rule["dst"]
    assert "172.17.0.1/32" in pol["autoApprovers"]["routes"]


@pytest.mark.unit
def test_no_collector_addr_is_unchanged_behavior():
    text = render_policy(
        [_net("run-a-agent", "10.9.0.0/24")],
        router_tag="tag:router",
        orchestrator_user="orchestrator",
    )
    pol = json.loads(text)
    agent_rule = next(r for r in pol["acls"] if r["src"] == ["run-a-agent@"])
    assert agent_rule["dst"] == ["10.9.0.0/24:*"]
    assert "172.17.0.1/32" not in json.dumps(pol)


@pytest.mark.unit
def test_assert_policy_safe_accepts_collector_dst():
    net = _net("run-a-agent", "10.9.0.0/24")
    text = render_policy(
        [net],
        router_tag="tag:router",
        orchestrator_user="orchestrator",
        collector_addr="172.17.0.1",
    )
    assert_policy_safe(text, [net], router_tag="tag:router", collector_addr="172.17.0.1")
    pol = json.loads(text)
    agent_rule = next(r for r in pol["acls"] if r["src"] == ["run-a-agent@"])
    assert "10.9.0.0/24:*" in agent_rule["dst"]
    assert "172.17.0.1:4318" in agent_rule["dst"]


@pytest.mark.unit
def test_assert_policy_safe_rejects_a_foreign_dst():
    # hand-craft a policy that smuggles an extra dst onto the agent rule
    bad = json.dumps(
        {
            "tagOwners": {"tag:router": ["orchestrator@"]},
            "autoApprovers": {"routes": {"10.9.0.0/24": ["tag:router"]}},
            "acls": [
                {"action": "accept", "src": ["orchestrator@"], "dst": ["orchestrator@:*"]},
                {
                    "action": "accept",
                    "src": ["run-a-agent@"],
                    "dst": ["10.9.0.0/24:*", "8.8.8.8:53"],  # <-- foreign dst
                },
            ],
        }
    )
    with pytest.raises(ValueError):
        assert_policy_safe(
            bad, [_net("run-a-agent", "10.9.0.0/24")], router_tag=TAG, collector_addr="172.17.0.1"
        )


@pytest.mark.unit
def test_assert_policy_safe_raises_when_agent_rule_missing_from_acls():
    """Fix 1: JSON layer independently raises when the agent rule is absent from acls.

    The string count(needle) check fires first for any policy that truly omits the
    agent_user string, so this test constructs a policy where 'run-a-agent@' appears
    exactly once (in the src of a different, non-agent rule) so the string check passes
    but the JSON-parse layer finds no matching acl rule. Either layer raising ValueError
    is acceptable per the spec; this exercises the fail-closed invariant.
    """
    # Policy has exactly one occurrence of "run-a-agent@" — but in an unrelated rule's
    # dst field, so count(needle)==1 passes the string check while the JSON layer finds
    # no rule whose src == ["run-a-agent@"].
    crafted = json.dumps(
        {
            "tagOwners": {"tag:router": ["orchestrator@"]},
            "autoApprovers": {"routes": {"10.9.0.0/24": ["tag:router"]}},
            "acls": [
                {
                    "action": "accept",
                    "src": ["orchestrator@"],
                    # sneaks the agent user string into dst so count==1 but no agent rule
                    "dst": ["run-a-agent@:*"],
                },
            ],
        }
    )
    with pytest.raises(ValueError):
        assert_policy_safe(crafted, [_net("run-a-agent", "10.9.0.0/24")], router_tag=TAG)


# --- agent ingress -----------------------------------------------------------------------------


@pytest.mark.parametrize("nets", [[_net("agent-1", "10.200.1.0/24")], []])
def test_safe_refuses_to_certify_without_a_router_tag(nets):
    """Fail closed. The inbound rule's safety rests entirely on its source being pinned to THIS
    run's router, so a policy that cannot be checked against that tag must not be certified.

    The empty-`networks` case is the one that mattered: the gate used to sit INSIDE
    `for net in networks:`, so a zero-iteration loop skipped it — and skipped the foreign-dst sweep
    below with it — certifying a policy without having checked one router-sourced rule."""
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError, match="router_tag"):
        assert_policy_safe(text, nets, router_tag="")


def test_safe_rejects_a_missing_inbound_rule():
    nets = [_net("agent-1", "10.200.1.0/24")]
    doc = json.loads(render_policy(nets, router_tag=TAG, orchestrator_user=ORCH))
    rtag = router_tag_for("agent-1", TAG)
    doc["acls"] = [r for r in doc["acls"] if r.get("src") != [rtag]]
    with pytest.raises(ValueError, match="exactly one inbound rule"):
        assert_policy_safe(json.dumps(doc), nets, router_tag=TAG)


def test_safe_rejects_a_router_sourced_dst_for_a_foreign_agent():
    """A run's router must never be handed a path to another run's agent.

    Two shapes, both cross-run reachability: this run's per-run tag reaching a foreign agent, and
    the bare shared base tag reaching any agent (the pre-per-run-tag shape, where every router
    shared one identity)."""
    nets = [_net("agent-1", "10.200.1.0/24")]
    rtag = router_tag_for("agent-1", TAG)
    for foreign in ({"src": [rtag], "dst": ["agent-2@:*"]}, {"src": [TAG], "dst": ["agent-1@:*"]}):
        doc = json.loads(render_policy(nets, router_tag=TAG, orchestrator_user=ORCH))
        doc["acls"].append({"action": "accept", **foreign})
        with pytest.raises(ValueError, match="unexpected router-sourced dst"):
            assert_policy_safe(json.dumps(doc), nets, router_tag=TAG)


def test_safe_rejects_cross_run_reachability_in_a_two_run_policy():
    """The blocker: with a shared base tag, run A's inbound rule (src base tag) was matched by
    run B's router, because every router carried that one tag. Per-run tags make each inbound rule
    name exactly one router. This asserts a genuinely two-run rendered policy certifies, and that
    grafting A's tag onto B's agent is caught."""
    a = _net("run-aaaa1111-agent", "10.200.1.0/24")
    b = _net("run-bbbb2222-agent", "10.200.2.0/24")
    text = render_policy(
        [a, b], router_tag=TAG, orchestrator_user=ORCH, collector_addr="172.17.0.1"
    )
    assert_policy_safe(text, [a, b], router_tag=TAG, collector_addr="172.17.0.1")
    # every router-sourced rule names a per-run tag, never the bare base tag
    doc = json.loads(text)
    router_srcs = [r["src"][0] for r in doc["acls"] if r["src"][0].startswith(TAG)]
    assert TAG not in router_srcs
    assert set(router_srcs) == {
        router_tag_for(a.agent_user, TAG),
        router_tag_for(b.agent_user, TAG),
    }
    # A's router must not be allowed to reach B's agent
    cross = json.loads(text)
    cross["acls"].append(
        {
            "action": "accept",
            "src": [router_tag_for(a.agent_user, TAG)],
            "dst": [f"{b.agent_user}@:*"],
        }
    )
    with pytest.raises(ValueError, match="unexpected router-sourced dst"):
        assert_policy_safe(json.dumps(cross), [a, b], router_tag=TAG, collector_addr="172.17.0.1")


def test_inbound_dst_does_not_break_the_one_rule_per_agent_invariant():
    """Regression: assert_policy_safe counts occurrences of '"<user>@"' (quotes included).

    The inbound rule's dst is '"<user>@:*"', which must NOT match that needle — otherwise every
    rendered policy would trip the "exactly one rule per agent" check.
    """
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    assert text.count('"agent-1@"') == 1
    assert '"agent-1@:*"' in text
    assert_policy_safe(text, nets, router_tag=TAG)
