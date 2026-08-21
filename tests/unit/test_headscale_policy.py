import json

import pytest

from xorcise.core.headscale.policy import RunNetwork, assert_policy_safe, render_policy

ORCH = "orchestrator"
TAG = "tag:router"


def _net(user: str, *cidrs: str) -> RunNetwork:
    return RunNetwork(agent_user=user, auth_key="k", entry_cidrs=tuple(cidrs))


def test_render_is_valid_json_with_expected_structure():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    doc = json.loads(text)
    assert doc["tagOwners"] == {TAG: [f"{ORCH}@"]}
    assert doc["autoApprovers"]["routes"] == {"10.200.1.0/24": [TAG]}
    assert {"action": "accept", "src": [f"{ORCH}@"], "dst": [f"{ORCH}@:*"]} in doc["acls"]
    assert {"action": "accept", "src": ["agent-1@"], "dst": ["10.200.1.0/24:*"]} in doc["acls"]
    # ...and the inbound rule back to the agent, which every run gets
    assert {"action": "accept", "src": [TAG], "dst": ["agent-1@:*"]} in doc["acls"]
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
        assert_policy_safe(evil, [])


def test_safe_rejects_missing_run_rule():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy([], router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError):
        assert_policy_safe(text, nets, router_tag=TAG)


def test_safe_rejects_missing_cidr():
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy([_net("agent-1", "10.200.9.0/24")], router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError):
        assert_policy_safe(text, nets)


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
        assert_policy_safe(bad, [_net("run-a-agent", "10.9.0.0/24")], collector_addr="172.17.0.1")


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
        assert_policy_safe(crafted, [_net("run-a-agent", "10.9.0.0/24")])


# --- agent ingress -----------------------------------------------------------------------------


def test_safe_refuses_to_certify_without_a_router_tag():
    """Fail closed. The inbound rule's safety rests entirely on its source being pinned to THIS
    run's router, so a policy that cannot be checked against that tag must not be certified."""
    nets = [_net("agent-1", "10.200.1.0/24")]
    text = render_policy(nets, router_tag=TAG, orchestrator_user=ORCH)
    with pytest.raises(ValueError, match="router_tag"):
        assert_policy_safe(text, nets)


def test_safe_rejects_a_missing_inbound_rule():
    nets = [_net("agent-1", "10.200.1.0/24")]
    doc = json.loads(render_policy(nets, router_tag=TAG, orchestrator_user=ORCH))
    doc["acls"] = [r for r in doc["acls"] if r.get("src") != [TAG]]
    with pytest.raises(ValueError, match="exactly one inbound rule"):
        assert_policy_safe(json.dumps(doc), nets, router_tag=TAG)


def test_safe_rejects_a_router_sourced_dst_for_a_foreign_agent():
    """A run's router must never be handed a path to another run's agent."""
    nets = [_net("agent-1", "10.200.1.0/24")]
    doc = json.loads(render_policy(nets, router_tag=TAG, orchestrator_user=ORCH))
    doc["acls"].append({"action": "accept", "src": [TAG], "dst": ["agent-2@:*"]})
    with pytest.raises(ValueError, match="unexpected router-sourced dst"):
        assert_policy_safe(json.dumps(doc), nets, router_tag=TAG)


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
