import re
import shutil
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
    # compose names the plain tag: _ensure_pinned_image has already verified what it names
    assert f"image: {provision.HEADSCALE_IMAGE_TAG}" in out
    assert ":stable" not in out  # neither service may float


def test_headscale_image_is_pinned_by_tag_and_digest():
    # #171: `:stable` handed fresh installs whatever upstream released last. The pin names a
    # release tag (what DockerExecHeadscaleCli targets) AND the multi-arch index digest.
    assert re.fullmatch(r"headscale/headscale:v\d+\.\d+\.\d+", provision.HEADSCALE_IMAGE_TAG)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", provision.HEADSCALE_IMAGE_DIGEST)
    assert (
        f"{provision.HEADSCALE_IMAGE_TAG}@{provision.HEADSCALE_IMAGE_DIGEST}"
    ) == provision.HEADSCALE_IMAGE_PIN


def test_pin_stays_on_the_release_line_the_cli_adapter_targets():
    # bumping the pin to another line means re-checking DockerExecHeadscaleCli's commands first
    from xorcise.core.headscale.cli import DockerExecHeadscaleCli

    release = provision.HEADSCALE_IMAGE_TAG.split(":", 1)[1]
    assert release.startswith(DockerExecHeadscaleCli.TARGET_RELEASE + "."), release
    assert provision.HEADSCALE_IMAGE_DIGEST in provision._HEADSCALE_IMAGE_IDS


class _Docker:
    """Records docker calls; answers `image inspect` from `ids`, everything else from `rc`."""

    def __init__(self, ids: dict[str, str], rc: dict[str, int] | None = None, out: str = ""):
        self.ids, self.rc, self.out = ids, rc or {}, out
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            ref = cmd[-1]
            return subprocess.CompletedProcess(
                cmd, 0 if ref in self.ids else 1, self.ids.get(ref, "")
            )
        if cmd[:2] == ["docker", "tag"]:
            self.ids[cmd[3]] = provision.HEADSCALE_IMAGE_DIGEST
        return subprocess.CompletedProcess(cmd, self.rc.get(cmd[1], 0), self.out, "boom")


_AMD64_ID = "sha256:46a8a7728a6fa2774e093313fa587922f68e1b56dc82f8276117050f27661939"


def test_ensure_pinned_image_uses_a_loaded_image_without_pulling(monkeypatch):
    # `docker load` keeps the image id but drops the repo digest: an offline host must still pass
    fake = _Docker({provision.HEADSCALE_IMAGE_TAG: _AMD64_ID})
    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", fake)
    provision._ensure_pinned_image()
    assert not [c for c in fake.calls if c[1] == "pull"]


def test_ensure_pinned_image_pulls_by_digest_and_tags_when_missing(monkeypatch):
    fake = _Docker({})
    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", fake)
    provision._ensure_pinned_image()
    assert ["docker", "pull", provision.HEADSCALE_IMAGE_PIN] in fake.calls
    # pulled by digest it is stored untagged (dangling); the tag keeps `image prune` off it
    assert [
        "docker",
        "tag",
        provision.HEADSCALE_IMAGE_PIN,
        provision.HEADSCALE_IMAGE_TAG,
    ] in fake.calls


def test_ensure_pinned_image_replaces_a_tag_that_names_another_build(monkeypatch):
    fake = _Docker({provision.HEADSCALE_IMAGE_TAG: "sha256:" + "0" * 64})
    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", fake)
    provision._ensure_pinned_image()
    assert ["docker", "pull", provision.HEADSCALE_IMAGE_PIN] in fake.calls


def test_ensure_pinned_image_explains_the_offline_route_when_the_pull_fails(monkeypatch):
    fake = _Docker({}, rc={"pull": 1})
    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", fake)
    with pytest.raises(ProvisionError, match="docker load"):
        provision._ensure_pinned_image()


def _container_on(image_id: str, version_out: str, monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0 if image_id else 1, image_id, "")
        return subprocess.CompletedProcess(cmd, 0, version_out, "")

    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", _run)
    return calls


def test_refuses_to_recreate_a_newer_headscale_on_the_older_pin(monkeypatch):
    # migrations only go forward: v0.30 data on the v0.29.5 pin would not start
    _container_on("sha256:" + "1" * 64, "headscale version v0.30.0\ncommit: x", monkeypatch)
    with pytest.raises(ProvisionError, match=r"v0\.30\.0.*xorcise down"):
        provision._refuse_headscale_downgrade()


def test_allows_moving_an_older_headscale_up_to_the_pin(monkeypatch):
    _container_on("sha256:" + "1" * 64, "headscale version v0.29.1", monkeypatch)
    provision._refuse_headscale_downgrade()


def test_downgrade_guard_is_silent_without_a_container_or_a_version(monkeypatch):
    calls = _container_on("", "", monkeypatch)
    provision._refuse_headscale_downgrade()
    assert len(calls) == 1  # no container: nothing run
    _container_on("sha256:" + "1" * 64, "headscale version dev", monkeypatch)
    provision._refuse_headscale_downgrade()


def test_downgrade_guard_does_not_run_the_pinned_image(monkeypatch):
    calls = _container_on(_AMD64_ID, "", monkeypatch)
    provision._refuse_headscale_downgrade()
    assert [c[:2] for c in calls] == [["docker", "inspect"]]


def test_compose_mounts_keep_private_keys_out_of_the_router(tmp_path):
    """#171: the router reads only ca.pem, so it must not see the server's TLS key; no service
    may see the CA key or the directory that holds it."""
    import yaml

    out = provision.render_compose(
        tmp_path, "headscale.local", collector_route="172.17.0.1/32", host_ip="172.17.0.1"
    )
    services = yaml.safe_load(out)["services"]
    ca_dir = str(provision.ca_key_path(tmp_path).parent)
    for name, svc in services.items():
        for vol in svc.get("volumes", []):
            source = vol.split(":")[0]
            assert not source.startswith(ca_dir), (name, vol)
            assert not source.endswith("ca.key"), (name, vol)
    assert services["orchestrator-router"]["volumes"] == [
        f"{tmp_path}/certs/ca.pem:/etc/headscale/certs/ca.pem:ro"
    ]
    assert f"{tmp_path}/certs:/etc/headscale/certs:ro" in services["headscale"]["volumes"]


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


def test_bridge_gateway_lookup_is_bounded_and_a_hung_docker_is_a_typed_error(monkeypatch):
    """Review of #99. `doctor` now reads the current host address through default_host_ip, and this
    subprocess had no timeout — a daemon that accepts the connection but never answers hung the
    whole diagnosis (25 s cap hit, one line of output). Bounded like doctor's other Docker probes;
    the expiry is a ProvisionError so `up` fails loud and `_current_host_ip` degrades to None."""
    import subprocess

    seen: list[dict[str, object]] = []

    def _hung(cmd, **kwargs):
        seen.append(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.delenv("XORCISE_HEADSCALE_HOST_IP", raising=False)
    monkeypatch.setattr(provision, "_host_is_macos", lambda: False)
    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", _hung)
    with pytest.raises(ProvisionError, match="did not answer"):
        default_host_ip()
    assert seen and seen[0].get("timeout") == provision._PROBE_TIMEOUT


def test_macos_address_probes_degrade_on_a_hung_tool(monkeypatch):
    # networksetup/ipconfig that never return read as "no hardware port / no address", so
    # _macos_lan_ipv4 falls through to the routing-table probe instead of hanging `up` or `doctor`.
    import subprocess

    def _hung(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr("xorcise.core.headscale.provision.subprocess.run", _hung)
    assert provision._macos_hardware_devices() == []
    assert provision._macos_interface_ipv4("en0") == ""
    assert provision._macos_lan_ipv4() is None


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


def _cert_text(crt: Path) -> str:
    return subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-text", "-noout"], capture_output=True, text=True
    ).stdout


def test_gen_certs_writes_ca_and_server_cert(issued):
    ca, crt, key = provision.cert_paths(issued, "headscale.local")
    assert ca.exists() and crt.exists() and key.exists()
    assert "headscale.local" in _cert_text(crt)


def test_gen_certs_adds_ip_san_so_by_ip_join_validates(issued):
    """The server cert carries the bootstrap IP as a SAN (alongside the DNS name), so a
    tailnet join against https://<ip>:8443 passes TLS without any /etc/hosts hostname mapping."""
    txt = _cert_text(issued / "certs" / "headscale.local.crt")
    assert "DNS:headscale.local" in txt  # hostname SAN kept (router join still validates)
    assert "IP Address:172.17.0.1" in txt  # IP SAN for the by-IP agent join


def test_gen_certs_noop_when_ip_san_unchanged(issued):
    """The freshness checks must not cause needless churn — a second call with the same
    host_ip is an idempotent no-op (the cached CA/cert/key bytes are untouched)."""
    ca, crt, key = provision.cert_paths(issued, "headscale.local")
    before = (ca.read_bytes(), crt.read_bytes(), key.read_bytes())
    assert gen_certs(issued, "headscale.local", host_ip="172.17.0.1") is False
    assert (ca.read_bytes(), crt.read_bytes(), key.read_bytes()) == before


def _verifies(ca: Path, crt: Path) -> bool:
    return (
        subprocess.run(
            ["openssl", "verify", "-CAfile", str(ca), str(crt)], capture_output=True
        ).returncode
        == 0
    )


def _serial(crt: Path) -> str:
    return subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-noout", "-serial"], capture_output=True, text=True
    ).stdout


@pytest.fixture(scope="module")
def _issued_once(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # RSA-4096 generation dominates this file's runtime: issue one CA + server cert per module
    # and hand each test a copy (copytree keeps the 0700/0600 modes).
    wd = tmp_path_factory.mktemp("issued")
    gen_certs(wd, "headscale.local", host_ip="172.17.0.1")
    return wd


@pytest.fixture
def issued(_issued_once: Path, tmp_path: Path) -> Path:
    wd = tmp_path / "hs"
    shutil.copytree(_issued_once, wd)
    return wd


def test_gen_certs_keeps_the_ca_key_out_of_the_mounted_certs_dir(issued):
    # #171: certs/ is bind-mounted into the Headscale container; the CA key must not be there.
    key = provision.ca_key_path(issued)
    assert key == issued / "ca" / "ca.key"
    assert key.exists()
    assert not (issued / "certs" / "ca.key").exists()
    assert key.stat().st_mode & 0o777 == 0o600
    assert key.parent.stat().st_mode & 0o777 == 0o700
    # nothing else (serial file, CSR, SAN config) is left behind
    assert sorted(p.name for p in (issued / "certs").iterdir()) == [
        "ca.pem",
        "headscale.local.crt",
        "headscale.local.key",
    ]
    assert [p.name for p in key.parent.iterdir()] == ["ca.key"]


def test_gen_certs_resigns_with_the_same_ca_when_the_ip_changes(issued):
    """A host IP change re-signs only the server cert: ca.pem stays byte-identical, so runs
    and routers that already trust it keep validating the new cert."""
    ca, crt, _ = provision.cert_paths(issued, "headscale.local")
    ca_before = ca.read_bytes()
    key_before = provision.ca_key_path(issued).read_bytes()
    serial_before = _serial(crt)
    assert gen_certs(issued, "headscale.local", host_ip="10.0.0.5") is True
    assert ca.read_bytes() == ca_before
    assert provision.ca_key_path(issued).read_bytes() == key_before
    txt = _cert_text(crt)
    assert "IP Address:10.0.0.5" in txt and "IP Address:172.17.0.1" not in txt
    assert _verifies(ca, crt)
    assert _serial(crt) != serial_before  # a reused CA never repeats a serial


def test_gen_certs_migrates_a_legacy_ca_key_without_rotating(issued):
    # an install from before #171 has ca.key inside certs/; it moves out, and the CA is kept
    ca, crt, _ = provision.cert_paths(issued, "headscale.local")
    key = provision.ca_key_path(issued)
    legacy = issued / "certs" / "ca.key"
    key.replace(legacy)
    key.parent.rmdir()
    ca_before, crt_before = ca.read_bytes(), crt.read_bytes()

    assert gen_certs(issued, "headscale.local", host_ip="172.17.0.1") is False

    assert not legacy.exists()
    assert key.exists() and key.stat().st_mode & 0o777 == 0o600
    assert key.parent.stat().st_mode & 0o777 == 0o700
    assert ca.read_bytes() == ca_before  # not rotated
    assert crt.read_bytes() == crt_before  # nothing else to do: cert still current


def test_gen_certs_removes_the_legacy_copy_when_both_exist(issued):
    key = provision.ca_key_path(issued)
    legacy = issued / "certs" / "ca.key"
    legacy.write_bytes(key.read_bytes())
    gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert not legacy.exists() and key.exists()


def test_gen_certs_mints_a_new_ca_when_its_key_is_lost(issued):
    # without the matching key the CA can never sign again, so a fresh pair is the only way on
    ca, crt, _ = provision.cert_paths(issued, "headscale.local")
    ca_before = ca.read_bytes()
    provision.ca_key_path(issued).unlink()
    assert gen_certs(issued, "headscale.local", host_ip="172.17.0.1") is True
    assert ca.read_bytes() != ca_before
    assert _verifies(ca, crt)


def _genrsa(path: Path) -> Path:
    subprocess.run(
        ["openssl", "genrsa", "-out", str(path), "2048"], check=True, capture_output=True
    )
    return path


def test_ca_reusable_only_for_its_own_unexpired_key(issued, tmp_path):
    ca, _, _ = provision.cert_paths(issued, "headscale.local")
    assert provision._ca_reusable(ca, provision.ca_key_path(issued))
    assert not provision._ca_reusable(ca, _genrsa(tmp_path / "stray.key"))
    assert not provision._ca_reusable(ca, tmp_path / "missing.key")
    # a CA inside its last 30 days is re-minted: every cert it signs would die with it
    short_key = _genrsa(tmp_path / "short.key")
    short = tmp_path / "short.pem"
    _args = ["-x509", "-new", "-key", str(short_key), "-days", "5", "-subj", "/CN=t"]
    subprocess.run(["openssl", "req", *_args, "-out", str(short)], check=True, capture_output=True)
    assert not provision._ca_reusable(short, short_key)


def test_an_unreadable_ca_raises_instead_of_rotating(issued):
    # a failed probe is not a mismatch: rotating on it would re-mint the CA on every `up`
    ca, _, _ = provision.cert_paths(issued, "headscale.local")
    ca_before = ca.read_bytes()
    provision.ca_key_path(issued).write_text("not a key\n")
    with pytest.raises(ProvisionError, match="delete both files"):
        gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert ca.read_bytes() == ca_before


def test_gen_certs_heals_a_server_key_that_no_longer_matches_its_cert(issued):
    # a regeneration cut off after the key was replaced must not be cached as current
    ca, crt, key = provision.cert_paths(issued, "headscale.local")
    ca_before = ca.read_bytes()
    _genrsa(key)
    gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert provision._cert_public_key(crt) == provision._key_public_key(key)
    assert ca.read_bytes() == ca_before


def test_gen_certs_heals_a_server_cert_from_another_ca(issued, tmp_path):
    # a CA replaced without its server cert (cut off between renames) is caught by the chain check
    ca, crt, key = provision.cert_paths(issued, "headscale.local")
    other_key, other_ca = _genrsa(tmp_path / "o.key"), tmp_path / "o.pem"
    _x509 = ["-x509", "-new", "-key", str(other_key), "-days", "30", "-subj", "/CN=o"]
    subprocess.run(
        ["openssl", "req", *_x509, "-out", str(other_ca)], check=True, capture_output=True
    )
    _genrsa(key)
    csr = tmp_path / "s.csr"
    _req = ["req", "-new", "-key", str(key), "-subj", "/CN=headscale.local", "-out", str(csr)]
    subprocess.run(["openssl", *_req], check=True, capture_output=True)
    _sign = ["x509", "-req", "-in", str(csr), "-CA", str(other_ca), "-CAkey", str(other_key)]
    subprocess.run(
        ["openssl", *_sign, "-set_serial", "1", "-days", "30", "-out", str(crt)],
        check=True,
        capture_output=True,
    )
    assert not _verifies(ca, crt)  # precondition: a self-consistent pair from another CA
    gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert _verifies(ca, crt)


def test_gen_certs_failing_mid_way_leaves_the_served_files_untouched(issued, monkeypatch):
    # staged generation: a failed sign must not leave a new key beside the old cert
    ca, crt, key = provision.cert_paths(issued, "headscale.local")
    before = (ca.read_bytes(), crt.read_bytes(), key.read_bytes())
    real = provision._openssl

    def _fail_on_sign(*args: str) -> str:
        if args[:2] == ("x509", "-req"):
            raise ProvisionError("sign failed")
        return real(*args)

    monkeypatch.setattr(provision, "_openssl", _fail_on_sign)
    with pytest.raises(ProvisionError, match="sign failed"):
        gen_certs(issued, "headscale.local", host_ip="10.0.0.5")
    assert (ca.read_bytes(), crt.read_bytes(), key.read_bytes()) == before
    assert not list(issued.glob(".certs-staging-*"))


def test_gen_certs_clears_a_staging_dir_left_by_a_killed_run(issued):
    stale = issued / ".certs-staging-dead"
    stale.mkdir()
    (stale / "ca.key").write_text("x")
    gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert not stale.exists()


def test_gen_certs_keeps_the_legacy_key_when_it_is_the_one_that_pairs(issued, tmp_path):
    # upgrade, downgrade (old release re-mints into certs/), upgrade again: the ca/ key is stale
    ca, _, _ = provision.cert_paths(issued, "headscale.local")
    key = provision.ca_key_path(issued)
    legacy = issued / "certs" / "ca.key"
    legacy.write_bytes(key.read_bytes())
    _genrsa(key)  # ca/ now holds a key that does not pair with ca.pem
    ca_before = ca.read_bytes()
    gen_certs(issued, "headscale.local", host_ip="172.17.0.1")
    assert not legacy.exists()
    assert ca.read_bytes() == ca_before  # kept the pair: no needless rotation
    assert provision._ca_reusable(ca, key)


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


def _stub_ensure_up(monkeypatch, *, renewed: bool) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def _gen_certs(wd: Path, hn: str, host_ip: str | None = None) -> bool:
        (wd / "certs").mkdir(parents=True, exist_ok=True)
        calls.append(("certs",))
        return renewed

    monkeypatch.setattr(provision, "_compose", lambda wd, *a, extra_env=None: calls.append(a))
    monkeypatch.setattr(provision, "_wait_healthy", lambda retries: None)
    monkeypatch.setattr(provision, "_ensure_user", lambda user: None)
    monkeypatch.setattr(provision, "_mint_orchestrator_key", lambda user: "stub-orch-key")
    monkeypatch.setattr(provision, "_refuse_headscale_downgrade", lambda: calls.append(("guard",)))
    monkeypatch.setattr(provision, "_ensure_pinned_image", lambda: calls.append(("image",)))
    monkeypatch.setattr(provision, "gen_certs", _gen_certs)
    return calls


def test_ensure_up_writes_artifacts_block_and_marker(tmp_path, monkeypatch):
    calls = _stub_ensure_up(monkeypatch, renewed=False)

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
    # the downgrade guard and the image check both run before the certs are touched, and
    # unchanged certs leave a running Headscale alone
    assert calls[:4] == [("guard",), ("image",), ("certs",), ("up", "-d", "headscale")]


def test_ensure_up_recreates_headscale_when_its_cert_was_replaced(tmp_path, monkeypatch):
    # Headscale reads its cert only at startup and compose sees no config change, so without a
    # forced recreate it keeps serving the old cert while the router trusts only the new ca.pem.
    calls = _stub_ensure_up(monkeypatch, renewed=True)
    provision.ensure_up(
        tmp_path / "hs", tmp_path / "config.toml", hostname="headscale.local", host_ip="172.17.0.1"
    )
    assert ("up", "-d", "--force-recreate", "headscale") in calls
    assert ("up", "-d", "headscale") not in calls


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
    # The router mounts ca.pem alone (see test_compose_mounts_keep_private_keys_out_of_the_router)
    assert f"{tmp_path}/certs/ca.pem:/etc/headscale/certs/ca.pem:ro" in out
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
