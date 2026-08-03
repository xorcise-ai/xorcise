import subprocess
from pathlib import Path

import pytest

from xorcise.core.headscale import provision
from xorcise.core.headscale.provision import (
    ProvisionError,
    _collector_route,
    default_host_ip,
    gen_certs,
)


def test_config_and_cert_paths_share_one_hostname():
    # server_url, tls_cert_path and the cert filename cannot diverge.
    cfg = provision.render_config("hs.example")
    # server_url is advertised on 443 (the port the tailscale client forces for its noise/register
    # dial); the container still binds 8443 internally (listen_addr), published host-443 → 8443.
    assert "server_url: https://hs.example:443" in cfg
    assert "listen_addr: 0.0.0.0:8443" in cfg
    assert "tls_cert_path: /etc/headscale/certs/hs.example.crt" in cfg
    assert "tls_key_path: /etc/headscale/certs/hs.example.key" in cfg
    _, crt, key = provision.cert_paths(Path("/wd"), "hs.example")
    assert crt.name == "hs.example.crt" and key.name == "hs.example.key"


def test_render_config_keeps_operator_only_listeners_on_loopback():
    # metrics + gRPC are never published nor dialed from outside the container (the CLI
    # goes through docker exec + the unix socket), so they stay on the container loopback.
    # listen_addr + STUN must remain wide: docker-proxy delivers published traffic to the
    # container IP, which a loopback bind would refuse.
    cfg = provision.render_config("hs.example")
    assert "metrics_listen_addr: 127.0.0.1:9090" in cfg
    assert "grpc_listen_addr: 127.0.0.1:50443" in cfg
    assert "listen_addr: 0.0.0.0:8443" in cfg
    assert "stun_listen_addr: 0.0.0.0:3478" in cfg


def test_render_compose_pins_workdir_and_ports():
    out = provision.render_compose(Path("/wd"), "headscale.local", host_ip="172.17.0.1")
    assert "/wd/config.yaml:/etc/headscale/config.yaml:ro" in out
    assert "/wd/certs:/etc/headscale/certs:ro" in out
    # Control plane published on host 443 → container 8443; external 8443 publish is gone.
    assert '"172.17.0.1:443:8443"' in out
    assert "8443:8443" not in out
    assert "headscale/headscale:stable" in out


def test_render_compose_gateway_scopes_the_443_publish_when_host_ip_given():
    # Bind host 443 to the docker-gateway IP only (the host+routers dial the control plane there),
    # so provisioning never grabs host-wide :443 — the collision that 8443 originally avoided.
    out = provision.render_compose(Path("/wd"), "headscale.local", host_ip="172.17.0.1")
    assert '"172.17.0.1:443:8443"' in out


def test_render_compose_scopes_the_stun_publish_to_the_host_ip():
    # STUN/DERP must ride the same advertised IP as the control plane — an unqualified
    # publish exposes 3478/udp on every interface even when the host IP is known.
    out = provision.render_compose(Path("/wd"), "headscale.local", host_ip="172.17.0.1")
    assert '"172.17.0.1:3478:3478/udp"' in out
    assert '"3478:3478/udp"' not in out


def test_render_compose_refuses_to_publish_without_a_host_ip():
    # No host IP means the publishes would fall back to all interfaces. ensure_up always
    # resolves one via default_host_ip (or raises), so an empty host_ip here is a bug —
    # fail loudly rather than silently publish the control plane host-wide.
    with pytest.raises(ProvisionError, match="host IP"):
        provision.render_compose(Path("/wd"), "headscale.local")


def test_default_host_ip_honors_valid_override(monkeypatch):
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    assert default_host_ip("10.1.2.3") == "10.1.2.3"


def test_default_host_ip_env_used_when_no_arg(monkeypatch):
    monkeypatch.setenv("XORCISE_HEADSCALE_HOST_IP", "172.20.0.1")
    assert default_host_ip() == "172.20.0.1"


def test_default_host_ip_rejects_garbage_override(monkeypatch):
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    with pytest.raises(ProvisionError):
        default_host_ip("not-an-ip")


def test_default_host_ip_uses_lan_ip_on_macos(monkeypatch):
    # on macOS the docker bridge gateway lives inside the Docker Desktop VM and is not
    # host-bindable — derive the host's primary LAN IPv4 (host-bindable AND container-reachable).
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    monkeypatch.setattr(provision, "_host_is_macos", lambda: True)
    monkeypatch.setattr(provision, "_primary_lan_ipv4", lambda: "10.1.2.196")
    assert default_host_ip() == "10.1.2.196"


def test_default_host_ip_macos_errors_without_lan_ip(monkeypatch):
    # no derivable LAN IP → fail loud pointing at the env override (never the VM gateway).
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    monkeypatch.setattr(provision, "_host_is_macos", lambda: True)
    monkeypatch.setattr(provision, "_primary_lan_ipv4", lambda: None)
    with pytest.raises(ProvisionError, match="XORCISE_HEADSCALE_HOST_IP"):
        default_host_ip()


class _FakeSock:
    """Minimal socket stub for _primary_lan_ipv4: a context manager whose getsockname() returns a
    canned (ip, port), and whose connect() optionally raises to exercise the OSError fallback."""

    def __init__(self, name: tuple[str, int], raise_on_connect: bool = False) -> None:
        self._name = name
        self._raise = raise_on_connect

    def __enter__(self) -> "_FakeSock":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def connect(self, addr: tuple[str, int]) -> None:
        if self._raise:
            raise OSError("no route to host")

    def getsockname(self) -> tuple[str, int]:
        return self._name


def test_primary_lan_ipv4_returns_routed_interface_addr(monkeypatch):
    # The UDP routing-table fallback (non-macOS, or macOS with no hardware IP). Force the fallback
    # so this is OS-independent — on macOS _primary_lan_ipv4 tries the hardware scan first.
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    # dotted-string target: patching provision.socket directly trips mypy no-implicit-reexport.
    monkeypatch.setattr(
        "xorcise.core.headscale.provision.socket.socket", lambda *a, **k: _FakeSock(("10.0.0.9", 9))
    )
    assert provision._primary_lan_ipv4() == "10.0.0.9"


def test_primary_lan_ipv4_rejects_loopback(monkeypatch):
    # A loopback result is useless: containers can't reach the host via 127.x.
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    monkeypatch.setattr(
        "xorcise.core.headscale.provision.socket.socket",
        lambda *a, **k: _FakeSock(("127.0.0.1", 9)),
    )
    assert provision._primary_lan_ipv4() is None


def test_primary_lan_ipv4_none_on_oserror(monkeypatch):
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    monkeypatch.setattr(
        "xorcise.core.headscale.provision.socket.socket",
        lambda *a, **k: _FakeSock(("10.0.0.9", 9), raise_on_connect=True),
    )
    assert provision._primary_lan_ipv4() is None


def test_primary_lan_ipv4_on_macos_prefers_hardware_ip_over_tunnel(monkeypatch):
    # regression: a Tailscale/VPN full-tunnel default route makes the UDP
    # routing-table probe return the *tailnet* IP (e.g. 10.8.x on utun), which is unreachable by a
    # joining agent. On macOS the hardware-port scan must win, so the socket path is never used.
    monkeypatch.setattr(provision, "_host_is_macos", lambda: True)
    monkeypatch.setattr(provision, "_macos_lan_ipv4", lambda: "192.168.1.114")
    # The socket, if ever reached, would return the poisoned tunnel IP — assert it is NOT used.
    monkeypatch.setattr(
        "xorcise.core.headscale.provision.socket.socket",
        lambda *a, **k: _FakeSock(("10.8.160.88", 9)),
    )
    assert provision._primary_lan_ipv4() == "192.168.1.114"


def test_primary_lan_ipv4_macos_falls_back_to_socket_when_no_hardware_ip(monkeypatch):
    # If hardware-port enumeration yields nothing (unusual), fall back to the routing-table probe.
    monkeypatch.setattr(provision, "_host_is_macos", lambda: True)
    monkeypatch.setattr(provision, "_macos_lan_ipv4", lambda: None)
    monkeypatch.setattr(
        "xorcise.core.headscale.provision.socket.socket", lambda *a, **k: _FakeSock(("10.0.0.9", 9))
    )
    assert provision._primary_lan_ipv4() == "10.0.0.9"


def test_macos_lan_ipv4_skips_ports_without_an_ip_and_returns_first_real_one(monkeypatch):
    # Hardware ports are walked in service-priority order; ports with no IPv4 ("") are skipped and
    # the first with a real non-loopback address wins. Tunnels never reach here (no hardware port).
    monkeypatch.setattr(provision, "_macos_hardware_devices", lambda: ["bridge0", "en0", "en1"])
    addrs = {"bridge0": "", "en0": "192.168.1.114", "en1": "10.9.9.9"}
    monkeypatch.setattr(provision, "_macos_interface_ipv4", lambda dev: addrs[dev])
    assert provision._macos_lan_ipv4() == "192.168.1.114"


def test_macos_lan_ipv4_none_when_no_hardware_port_has_an_ipv4(monkeypatch):
    monkeypatch.setattr(provision, "_macos_hardware_devices", lambda: ["en0", "en1"])
    monkeypatch.setattr(provision, "_macos_interface_ipv4", lambda dev: "")
    assert provision._macos_lan_ipv4() is None


def test_default_host_ip_uses_bridge_gateway_on_linux(monkeypatch):
    # Linux behavior preserved — the docker bridge gateway is a real host interface.
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    monkeypatch.setattr(provision, "_docker_bridge_gateway", lambda: "172.17.0.1")
    assert default_host_ip() == "172.17.0.1"


def test_default_host_ip_linux_errors_on_bad_gateway(monkeypatch):
    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    monkeypatch.setattr(provision, "_docker_bridge_gateway", lambda: "")
    with pytest.raises(ProvisionError, match="XORCISE_HEADSCALE_HOST_IP"):
        default_host_ip()


def test_gen_certs_writes_ca_and_server_cert(tmp_path):
    gen_certs(tmp_path, "headscale.local")
    ca, crt, key = (
        tmp_path / "certs" / n for n in ("ca.pem", "headscale.local.crt", "headscale.local.key")
    )
    assert ca.exists() and crt.exists() and key.exists()
    txt = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-text", "-noout"], capture_output=True, text=True
    ).stdout
    assert "headscale.local" in txt


def test_gen_certs_adds_ip_san_so_by_ip_join_validates(tmp_path):
    """The server cert carries the bootstrap IP as a SAN (alongside the DNS name), so a
    tailnet join against https://<ip>:8443 passes TLS without any /etc/hosts hostname mapping."""
    gen_certs(tmp_path, "headscale.local", host_ip="172.17.0.1")
    crt = tmp_path / "certs" / "headscale.local.crt"
    txt = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-text", "-noout"], capture_output=True, text=True
    ).stdout
    assert "DNS:headscale.local" in txt  # hostname SAN kept (router join still validates)
    assert "IP Address:172.17.0.1" in txt  # NEW: IP SAN for the by-IP agent join


def _cert_text(crt: Path) -> str:
    return subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-text", "-noout"], capture_output=True, text=True
    ).stdout


def test_gen_certs_regenerates_when_ip_san_changes(tmp_path):
    """A changed host IP (DHCP / new network) must not leave a stale IP SAN — the by-IP
    TLS join would break. gen_certs regenerates when the cached cert lacks the desired IP SAN."""
    crt = tmp_path / "certs" / "headscale.local.crt"
    gen_certs(tmp_path, "headscale.local", host_ip="172.17.0.1")
    assert "IP Address:172.17.0.1" in _cert_text(crt)
    gen_certs(tmp_path, "headscale.local", host_ip="10.0.0.5")
    txt = _cert_text(crt)
    assert "IP Address:10.0.0.5" in txt
    assert "IP Address:172.17.0.1" not in txt


def test_gen_certs_noop_when_ip_san_unchanged(tmp_path):
    """The IP-SAN freshness check must not cause needless churn — a second call with the
    same host_ip is still an idempotent no-op (the cached CA/cert bytes are untouched)."""
    ca = tmp_path / "certs" / "ca.pem"
    gen_certs(tmp_path, "headscale.local", host_ip="172.17.0.1")
    first = ca.read_bytes()
    gen_certs(tmp_path, "headscale.local", host_ip="172.17.0.1")
    assert ca.read_bytes() == first


def test_managed_block_round_trip_preserves_other_config(tmp_path):
    conf = tmp_path / "config.toml"
    conf.write_text('host = "127.0.0.1"\n')
    provision.write_managed_block(
        conf,
        url="https://h:8443",
        ca_cert="/c/ca.pem",
        host_alias="h:1.2.3.4",
        advertise_host="1.2.3.4",
    )
    body = conf.read_text()
    assert 'host = "127.0.0.1"' in body
    assert 'headscale_url = "https://h:8443"' in body
    assert 'headscale_advertise_host = "1.2.3.4"' in body
    # idempotent: writing again does not duplicate the block
    provision.write_managed_block(
        conf,
        url="https://h:8443",
        ca_cert="/c/ca.pem",
        host_alias="h:1.2.3.4",
        advertise_host="1.2.3.4",
    )
    assert conf.read_text().count("xorcise headscale (managed)") == 2  # begin + end marker, once
    provision.strip_managed_block(conf)
    assert "headscale_url" not in conf.read_text()
    assert 'host = "127.0.0.1"' in conf.read_text()


def test_ownership_marker(tmp_path):
    assert provision.is_owned(tmp_path) is False
    provision.mark_owned(tmp_path)
    assert provision.is_owned(tmp_path) is True
    provision.clear_owned(tmp_path)
    assert provision.is_owned(tmp_path) is False


def test_ensure_up_writes_artifacts_block_and_marker(tmp_path, monkeypatch):
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(provision, "_compose", lambda wd, *a, extra_env=None: calls.append(a))
    monkeypatch.setattr(provision, "_wait_healthy", lambda retries: None)
    monkeypatch.setattr(provision, "_ensure_user", lambda user: None)
    monkeypatch.setattr(provision, "_mint_orchestrator_key", lambda user: "stub-orch-key")
    monkeypatch.setattr(
        provision,
        "gen_certs",
        lambda wd, hn, host_ip=None: (wd / "certs").mkdir(parents=True, exist_ok=True),
    )

    wd = tmp_path / "hs"
    conf = tmp_path / "config.toml"
    res = provision.ensure_up(wd, conf, hostname="headscale.local", host_ip="172.17.0.1")

    assert (wd / "config.yaml").exists() and (wd / "compose.yaml").exists()
    # the control plane is advertised BY IP (agent + routers join by IP, DERP too), now
    # on :443 so the tailscale client's forced-443 noise dial lands on the control plane.
    assert res.url == "https://172.17.0.1:443"
    assert res.host_alias == "headscale.local:172.17.0.1"
    assert res.advertise_host == "172.17.0.1"
    assert provision.is_owned(wd)
    assert 'headscale_url = "https://172.17.0.1:443"' in conf.read_text()
    # server_url in the rendered headscale config is the IP (embedded DERP derives from it)
    assert "server_url: https://172.17.0.1:443" in (wd / "config.yaml").read_text()
    # First compose up is headscale-only; second is all services with the router
    assert ("up", "-d", "headscale") in calls
    assert ("up", "-d") in calls


def test_render_compose_includes_orchestrator_router_when_route_given(tmp_path):
    out = provision.render_compose(
        tmp_path,
        "headscale.local",
        collector_route="172.17.0.1/32",
        host_ip="172.17.0.1",
    )
    assert "orchestrator-router" in out
    assert "TS_ROUTES=172.17.0.1/32" in out
    # the router joins BY IP (no extra_hosts hostname mapping); cert IP SAN validates it
    assert "--login-server=https://172.17.0.1:443" in out
    assert "headscale.local" not in out  # no hostname anywhere in the router stanza
    assert "extra_hosts" not in out
    assert "TS_USERSPACE=false" in out
    assert "${XORCISE_ORCH_AUTHKEY}" in out
    # Must NOT write a literal key value — only the env-interpolated placeholder
    assert "XORCISE_ORCH_AUTHKEY:" not in out or "${XORCISE_ORCH_AUTHKEY}" in out
    # Router must trust the self-signed CA
    assert "SSL_CERT_FILE=/etc/headscale/certs/ca.pem" in out
    # Certs dir must be mounted
    assert f"{tmp_path}/certs:/etc/headscale/certs:ro" in out
    # Kernel mode caps
    assert "NET_ADMIN" in out
    assert "NET_RAW" in out
    assert "/dev/net/tun" in out


def test_render_compose_no_router_without_collector_route(tmp_path):
    out = provision.render_compose(tmp_path, "headscale.local", host_ip="172.17.0.1")
    assert "orchestrator-router" not in out
    assert "XORCISE_ORCH_AUTHKEY" not in out


# topology-gated collector route
def test_collector_route_empty_for_local():
    assert _collector_route("172.17.0.1", "local") == ""


def test_collector_route_is_slash32_for_distributed():
    assert _collector_route("172.17.0.1", "distributed") == "172.17.0.1/32"


def test_render_compose_local_topology_omits_orchestrator_router(tmp_path):
    """local topology: _collector_route yields "", so render_compose emits no router."""
    out = provision.render_compose(
        tmp_path,
        "headscale.local",
        collector_route=_collector_route("172.17.0.1", "local"),
        host_ip="172.17.0.1",
    )
    assert "orchestrator-router" not in out
    assert "XORCISE_ORCH_AUTHKEY" not in out


def test_render_compose_router_has_no_extra_hosts_and_is_valid_yaml(tmp_path):
    """The router joins by IP, so the stanza carries NO extra_hosts (the old empty-list
    workaround is gone). The document must parse cleanly."""
    import yaml

    out = provision.render_compose(
        tmp_path, "headscale.local", collector_route="172.17.0.1/32", host_ip="172.17.0.1"
    )
    assert "extra_hosts" not in out
    doc = yaml.safe_load(out)  # parses without error
    assert "extra_hosts" not in doc["services"]["orchestrator-router"]


def test_mint_orchestrator_key_raises_when_user_not_found(monkeypatch):
    """Fix 2 (Minor): _mint_orchestrator_key raises ProvisionError when the user is absent."""
    import json

    class FakeResult:
        returncode = 0
        stdout = json.dumps([{"id": 1, "name": "someoneelse"}])
        stderr = ""

    monkeypatch.setattr(
        "xorcise.core.headscale.provision.subprocess.run", lambda *a, **kw: FakeResult()
    )
    with pytest.raises(ProvisionError, match="not found in Headscale"):
        provision._mint_orchestrator_key("orchestrator")


def test_teardown_only_when_owned(tmp_path, monkeypatch):
    issued = []
    monkeypatch.setattr(provision, "_compose", lambda wd, *a: issued.append(a))
    conf = tmp_path / "config.toml"
    conf.write_text("")
    assert provision.teardown(tmp_path / "hs", conf) is False  # no marker → no-op
    assert issued == []
    wd = tmp_path / "hs"
    provision.mark_owned(wd)
    assert provision.teardown(wd, conf) is True
    assert ("down", "-v") in issued
    assert provision.is_owned(wd) is False
