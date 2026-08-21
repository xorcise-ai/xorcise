from typing import Any

import pytest

from xorcise.core.runner.netoverride import (
    EGRESS_NET,
    build_net_override,
    carve_entry_subnets,
    compose_network_names,
    ingress_address,
    router_address,
    target_ips_for,
)

# The router discovers the agent by this headscale user; ingress is armed on every run.
USER = "run-run1-agent"


def test_no_entries_is_empty():
    assert carve_entry_subnets("10.200.1.0/24", ()) == {}


def test_single_entry_uses_whole_cidr():
    assert carve_entry_subnets("10.200.1.0/24", ("dmz",)) == {"dmz": "10.200.1.0/24"}


def test_two_entries_split_the_cidr():
    out = carve_entry_subnets("10.200.1.0/24", ("dmz", "internal"))
    assert set(out) == {"dmz", "internal"}
    assert out["dmz"] != out["internal"]
    assert out["dmz"].endswith("/25") and out["internal"].endswith("/25")


def test_build_override_pins_subnets_and_adds_router():
    override = build_net_override("run-1", {"dmz": "10.200.1.0/25"}, agent_user=USER)
    networks = override["networks"]
    services = override["services"]
    assert isinstance(networks, dict) and isinstance(services, dict)
    assert networks["dmz"]["ipam"]["config"][0]["subnet"] == "10.200.1.0/25"
    assert "xorcise-router" in services


def test_router_is_official_tailscale_image_in_kernel_mode():
    # Tailscale runs as its own inner container (the official image), NOT in the outer netns.
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"}, agent_user=USER)["services"]
    assert isinstance(services, dict)
    router = services["xorcise-router"]
    assert router["image"].startswith("tailscale/tailscale")
    assert "/dev/net/tun:/dev/net/tun" in router["devices"]
    assert "NET_ADMIN" in router["cap_add"]
    env = router["environment"]
    assert "TS_USERSPACE=false" in env  # kernel mode for subnet routing
    assert "TS_AUTHKEY=${XORCISE_AUTHKEY}" in env  # secret stays a placeholder
    assert "TS_ROUTES=${XORCISE_ROUTES}" in env
    # login server goes via --login-server (containerboot ignores TS_LOGIN_SERVER)
    assert "TS_EXTRA_ARGS=--login-server=${XORCISE_LOGIN_SERVER}" in env


def test_router_pins_nftables_firewall_backend():
    # the router must run its netfilter programming through nftables, not the stable
    # image's default iptables-legacy. Legacy needs ip_tables/iptable_{filter,nat} modules that a
    # nested container can't modprobe; on nftables hosts they're absent, so the router silently
    # installs no forwarding rules and every run is unwinnable. Pinning nftables removes that
    # host-module dependency.
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"}, agent_user=USER)["services"]
    assert isinstance(services, dict)
    env = services["xorcise-router"]["environment"]
    assert "TS_DEBUG_FIREWALL_MODE=nftables" in env


def test_router_trusts_ca_and_resolves_host_when_airgapped():
    # a TLS CA + host alias make the router trust the self-signed control cert and
    # resolve the Headscale hostname from inside the nested network.
    services = build_net_override(
        "run-1",
        {"dmz": "10.200.1.0/25"},
        agent_user=USER,
        extra_hosts=("headscale.local:172.17.0.1",),
        ca_cert_path="/mission/headscale-ca.pem",
    )["services"]
    assert isinstance(services, dict)
    router = services["xorcise-router"]
    assert router["extra_hosts"] == ["headscale.local:172.17.0.1"]
    assert "volumes" not in router  # host-daemon mode cannot bind an outer-container path
    assert "SSL_CERT_FILE=/tmp/headscale-ca.pem" in router["environment"]
    assert "XORCISE_HEADSCALE_CA_B64=${XORCISE_HEADSCALE_CA_B64}" in router["environment"]
    assert router["command"] == []
    assert "base64 -d" in router["entrypoint"][2]
    assert "containerboot" in router["entrypoint"][2]


def test_no_ca_or_hosts_by_default():
    # Dev (plain-HTTP) path: no CA mount, no extra_hosts.
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"}, agent_user=USER)["services"]
    assert isinstance(services, dict)
    router = services["xorcise-router"]
    assert "extra_hosts" not in router
    assert "volumes" not in router
    assert not any(e.startswith("SSL_CERT_FILE") for e in router["environment"])


def test_override_labels_router_and_target_for_exact_host_daemon_teardown():
    services = build_net_override(
        "run-1", {"default": "10.200.1.0/24"}, agent_user=USER, static_ips={"web": {"default": 10}}
    )["services"]
    assert isinstance(services, dict)
    for name in ("xorcise-router", "web"):
        assert services[name]["labels"] == {
            "xorcise.managed": "true",
            "xorcise.run_id": "run-1",
        }


# --- honor static_ips ---------------------------------------------------------------


def test_target_ips_for_computes_address_from_subnet_and_octet():
    ips = target_ips_for({"web": {"default": 10}}, {"default": "10.200.20.0/24"})
    assert ips == {"web": "10.200.20.10"}


def test_target_ips_for_skips_service_on_uncarved_network():
    # A service pinned only on a network this run didn't carve has no subnet to resolve against.
    assert target_ips_for({"db": {"internal": 5}}, {"default": "10.200.20.0/24"}) == {}


def test_build_override_pins_service_ipv4_address_from_static_ips():
    override = build_net_override(
        "run-x", {"default": "10.200.20.0/24"}, agent_user=USER, static_ips={"web": {"default": 10}}
    )
    services = override["services"]
    assert isinstance(services, dict)
    assert services["web"]["networks"]["default"]["ipv4_address"] == "10.200.20.10"
    # the router is still present and the network subnet still pinned
    assert "xorcise-router" in services
    networks = override["networks"]
    assert isinstance(networks, dict)
    assert networks["default"]["ipam"]["config"][0]["subnet"] == "10.200.20.0/24"


def test_build_override_without_static_ips_pins_no_service_ip():
    services = build_net_override("run-x", {"default": "10.200.20.0/24"}, agent_user=USER)[
        "services"
    ]
    assert isinstance(services, dict)
    assert set(services) == {"xorcise-router"}  # only the router, as before


# --- confinement + agent ingress ---------------------------------------------------------------

ENTRY = {"dmz_net": "10.200.1.0/24"}


def _override(**kw: Any) -> dict[str, Any]:
    kw.setdefault("agent_user", USER)
    return build_net_override("run1", ENTRY, **kw)


def test_every_mission_network_is_confined_not_just_the_entry_ones():
    """The multi-homed case: a service with a foot in an unconfined network keeps its way out."""
    o = _override(all_networks=["dmz_net", "internal_net"])
    assert o["networks"]["dmz_net"]["internal"] is True
    assert o["networks"]["internal_net"]["internal"] is True


def test_router_gets_an_egress_network_with_default_route_priority():
    o = _override(all_networks=["dmz_net"])
    assert o["networks"][EGRESS_NET] == {}  # the one network that is NOT internal
    router = o["services"]["xorcise-router"]
    assert EGRESS_NET in router["networks"]
    assert router["networks"][EGRESS_NET]["priority"] > 0


def test_router_address_is_pinned_not_docker_sequential():
    o = _override()
    assert o["services"]["xorcise-router"]["networks"]["dmz_net"]["ipv4_address"] == "10.200.1.253"


def test_allow_egress_leaves_networks_routable_and_adds_no_egress_net():
    o = _override(all_networks=["dmz_net"], allow_egress=True)
    assert "internal" not in o["networks"]["dmz_net"]
    assert EGRESS_NET not in o["networks"]
    assert EGRESS_NET not in o["services"]["xorcise-router"]["networks"]


def test_ingress_address_sits_at_the_top_of_a_carved_subnet():
    """A fixed high octet would fall outside a carved /25; counting down from broadcast cannot."""
    assert ingress_address("10.200.1.0/24") == "10.200.1.254"
    assert ingress_address("10.200.1.0/25") == "10.200.1.126"
    assert ingress_address("10.200.1.128/25") == "10.200.1.254"


def test_ingress_is_armed_on_every_run():
    """Not a per-mission switch: the agent is a host on the mission network, so the router always
    arms the path back to it. A mission that never calls back simply does not use it."""
    router = _override()["services"]["xorcise-router"]
    script = router["entrypoint"][2]
    assert "MASQUERADE" in script and "-o tailscale0" in script
    assert "10.200.1.254" in script  # the callback address the target dials
    assert USER in script  # discovered by headscale user, never a pinned address
    assert script.rstrip().endswith("exec /usr/local/bin/containerboot")


def test_ingress_is_armed_on_every_entry_segment_not_just_one():
    """Confinement makes each entry network `internal: true`, and an internal network gives its
    containers an on-link route and NO default route — so an address in a SIBLING subnet is
    `Network unreachable`, not merely filtered. Arming one address on one segment therefore left
    every other segment with no way to reach the agent at all, which is the shape segmented-pivot
    and operation-tessera both have. Verified live: a container on 10.200.1.0/25 could reach
    .126 (its own segment) and got `Network unreachable` for .254 (the sibling)."""
    two = {"zulu_net": "10.200.1.0/25", "alpha_net": "10.200.1.128/25"}
    services: Any = build_net_override("run1", two, agent_user=USER)["services"]
    script = services["xorcise-router"]["entrypoint"][2]
    for addr in ("10.200.1.126", "10.200.1.254"):
        assert f"ip addr add {addr}/25" in script, f"{addr} never armed on its own segment"
        assert addr in script.split("INGRESS_IPS=")[1], f"{addr} missing from the DNAT set"


def test_ingress_dnat_matches_every_protocol():
    """The prompt promises the agent any port; a `-p tcp` match silently dropped a UDP beacon or
    an ICMP probe of the callback address, which from the mission author's side is indistinguishable
    from a mission bug."""
    script = _override()["services"]["xorcise-router"]["entrypoint"][2]
    dnat = [ln for ln in script.splitlines() if "DNAT" in ln]
    assert dnat and not any("-p tcp" in ln or "-p udp" in ln for ln in dnat)


def test_ingress_uses_the_nft_backend_and_refuses_to_boot_unarmed():
    """The image symlinks `iptables` to iptables-legacy, which needs ip_tables/iptable_nat; this
    container is nested with no /lib/modules to modprobe them, so on an nftables-only host legacy
    installs ZERO rules and the data plane is silently dead while the control plane looks healthy.
    That is the same failure TS_DEBUG_FIREWALL_MODE=nftables exists to prevent."""
    script = _override()["services"]["xorcise-router"]["entrypoint"][2]
    assert "IPT=iptables-nft" in script
    assert "FATAL" in script and "exit 1" in script


def test_ingress_ignores_offline_peers_when_resolving_the_agent():
    """`tailscale status` lists offline peers too, so a stale registration from an earlier join
    matched the grep and `head -1` returned it forever — pinning the DNAT to a dead address while
    the log said `agent ingress ... -> ...`, i.e. armed."""
    script = _override()["services"]["xorcise-router"]["entrypoint"][2]
    resolve = script.split("aip=$$(tailscale status")[1].split("\n    case")[0]
    assert 'grep -v "offline"' in resolve
    assert resolve.index('grep -v "offline"') < resolve.index("head -1")


def test_ingress_requires_an_agent_user_to_discover():
    """Fail closed: without the user the router cannot find the agent, and an override that
    silently omitted the DNAT would leave a callback timing out with no signal at all."""
    with pytest.raises(ValueError, match="agent_user"):
        build_net_override("run1", ENTRY)


def test_ca_and_ingress_prologues_compose():
    router = _override(ca_cert_path="/tmp/ca.pem")["services"]["xorcise-router"]
    script = router["entrypoint"][2]
    assert "headscale-ca.pem" in script and "MASQUERADE" in script
    assert script.count("exec /usr/local/bin/containerboot") == 1


@pytest.mark.parametrize("octet", [253, 254])
def test_reserved_addresses_refuse_to_collide_with_a_mission_pin(octet):
    with pytest.raises(ValueError, match="reserved"):
        _override(static_ips={"web": {"dmz_net": octet}})


def test_the_router_keeps_clear_of_dockers_dynamic_range():
    """Docker's IPAM allocates from the BOTTOM (.1 gateway, then .2 onward), so a router pinned
    low races every unpinned mission service: at .2 a two-service stack failed `compose up` 4
    times in 5 with `Address already in use`, which under the fused entrypoint's `set -eu` kills
    the outer container and reports the run FAILED."""
    assert router_address("10.200.1.0/24") == "10.200.1.253"
    assert router_address("10.200.1.128/25") == "10.200.1.253"
    assert router_address("10.200.1.0/25") == "10.200.1.125"
    for name, spec in _override()["services"]["xorcise-router"]["networks"].items():
        if name == EGRESS_NET:
            continue
        assert not spec["ipv4_address"].endswith((".1", ".2", ".3"))


def test_a_normal_static_ip_pin_is_untouched():
    o = _override(static_ips={"web": {"dmz_net": 10}})
    assert o["services"]["web"]["networks"]["dmz_net"]["ipv4_address"] == "10.200.1.10"


def test_compose_network_names_reads_yaml_12_like_compose_does():
    """PyYAML defaults to YAML 1.1, where the bare keys no/on/yes/off/y/n resolve to BOOLEANS —
    so a network named `no` came back as the string "False", the override declared a phantom
    network by that name, and the real one was never marked internal. Compose reads YAML 1.2
    (gopkg.in/yaml.v3), where they stay strings. A confinement control that disagrees with Compose
    about a network's NAME fails open."""
    text = "networks:\n  no: {}\n  on: {}\nservices:\n  a: {networks: [no]}\n"
    assert compose_network_names(text) == ("no", "on")


def test_compose_network_names_reads_declarations_and_attachments():
    text = """
services:
  web: {networks: [dmz_net, internal_net]}
  db: {networks: {internal_net: {}}}
networks:
  dmz_net: {}
  internal_net: {}
"""
    assert compose_network_names(text) == ("dmz_net", "internal_net")


def test_compose_network_names_falls_back_to_the_implicit_default():
    assert compose_network_names("services:\n  web:\n    image: x") == ("default",)


def test_compose_network_names_rejects_unparseable_input():
    with pytest.raises(ValueError):
        compose_network_names("[not, a, mapping]")
