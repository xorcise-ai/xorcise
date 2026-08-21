"""xorcise.core.headscale — tailnet control (the hard fence).

LAYER: PART-ISLAND. Imports only contracts + kernel; never the application layer or a
sibling island. Per-run auth key + ACL minted at run-create, revoked at teardown — the ACL
is the hard boundary. Lifted from the networking PoC.
"""

from __future__ import annotations

from .cidr import allocate_cidr, cidr_for_index, overlapping_subnets
from .cli import (
    DockerExecHeadscaleCli,
    HeadscaleCli,
    HeadscaleError,
    StubHeadscaleCli,
)
from .controller import NetworkController
from .policy import RunNetwork, assert_policy_safe, render_policy, router_tag_for

__all__ = [
    "NetworkController",
    "RunNetwork",
    "HeadscaleCli",
    "StubHeadscaleCli",
    "DockerExecHeadscaleCli",
    "HeadscaleError",
    "render_policy",
    "router_tag_for",
    "assert_policy_safe",
    "allocate_cidr",
    "cidr_for_index",
    "overlapping_subnets",
]
