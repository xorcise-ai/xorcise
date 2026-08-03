"""Per-run compose net-override helpers (pure). Runner-internal.

Lifts the PoC's carve_entry_subnets/build_net_override: split a run's CIDR across its
entry networks and pin them in a docker-compose override, plus a Tailscale router service.
Secrets are referenced as ${VAR} placeholders so keys never land on disk.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Mapping, Sequence

from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL

# The Tailscale router runs as its own inner container from the official image; the builder
# bakes this into the fused image's images.tar so no run-time pull is needed at deploy.
ROUTER_IMAGE = "tailscale/tailscale:stable"


def carve_entry_subnets(run_cidr: str, entry_networks: Sequence[str]) -> dict[str, str]:
    """Map each entry network to a subnet of the run CIDR.

    One entry → the whole run CIDR; N entries → the CIDR split into the smallest equal
    power-of-two block that fits them (deterministic, by input order).
    """
    nets = list(entry_networks)
    if not nets:
        return {}
    if len(nets) == 1:
        return {nets[0]: run_cidr}
    network = ipaddress.ip_network(run_cidr)
    diff = math.ceil(math.log2(len(nets)))
    subnets = list(network.subnets(prefixlen_diff=diff))
    return {name: str(subnets[i]) for i, name in enumerate(nets)}


def _host_in_subnet(cidr: str, octet: int) -> str:
    """The address at <octet> within <cidr> (network address + octet). Pure."""
    return str(ipaddress.ip_network(cidr).network_address + octet)


def target_ips_for(
    static_ips: Mapping[str, Mapping[str, int]],
    entry_subnets: Mapping[str, str],
) -> dict[str, str]:
    """Resolve each statically-pinned service to its agent-facing IP on the carved subnet.

    `static_ips` is the manifest's service->network->last-octet map. A service pinned only on a
    network this run didn't carve is skipped (no subnet to resolve against). First carved match
    wins — one address per service. Pure; mirrors the per-net pinning in build_net_override.
    """
    out: dict[str, str] = {}
    for service, by_net in static_ips.items():
        for net, octet in by_net.items():
            cidr = entry_subnets.get(net)
            if cidr is None:
                continue
            out[service] = _host_in_subnet(cidr, octet)
            break
    return out


def build_net_override(
    project: str,
    entry_subnets: Mapping[str, str],
    *,
    router_image: str = ROUTER_IMAGE,
    extra_hosts: Sequence[str] = (),
    ca_cert_path: str = "",
    static_ips: Mapping[str, Mapping[str, int]] | None = None,
) -> dict[str, object]:
    """Compose override pinning each entry network's subnet + a Tailscale router service.

    The router is the OFFICIAL Tailscale image running as its OWN inner container (clean
    netns — NOT tailscaled in the outer fused container's namespace, which collides with the
    inner dockerd's netfilter). It joins the per-run
    tailnet in kernel mode (TS_USERSPACE=false + NET_ADMIN + /dev/net/tun) and advertises the
    run's routes. Secrets stay as ${XORCISE_AUTHKEY} placeholders so the key is interpolated
    from the container env at `compose up` time and never lands in the override file.
    """
    networks: dict[str, object] = {
        name: {"ipam": {"config": [{"subnet": cidr}]}} for name, cidr in entry_subnets.items()
    }
    env = [
        # containerboot ignores TS_LOGIN_SERVER on current images — the reliable way to point
        # at Headscale is `--login-server` via TS_EXTRA_ARGS (verified live: TS_LOGIN_SERVER
        # alone sent the node to controlplane.tailscale.com).
        "TS_EXTRA_ARGS=--login-server=${XORCISE_LOGIN_SERVER}",
        "TS_AUTHKEY=${XORCISE_AUTHKEY}",
        "TS_ROUTES=${XORCISE_ROUTES}",
        "TS_USERSPACE=false",
        # pin the router's firewall backend to nftables. In kernel mode Tailscale
        # programs its ts-forward chain + SNAT/MASQUERADE through netfilter; the stable image
        # defaults to iptables-legacy, which needs the ip_tables/iptable_{filter,nat} kernel
        # modules. This container is nested with no /lib/modules, so it can't modprobe them —
        # and on nftables hosts (e.g. Kali) the legacy modules aren't loaded, so iptables can't
        # initialize its filter/nat tables and the router installs ZERO forwarding rules. The
        # control plane looks healthy (route approved + serving, agent online) but the data
        # plane is dead: every subnet-routed packet is dropped and every run is unwinnable and
        # silent. nftables IS loaded on these hosts, so pinning it removes the host-module
        # dependency entirely. containerboot honors auto|iptables|nftables.
        "TS_DEBUG_FIREWALL_MODE=nftables",
        f"TS_HOSTNAME={project}-router",
        "TS_STATE_DIR=/var/lib/tailscale",
    ]
    router: dict[str, object] = {
        "image": router_image,
        "networks": list(entry_subnets.keys()),
        "cap_add": ["NET_ADMIN", "NET_RAW"],
        "devices": ["/dev/net/tun:/dev/net/tun"],
        "environment": env,
        "labels": {MANAGED_LABEL: "true", RUN_ID_LABEL: project},
    }
    if extra_hosts:
        # Resolve the Headscale TLS hostname to the host from the nested network (air-gapped).
        router["extra_hosts"] = list(extra_hosts)
    if ca_cert_path:
        # The macOS fallback composes against Docker Desktop's HOST daemon, where a bind source
        # inside the outer fused container does not exist. Pass the already-provided CA as base64
        # and materialize it inside the router before containerboot; this also works under DinD.
        env.extend(
            [
                "XORCISE_HEADSCALE_CA_B64=${XORCISE_HEADSCALE_CA_B64}",
                "SSL_CERT_FILE=/tmp/headscale-ca.pem",
            ]
        )
        ca_boot = (
            'printf "%s" "$$XORCISE_HEADSCALE_CA_B64" | base64 -d '
            "> /tmp/headscale-ca.pem; exec /usr/local/bin/containerboot"
        )
        router["entrypoint"] = ["/bin/sh", "-c", ca_boot]
        router["command"] = []
    # honor the manifest's static_ips — pin each service to its authored address on the
    # carved subnet so docker stops handing out sequential IPs. A service on an un-carved network
    # is skipped. Compose merges these networks blocks with the base mission compose.
    services: dict[str, object] = {"xorcise-router": router}
    for service, by_net in (static_ips or {}).items():
        for net, octet in by_net.items():
            cidr = entry_subnets.get(net)
            if cidr is None:
                continue
            svc: dict[str, object] = services.setdefault(service, {})  # type: ignore[assignment]
            svc["labels"] = {MANAGED_LABEL: "true", RUN_ID_LABEL: project}
            nets: dict[str, object] = svc.setdefault("networks", {})  # type: ignore[assignment]
            nets[net] = {"ipv4_address": _host_in_subnet(cidr, octet)}
    return {"networks": networks, "services": services}
