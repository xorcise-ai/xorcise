"""Deterministic /prefix allocator over a base network. PART-ISLAND, pure.

Run IDs may encode the 3rd octet for readability (run-17 -> 10.200.17.0/24), but
the allocator always verifies the chosen subnet is free against currently allocated
CIDRs. Lifted from the networking PoC (cidr_allocator.py).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable


def overlapping_subnets(base: str, prefix: int, in_use: Iterable[str]) -> set[str]:
    """The `/prefix` subnets of `base` that overlap any CIDR in `in_use`, as canonical strings.

    Lets the allocator exclude subnets ALREADY carved on the Docker host — including LEFTOVER
    networks the run DB no longer tracks (imperfect teardown), the collision that reused a subnet
    and silently killed a mission deploy. Overlap-aware, not string equality: a non-`/prefix`-
    aligned in-use CIDR still masks every `/prefix` it spans. CIDRs outside `base` contribute
    nothing (Docker's own bridges live elsewhere); an unparseable token is skipped, never raised.
    The result slots straight into `allocate_cidr`/`cidr_for_index`'s `allocated` set. Pure."""
    network = ipaddress.ip_network(base)
    used: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in in_use:
        try:
            used.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue  # a malformed/non-CIDR token can never mask a real subnet
    return {
        str(subnet)
        for subnet in network.subnets(new_prefix=prefix)
        if any(subnet.overlaps(u) for u in used)
    }


def allocate_cidr(base: str, prefix: int, allocated: set[str]) -> str:
    """Return the first free /prefix subnet of `base` not in `allocated`.

    Skips the leading subnet (e.g. 10.200.0.0/24) to reserve octet 0.
    """
    network = ipaddress.ip_network(base)
    for subnet in network.subnets(new_prefix=prefix):
        if subnet.network_address == network.network_address:
            continue
        if str(subnet) not in allocated:
            return str(subnet)
    raise RuntimeError(f"No free /{prefix} subnets available in {base}")


def cidr_for_index(base: str, prefix: int, index: int, allocated: set[str]) -> str:
    """Try to give run index N the Nth subnet (10.200.N.0/24); else first-free."""
    network = ipaddress.ip_network(base)
    subnets = list(network.subnets(new_prefix=prefix))
    if 0 < index < len(subnets):
        candidate = str(subnets[index])
        if candidate not in allocated:
            return candidate
    return allocate_cidr(base, prefix, allocated)
