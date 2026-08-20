"""Pure ACL (HuJSON) rendering + safety asserts for the per-run fence.

PART-ISLAND, pure: builds the policy as a Python structure and serializes it (JSON
is valid HuJSON), so there is no template-file I/O and no jinja2 dependency. The
safety invariants are the hard gate run before any policy is applied:
- never emit an allow-all rule;
- each active run maps to exactly one rule for its advertised (entry) subnet(s).
Lifted/rewritten from the networking PoC (runner/policy.py + templates/acl.hujson.j2).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RunNetwork:
    """One active run's fence facts: its agent user, minted keys, and entry subnet(s).

    Two distinct pre-auth keys: `auth_key` for the AGENT node (joins as agent_user, untagged —
    the mission prompt hands this to the agent) and `router_key` for the run's Tailscale ROUTER
    node (joins tagged router_tag so the autoApprovers policy approves its advertised routes —
    the runner hands this to the fused container)."""

    agent_user: str
    auth_key: str
    entry_cidrs: tuple[str, ...]
    router_key: str = ""
    # Callback missions need the TARGET to open connections back to the agent (the mission
    # registers an address the target dials). Without a rule in that direction the agent node's
    # packet filter is empty and every inbound SYN is dropped ("no rules matched"). Opt-in per
    # mission: a mission that never calls back gets no inbound rule at all.
    agent_ingress: bool = False


_FORBIDDEN = ('"*:*"', '"src": ["*"]', "autogroup:members")


def render_policy(
    networks: Sequence[RunNetwork],
    *,
    router_tag: str,
    orchestrator_user: str,
    collector_addr: str = "",
    collector_port: int = 4318,
) -> str:
    """Render the full HuJSON ACL from the active run set (deterministic, sorted)."""
    ordered = sorted(networks, key=lambda n: n.agent_user)

    routes: dict[str, list[str]] = {}
    for net in ordered:
        for cidr in net.entry_cidrs:
            routes.setdefault(cidr, [router_tag])
    if collector_addr:
        routes.setdefault(f"{collector_addr}/32", [router_tag])

    collector_dst = f"{collector_addr}:{collector_port}" if collector_addr else None

    acls: list[dict[str, object]] = [
        {"action": "accept", "src": [f"{orchestrator_user}@"], "dst": [f"{orchestrator_user}@:*"]}
    ]
    for net in ordered:
        dst = [f"{c}:*" for c in net.entry_cidrs]
        if collector_dst:
            dst.append(collector_dst)
        acls.append(
            {
                "action": "accept",
                "src": [f"{net.agent_user}@"],
                "dst": dst,
            }
        )
        if net.agent_ingress:
            # The reverse direction, scoped to this run's router as the ONLY source. Ports are
            # deliberately wildcard: the agent is a host on the mission network and a mission may
            # legitimately expect it to listen anywhere (a callback API, a shell, a C2 port), so
            # the port policy belongs to the mission, not to the harness. What keeps this safe is
            # the source pin, not a port list.
            acls.append(
                {
                    "action": "accept",
                    "src": [router_tag],
                    "dst": [f"{net.agent_user}@:*"],
                }
            )

    policy = {
        "tagOwners": {router_tag: [f"{orchestrator_user}@"]},
        "autoApprovers": {"routes": dict(sorted(routes.items()))},
        "acls": acls,
    }
    return json.dumps(policy, indent=2)


def assert_policy_safe(
    policy_text: str,
    networks: Sequence[RunNetwork],
    *,
    router_tag: str = "",
    collector_addr: str = "",
    collector_port: int = 4318,
) -> None:
    """Defensive gate before a policy is ever applied. Raises ValueError on violation."""
    lowered = policy_text.lower()
    for token in _FORBIDDEN:
        if token.lower() in lowered:
            raise ValueError(f"Refusing to apply unsafe ACL policy: contains {token!r}")
    for net in networks:
        needle = f'"{net.agent_user}@"'
        if policy_text.count(needle) != 1:
            raise ValueError(f"Policy must contain exactly one rule for {net.agent_user}@")
        for cidr in net.entry_cidrs:
            if cidr not in policy_text:
                raise ValueError(f"Policy missing CIDR {cidr} for {net.agent_user}@")

    pol = json.loads(policy_text)
    collector_dst_str = f"{collector_addr}:{collector_port}" if collector_addr else None
    allowed_extra: set[str] = {collector_dst_str} if collector_dst_str else set()
    for net in networks:
        agent_rule = next(
            (r for r in pol.get("acls", []) if r.get("src") == [f"{net.agent_user}@"]),
            None,
        )
        if agent_rule is None:
            raise ValueError(f"Policy missing an ACL rule for {net.agent_user}@")
        allowed = {f"{c}:*" for c in net.entry_cidrs} | allowed_extra
        for dst in agent_rule.get("dst", []):
            if dst not in allowed:
                raise ValueError(f"Policy contains foreign dst {dst!r} for {net.agent_user}@")

    # Inbound (agent-ingress) rules. The invariant is NOT "no wildcard port" — ports are
    # deliberately wildcard (see render_policy). It is that every router-sourced rule names THIS
    # run's router tag as its only source and THIS run's agent user as its only destination, so a
    # run's router can never be handed a path to another run's agent.
    ingress_dsts = {f"{n.agent_user}@:*" for n in networks if n.agent_ingress}
    for net in networks:
        if not net.agent_ingress:
            continue
        if not router_tag:
            raise ValueError(
                f"{net.agent_user}@ requests agent ingress but no router_tag was supplied to "
                "assert_policy_safe — refusing to certify an unverifiable inbound rule"
            )
        wanted = [f"{net.agent_user}@:*"]
        matches = [
            r
            for r in pol.get("acls", [])
            if r.get("src") == [router_tag] and r.get("dst") == wanted
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Policy must contain exactly one inbound rule {router_tag} -> {wanted[0]!r} "
                f"for {net.agent_user}@ (found {len(matches)})"
            )
    if router_tag:
        for rule in pol.get("acls", []):
            if rule.get("src") != [router_tag]:
                continue
            for dst in rule.get("dst", []):
                if dst not in ingress_dsts:
                    raise ValueError(f"Policy contains unexpected router-sourced dst {dst!r}")
