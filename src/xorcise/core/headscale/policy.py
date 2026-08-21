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


_FORBIDDEN = ('"*:*"', '"src": ["*"]', "autogroup:members")


def router_tag_for(agent_user: str, base_tag: str) -> str:
    """The PER-RUN router tag: the base router tag namespaced by the run's agent user.

    The shared derivation between the minting side (the controller tags a run's router key with
    this) and the rendering side (render_policy scopes that run's inbound rule to this). It is the
    per-run scoping that keeps the inbound ACL honest: with every router carrying only the base
    `tag:router`, a rule `src:[tag:router] dst:[A-agent:*]` is matched by EVERY run's router, so
    B's router (or a mission that pivots through it) has a compiled path to A's agent on any port.
    Namespacing the tag per run makes the source pin name exactly one router — this run's.
    """
    return f"{base_tag}-{agent_user}"


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

    # A run's router carries TWO tags: its per-run tag (below), and the shared base `router_tag`.
    # The base tag exists only to auto-approve the ONE route that is identical across runs — the
    # collector /32 — so that logic stays run-independent. Every per-run subnet is approved for
    # just that run's tag, so a router can advertise only its own run's routes.
    routes: dict[str, list[str]] = {}
    for net in ordered:
        rtag = router_tag_for(net.agent_user, router_tag)
        for cidr in net.entry_cidrs:
            routes.setdefault(cidr, [rtag])
    if collector_addr:
        routes.setdefault(f"{collector_addr}/32", [router_tag])

    collector_dst = f"{collector_addr}:{collector_port}" if collector_addr else None

    acls: list[dict[str, object]] = [
        {"action": "accept", "src": [f"{orchestrator_user}@"], "dst": [f"{orchestrator_user}@:*"]}
    ]
    tag_owners: dict[str, list[str]] = {router_tag: [f"{orchestrator_user}@"]}
    for net in ordered:
        rtag = router_tag_for(net.agent_user, router_tag)
        tag_owners[rtag] = [f"{orchestrator_user}@"]
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
        # The reverse direction, scoped to THIS run's router as the ONLY source — its per-run tag,
        # not the shared base tag. The target has to be able to open connections back to the agent
        # — without a rule this way the agent node's packet filter is empty and every inbound SYN
        # is dropped ("no rules matched").
        #
        # Ports are deliberately wildcard: the agent is a host on the mission network and a
        # mission may legitimately expect it to listen anywhere (a callback API, a shell, a C2
        # port), so the port policy belongs to the mission, not to the harness. What keeps this
        # safe is the source pin — and the pin is now per-run, so no other run's router matches.
        acls.append(
            {
                "action": "accept",
                "src": [rtag],
                "dst": [f"{net.agent_user}@:*"],
            }
        )

    policy = {
        "tagOwners": dict(sorted(tag_owners.items())),
        "autoApprovers": {"routes": dict(sorted(routes.items()))},
        "acls": acls,
    }
    return json.dumps(policy, indent=2)


def assert_policy_safe(
    policy_text: str,
    networks: Sequence[RunNetwork],
    *,
    router_tag: str,
    collector_addr: str = "",
    collector_port: int = 4318,
) -> None:
    """Defensive gate before a policy is ever applied. Raises ValueError on violation."""
    # Hoisted ABOVE every loop, and required rather than defaulted. Both matter: as a
    # loop-invariant inside `for net in networks:` this never ran for an empty `networks`, so
    # `assert_policy_safe(text, [])` certified a policy without checking a single router-sourced
    # rule; and a `router_tag: str = ""` default let any future caller opt out of a gate whose
    # whole purpose is that it cannot be opted out of. `render_policy` already requires the tag,
    # so the two now agree.
    if not router_tag:
        raise ValueError(
            "cannot certify a policy's inbound rules — no router_tag was supplied to "
            "assert_policy_safe"
        )
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

    # Inbound rules. The invariant is NOT "no wildcard port" — ports are deliberately wildcard
    # (see render_policy). It is that every router-sourced rule names THIS run's PER-RUN router tag
    # as its only source and THIS run's agent user as its only destination. The per-run pin is what
    # the gate is really enforcing: a rule sourced from run A's router tag whose dst is run B's
    # agent would hand A's router a path to B's agent, which is the cross-run reachability the
    # shared base tag used to allow. Pairing each tag with its one permitted dst catches it; a
    # union of all agent dsts (the previous shape) did not, because every dst was "allowed" for
    # every tag.
    allowed_by_tag: dict[str, str] = {
        router_tag_for(n.agent_user, router_tag): f"{n.agent_user}@:*" for n in networks
    }
    for net in networks:
        rtag = router_tag_for(net.agent_user, router_tag)
        wanted = [f"{net.agent_user}@:*"]
        matches = [
            r for r in pol.get("acls", []) if r.get("src") == [rtag] and r.get("dst") == wanted
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Policy must contain exactly one inbound rule {rtag} -> {wanted[0]!r} "
                f"for {net.agent_user}@ (found {len(matches)})"
            )
    # Sweep EVERY router-sourced rule (any per-run router tag, or the bare base tag) and reject a
    # dst that is not the single agent that tag is allowed to reach. A rule sourced from the base
    # `tag:router` reaching any agent is itself a violation now — routers source inbound only from
    # their per-run tag.
    for rule in pol.get("acls", []):
        src = rule.get("src")
        if not (isinstance(src, list) and len(src) == 1 and str(src[0]).startswith(router_tag)):
            continue
        permitted = allowed_by_tag.get(str(src[0]))
        for dst in rule.get("dst", []):
            if dst != permitted:
                raise ValueError(
                    f"Policy contains unexpected router-sourced dst {dst!r} for src {src[0]!r}"
                )
