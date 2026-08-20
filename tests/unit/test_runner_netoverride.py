from typing import Any

import pytest

from xorcise.core.runner.netoverride import (
    EGRESS_NET,
    ROUTER_OCTET,
    build_net_override,
    carve_entry_subnets,
    compose_network_names,
    ingress_address,
    target_ips_for,
)


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
    override = build_net_override("run-1", {"dmz": "10.200.1.0/25"})
    networks = override["networks"]
    services = override["services"]
    assert isinstance(networks, dict) and isinstance(services, dict)
    assert networks["dmz"]["ipam"]["config"][0]["subnet"] == "10.200.1.0/25"
    assert "xorcise-router" in services


def test_router_is_official_tailscale_image_in_kernel_mode():
    # Tailscale runs as its own inner container (the official image), NOT in the outer netns.
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"})["services"]
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
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"})["services"]
    assert isinstance(services, dict)
    env = services["xorcise-router"]["environment"]
    assert "TS_DEBUG_FIREWALL_MODE=nftables" in env


def test_router_trusts_ca_and_resolves_host_when_airgapped():
    # a TLS CA + host alias make the router trust the self-signed control cert and
    # resolve the Headscale hostname from inside the nested network.
    services = build_net_override(
        "run-1",
        {"dmz": "10.200.1.0/25"},
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
    services = build_net_override("run-1", {"dmz": "10.200.1.0/25"})["services"]
    assert isinstance(services, dict)
    router = services["xorcise-router"]
    assert "extra_hosts" not in router
    assert "volumes" not in router
    assert not any(e.startswith("SSL_CERT_FILE") for e in router["environment"])


def test_override_labels_router_and_target_for_exact_host_daemon_teardown():
    services = build_net_override(
        "run-1", {"default": "10.200.1.0/24"}, static_ips={"web": {"default": 10}}
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
        "run-x", {"default": "10.200.20.0/24"}, static_ips={"web": {"default": 10}}
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
    services = build_net_override("run-x", {"default": "10.200.20.0/24"})["services"]
    assert isinstance(services, dict)
    assert set(services) == {"xorcise-router"}  # only the router, as before


# --- confinement + agent ingress ---------------------------------------------------------------

ENTRY = {"dmz_net": "10.200.1.0/24"}


def _override(**kw: Any) -> dict[str, Any]:
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
    assert o["services"]["xorcise-router"]["networks"]["dmz_net"]["ipv4_address"] == "10.200.1.2"


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


def test_no_ingress_prologue_unless_the_mission_asks_for_it():
    router = _override()["services"]["xorcise-router"]
    assert "entrypoint" not in router


def test_ingress_prologue_arms_dnat_and_masquerade():
    o = _override(agent_ingress=True, agent_user="run-run1-agent")
    router = o["services"]["xorcise-router"]
    script = router["entrypoint"][2]
    assert "MASQUERADE" in script and "-o tailscale0" in script
    assert "10.200.1.254" in script  # the callback address the target dials
    assert "run-run1-agent" in script  # discovered by user, never a pinned address
    assert script.rstrip().endswith("exec /usr/local/bin/containerboot")


def test_ingress_requires_an_agent_user_to_discover():
    with pytest.raises(ValueError, match="agent_user"):
        _override(agent_ingress=True)


def test_ca_and_ingress_prologues_compose():
    router = _override(agent_ingress=True, agent_user="run-run1-agent", ca_cert_path="/tmp/ca.pem")[
        "services"
    ]["xorcise-router"]
    script = router["entrypoint"][2]
    assert "headscale-ca.pem" in script and "MASQUERADE" in script
    assert script.count("exec /usr/local/bin/containerboot") == 1


@pytest.mark.parametrize("octet", [ROUTER_OCTET, 254])
def test_reserved_addresses_refuse_to_collide_with_a_mission_pin(octet):
    with pytest.raises(ValueError, match="reserved"):
        _override(static_ips={"web": {"dmz_net": octet}})


def test_a_normal_static_ip_pin_is_untouched():
    o = _override(static_ips={"web": {"dmz_net": 10}})
    assert o["services"]["web"]["networks"]["dmz_net"]["ipv4_address"] == "10.200.1.10"


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
