"""Per-run compose net-override helpers (pure). Runner-internal.

Lifts the PoC's carve_entry_subnets/build_net_override: split a run's CIDR across its
entry networks and pin them in a docker-compose override, plus a Tailscale router service.
Secrets are referenced as ${VAR} placeholders so keys never land on disk.
"""

from __future__ import annotations

import ipaddress
import math
from collections.abc import Mapping, Sequence

import yaml

from xorcise.core.contracts.errors import EnvironmentConfigError
from xorcise.core.runner.docker import MANAGED_LABEL, RUN_ID_LABEL

# The Tailscale router runs as its own inner container from the official image; the builder
# bakes this into the fused image's images.tar so no run-time pull is needed at deploy.
ROUTER_IMAGE = "tailscale/tailscale:stable"

# How far below the broadcast address the router and the agent's callback address sit. Both are
# pinned rather than left to docker: the ingress DNAT and the callback address have to be knowable
# before the stack is up, and a docker-sequential address is not.
#
# They sit at the TOP of the range because docker's IPAM allocates from the BOTTOM: the gateway
# takes .1 and the first unpinned service takes .2, so a router pinned there races every mission
# service that does not pin itself. That race is not theoretical and it is not rare — a two-service
# stack with the router at .2 failed `compose up` 4 times in 5 with `Address already in use`, which
# under the fused entrypoint's `set -eu` kills the outer container and reports the run FAILED with
# an error naming neither side. At the top of the range the same stack came up 6 times in 6.
INGRESS_OFFSET = 1  # /24 -> .254
ROUTER_OFFSET = 2  # /24 -> .253

# The confinement network. Mission networks are `internal: true`, which removes their route off
# box entirely — including the docker-bridge path that let a mission container reach the agent
# WITHOUT crossing the tailnet. The router still needs to reach Headscale and its WireGuard peer,
# so it (and only it) is also attached here. `priority` makes this the router's default route:
# with several attachments docker picks the default gateway by priority, and an internal network
# winning that race would leave the router with no way out.
EGRESS_NET = "xorcise-egress"
EGRESS_PRIORITY = 100


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


def _from_top(cidr: str, offset: int) -> str:
    """The address <offset> below <cidr>'s broadcast address. Pure.

    Counting DOWN from the broadcast keeps these clear of docker's bottom-up sequential allocation
    and stays in range for a carved sub-subnet, which a fixed high octet would not.
    """
    return str(ipaddress.ip_network(cidr).broadcast_address - offset)


def ingress_address(cidr: str) -> str:
    """The agent's callback address ON the mission subnet.

    The target reaches the agent here and the router DNATs it across the tailnet. Putting the
    address inside the mission subnet is what keeps the fused container out of this: a mission
    container talks to it as a same-segment neighbour, so no route off the subnet — and therefore
    no gateway change — is ever needed. That last point is load-bearing under confinement: an
    `internal: true` network gives its containers an on-link route and NO default route, so an
    address outside their own subnet is not merely filtered, it is `Network unreachable`.
    """
    return _from_top(cidr, INGRESS_OFFSET)


def router_address(cidr: str) -> str:
    """The router's own address on a carved entry subnet."""
    return _from_top(cidr, ROUTER_OFFSET)


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


# Router prologue that arms agent ingress, run before containerboot hands over to tailscaled.
#
# `$$` is compose's escape for a literal `$` — every shell expansion below has to be written that
# way or `compose up` interpolates it away. Placeholders are substituted, not f-string formatted,
# because the body is full of `${...}` and awk's `{print $1}`.
#
# The agent is found by its HEADSCALE USER, which is derived from the run id and therefore known
# here; its tailnet ADDRESS is not knowable at compose time (the agent joins later, and may rejoin
# with a different one), so the DNAT target is discovered and re-pointed rather than pinned.
#
# `iptables-nft`, not bare `iptables`: the image symlinks `iptables` to iptables-legacy, which needs
# the ip_tables/iptable_nat kernel modules. This container is nested with no /lib/modules to
# modprobe them, so on an nftables-only host (Kali) legacy installs ZERO rules — the same
# silent-dead-data-plane failure TS_DEBUG_FIREWALL_MODE=nftables exists to prevent, and mixing the
# two backends would split the ruleset besides. There is no `set -e` here, so every rule that
# matters is checked explicitly and a failure aborts the router rather than booting it unarmed.
_INGRESS_ADDR_BLOCK = r"""
rip="%%ROUTER_IP%%"
dev=$$(ip -4 -o addr show 2>/dev/null | awk -v r="$$rip" '$$4 ~ "^"r"/" {print $$2; exit}')
[ -n "$$dev" ] || dev=eth0
ip addr add %%INGRESS_CIDR%% dev "$$dev" 2>/dev/null || true
""".strip()

_INGRESS_WATCHER = r"""
IPT=iptables-nft
command -v "$$IPT" >/dev/null 2>&1 || IPT=iptables
INGRESS_IPS="%%INGRESS_IPS%%"
"$$IPT" -t nat -C POSTROUTING -o tailscale0 -j MASQUERADE 2>/dev/null ||
  "$$IPT" -t nat -A POSTROUTING -o tailscale0 -j MASQUERADE || {
    echo "xorcise: FATAL agent ingress SNAT rule would not install ($$IPT)" >&2
    exit 1
  }
(
  cur=""
  waited=0
  while :; do
    aip=$$(tailscale status 2>/dev/null |
      grep -E "[[:space:]]%%AGENT_USER%%[[:space:]]" | grep -v "offline" |
      head -1 | awk '{print $$1}')
    case "$$aip" in 100.*) ;; *) aip="" ;; esac
    if [ -n "$$aip" ] && [ "$$aip" != "$$cur" ]; then
      for iip in $$INGRESS_IPS; do
        if [ -n "$$cur" ]; then
          "$$IPT" -t nat -D PREROUTING -d "$$iip" \
            -j DNAT --to-destination "$$cur" 2>/dev/null || true
        fi
        "$$IPT" -t nat -A PREROUTING -d "$$iip" -j DNAT --to-destination "$$aip" || {
          echo "xorcise: FATAL agent ingress DNAT for $$iip would not install" >&2
          kill -TERM 1
          exit 1
        }
      done
      cur="$$aip"
      waited=0
      echo "xorcise: agent ingress [$$INGRESS_IPS] -> $$aip" >&2
    fi
    if [ -z "$$cur" ]; then
      waited=$$((waited + 3))
      if [ "$$waited" -ge 60 ] && [ $$((waited % 60)) -eq 0 ]; then
        echo "xorcise: WARNING agent ingress NOT armed after $${waited}s -" \
          "no online tailnet node for user %%AGENT_USER%%" >&2
      fi
    fi
    sleep 3
  done
) &
""".strip()


def _ingress_prologue(*, agent_user: str, entry_subnets: Mapping[str, str]) -> str:
    """Render the ingress prologue for one run. Pure.

    An ingress address is armed on EVERY carved entry subnet, not just one. Under confinement each
    entry network is `internal: true` and therefore has no default route, so a mission service can
    only ever reach a callback address that is on-link for its OWN segment — one address armed on
    one segment is simply unreachable from the others (`Network unreachable`, not a timeout). The
    DNAT carries no protocol match, so a beacon over UDP, or an ICMP probe of the callback address,
    reaches the agent the same as TCP does; the prompt promises the agent any port, and the
    mission author should not have to know which protocols the fence happens to forward.
    """
    blocks = [
        _INGRESS_ADDR_BLOCK.replace("%%ROUTER_IP%%", router_address(cidr)).replace(
            "%%INGRESS_CIDR%%",
            f"{ingress_address(cidr)}/{ipaddress.ip_network(cidr).prefixlen}",
        )
        for cidr in entry_subnets.values()
    ]
    watcher = _INGRESS_WATCHER.replace(
        "%%INGRESS_IPS%%", " ".join(ingress_address(c) for c in entry_subnets.values())
    ).replace("%%AGENT_USER%%", agent_user)
    return "\n".join([*blocks, watcher])


def compose_network_names(compose_text: str) -> tuple[str, ...]:
    """Every network a mission's compose will create, sorted. Pure (parses text, no I/O).

    Both the top-level `networks:` block and each service's attachments are read: a service may
    name a network the top level declares, and compose synthesises `default` when nothing is
    declared at all. Getting the FULL set matters — confining only the agent-facing networks
    leaves a multi-homed service (one foot in an entry network, one in another) with its route off
    box intact, which is exactly the shape the segmentation missions use.
    """
    try:
        # BaseLoader, not safe_load: PyYAML's default resolver is YAML 1.1, where the bare keys
        # `no`/`on`/`yes`/`off`/`y`/`n` become booleans — so a network legitimately named `no`
        # would be stringified back to "False" here, and the real network would never be marked
        # internal. Docker Compose reads YAML 1.2 (gopkg.in/yaml.v3), where they stay strings.
        # BaseLoader leaves every scalar a string, which is what makes this read the same document
        # Compose will. Silently disagreeing with Compose about a network's NAME is a fail-open in
        # a confinement control, which is why it is worth the non-default loader.
        doc = yaml.load(compose_text, Loader=yaml.BaseLoader) or {}  # noqa: S506
    except yaml.YAMLError as exc:
        raise ValueError(f"mission compose is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("mission compose did not parse to a mapping")
    names: set[str] = set()
    declared = doc.get("networks")
    if isinstance(declared, dict):
        names.update(str(k) for k in declared)
    services = doc.get("services")
    if isinstance(services, dict):
        for cfg in services.values():
            if not isinstance(cfg, dict):
                continue
            attached = cfg.get("networks")
            if isinstance(attached, dict):
                names.update(str(k) for k in attached)
            elif isinstance(attached, list):
                names.update(str(n) for n in attached)
    return tuple(sorted(names)) or ("default",)


def external_network_names(compose_text: str) -> tuple[str, ...]:
    """The networks a mission's compose declares `external: true`, sorted. Pure.

    An external network is created OUTSIDE the run's compose project, so the override's
    `internal: true` on it is a no-op — compose merges the flag but never applies it to a network
    it did not create (verified: `up` succeeds and the network keeps its real config). Confinement
    would therefore SILENTLY not hold on such a network, the fail-open this pass exists to close.
    The caller refuses to deploy a confined run that declares one. External is spelled either
    `external: true` (short form) or `external: {name: ...}` (long form); BaseLoader renders the
    bool as the string "true", so both a truthy string and a mapping count.
    """
    try:
        doc = yaml.load(compose_text, Loader=yaml.BaseLoader) or {}  # noqa: S506
    except yaml.YAMLError:
        return ()
    if not isinstance(doc, dict):
        return ()
    declared = doc.get("networks")
    if not isinstance(declared, dict):
        return ()
    out: set[str] = set()
    for name, cfg in declared.items():
        if not isinstance(cfg, dict):
            continue
        ext = cfg.get("external")
        if ext == "true" or isinstance(ext, dict):
            out.add(str(name))
    return tuple(sorted(out))


def _assert_reserved_addresses_free(
    entry_subnets: Mapping[str, str],
    static_ips: Mapping[str, Mapping[str, int]],
) -> None:
    """Refuse to build an override whose reserved addresses a mission already claims.

    The router's address and the agent's callback address are pinned, so an authored `static_ips`
    pin on the same octet would produce a duplicate-address stack that comes up looking healthy
    and misroutes. Fail at build time with the collision named rather than at deploy with a
    docker error that says nothing about which mission service is at fault.
    """
    for service, by_net in static_ips.items():
        for net, octet in by_net.items():
            cidr = entry_subnets.get(net)
            if cidr is None:
                continue
            claimed = _host_in_subnet(cidr, octet)
            if claimed == router_address(cidr):
                raise EnvironmentConfigError(
                    f"mission service {service!r} pins {claimed} on network {net!r}, which is "
                    "reserved for the run's tailscale router"
                )
            if claimed == ingress_address(cidr):
                raise EnvironmentConfigError(
                    f"mission service {service!r} pins {claimed} on network {net!r}, which is "
                    "reserved for the agent's callback address"
                )


def build_net_override(
    project: str,
    entry_subnets: Mapping[str, str],
    *,
    router_image: str = ROUTER_IMAGE,
    extra_hosts: Sequence[str] = (),
    ca_cert_path: str = "",
    static_ips: Mapping[str, Mapping[str, int]] | None = None,
    all_networks: Sequence[str] = (),
    agent_user: str = "",
    allow_egress: bool = False,
) -> dict[str, object]:
    """Compose override pinning each entry network's subnet + a Tailscale router service.

    The router is the OFFICIAL Tailscale image running as its OWN inner container (clean
    netns — NOT tailscaled in the outer fused container's namespace, which collides with the
    inner dockerd's netfilter). It joins the per-run
    tailnet in kernel mode (TS_USERSPACE=false + NET_ADMIN + /dev/net/tun) and advertises the
    run's routes. Secrets stay as ${XORCISE_AUTHKEY} placeholders so the key is interpolated
    from the container env at `compose up` time and never lands in the override file.
    """
    confine = not allow_egress
    # The confinement egress network's name is reserved. A mission that declares a network called
    # `xorcise-egress` would have its `internal: true` overwritten by `networks[EGRESS_NET] = {}`
    # below — silently losing confinement AND sharing an L2 segment with the run's router, while
    # the run still reports itself confined. Refuse at build time with the collision named rather
    # than deploy a mission that is quietly unconfined on exactly one network.
    if confine and EGRESS_NET in {*entry_subnets, *all_networks}:
        raise EnvironmentConfigError(
            f"mission declares a network named {EGRESS_NET!r}, which is reserved for the run's "
            "confinement egress network — rename the mission network"
        )
    # EVERY mission network, not just the carved entry ones. A service attached to an entry
    # network AND a second, unconfined one keeps its route off box through the second — and the
    # multi-homed services (segmented-pivot's `web`, operation-tessera's `eval-harness`) are
    # precisely the agent-facing ones. Confining only the entry nets would leave the hole open
    # exactly where it matters most.
    named = dict.fromkeys([*entry_subnets, *all_networks])
    networks: dict[str, object] = {}
    for name in named:
        spec: dict[str, object] = {}
        cidr = entry_subnets.get(name)
        if cidr is not None:
            spec["ipam"] = {"config": [{"subnet": cidr}]}
        if confine:
            spec["internal"] = True
        networks[name] = spec
    if confine:
        networks[EGRESS_NET] = {}

    _assert_reserved_addresses_free(entry_subnets, static_ips or {})
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
    router_nets: dict[str, object] = {
        name: {"ipv4_address": router_address(cidr)} for name, cidr in entry_subnets.items()
    }
    if confine:
        router_nets[EGRESS_NET] = {"priority": EGRESS_PRIORITY}
    router: dict[str, object] = {
        "image": router_image,
        "networks": router_nets,
        "cap_add": ["NET_ADMIN", "NET_RAW"],
        "devices": ["/dev/net/tun:/dev/net/tun"],
        "environment": env,
        "labels": {MANAGED_LABEL: "true", RUN_ID_LABEL: project},
    }
    prologue: list[str] = []
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
        prologue.append(
            'printf "%s" "$$XORCISE_HEADSCALE_CA_B64" | base64 -d > /tmp/headscale-ca.pem'
        )
    if entry_subnets:
        if not agent_user:
            raise EnvironmentConfigError(
                "agent ingress requires agent_user — the router discovers the agent's tailnet "
                "address by its headscale user and cannot arm ingress without it"
            )
        prologue.append(_ingress_prologue(agent_user=agent_user, entry_subnets=entry_subnets))
    if prologue:
        # Anything the router must do in its OWN netns before tailscaled owns it. iptables rules
        # naming tailscale0 are accepted before the interface exists (matched at packet time), so
        # this stays a pre-exec prologue rather than needing a post-start hook.
        boot = "\n".join([*prologue, "exec /usr/local/bin/containerboot"])
        router["entrypoint"] = ["/bin/sh", "-c", boot]
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
