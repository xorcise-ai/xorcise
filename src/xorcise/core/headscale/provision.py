"""Local Headscale provisioner (headscale part-island).

The single source for the local air-gapped Headscale (replaces the former deploy/headscale/*
scripts): renders config.yaml + compose.yaml from ONE
hostname var (they cannot diverge), generates certs via openssl, runs
`docker compose` for project `xorcise-headscale`, writes the managed config block, and tracks
ownership with a marker. Takes filesystem paths as arguments — it must NOT import
xorcise.core.home (a domain module above this part-island; the layer rule forbids the
upward import).
"""

from __future__ import annotations

import ipaddress
import os
import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT = "xorcise-headscale"
CONTAINER = "headscale"
_DATA_VOLUME = "xorcise-headscale-data"
_BEGIN = "# >>> xorcise headscale (managed) >>>"
_END = "# <<< xorcise headscale (managed) <<<"


class ProvisionError(RuntimeError):
    pass


def cert_paths(workdir: Path, hostname: str) -> tuple[Path, Path, Path]:
    certs = workdir / "certs"
    return certs / "ca.pem", certs / f"{hostname}.crt", certs / f"{hostname}.key"


def _cert_has_ip_san(crt: Path, host_ip: str) -> bool:
    """True if the server cert already carries *host_ip* as an IP SAN (freshness check)."""
    out = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-noout", "-text"],
        capture_output=True,
        text=True,
    )
    return f"IP Address:{host_ip}" in out.stdout


def _valid_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return False


def _host_is_macos() -> bool:
    """True on a macOS host. There the Docker engine always runs inside a LinuxKit VM (Docker
    Desktop), so the docker bridge gateway is VM-internal and NOT bindable on the host — the
    single-host bridge-gateway assumption only holds on native Linux."""
    return platform.system() == "Darwin"


def _macos_hardware_devices() -> list[str]:
    """macOS hardware network devices (en0, en1, …) in service-priority order.

    `networksetup -listnetworkserviceorder` lists services in the user's configured priority
    (Ethernet before Wi-Fi, …), each with its BSD device name. VPN/tunnel pseudo-interfaces
    (utun*, ppp*, …) are never hardware ports, so they never appear here — which is exactly why
    enumerating these cannot be poisoned by a tunnel that owns the default route."""
    out = subprocess.run(
        ["networksetup", "-listnetworkserviceorder"], capture_output=True, text=True
    )
    return re.findall(r"Device:\s*([A-Za-z0-9]+)\)", out.stdout)


def _macos_interface_ipv4(device: str) -> str:
    """The IPv4 bound to a macOS hardware *device*, or "" if none. `ipconfig getifaddr` returns an
    address ONLY for a configured hardware port — it is blank for utun/VPN tunnels — so a tunnel's
    tailnet IP can never leak through this path."""
    out = subprocess.run(["ipconfig", "getifaddr", device], capture_output=True, text=True)
    return out.stdout.strip()


def _macos_lan_ipv4() -> str | None:
    """The host's physical LAN IPv4 on macOS, robust against a VPN/Tailscale default route.

    Walks the hardware ports in service-priority order and returns the first with a real,
    non-loopback IPv4. Tunnels are excluded structurally — they are not hardware ports and
    `ipconfig getifaddr` is blank for them — so a full-tunnel default route cannot make this
    return a tailnet address the way the routing-table probe below does."""
    for device in _macos_hardware_devices():
        ip = _macos_interface_ipv4(device)
        if _valid_ipv4(ip) and not ip.startswith("127."):
            return ip
    return None


def _primary_lan_ipv4() -> str | None:
    """The host's primary PHYSICAL LAN IPv4, or None if it can't be determined.

    On macOS, prefer hardware-port enumeration and NEVER a tunnel IP: the host frequently runs
    Tailscale (or joins its own headscale while testing), whose full-tunnel default route makes the
    routing-table probe below return the *tailnet* IP (e.g. 10.8.x on utun) instead of the LAN IP.
    Baking that into the control plane leaves a joining agent chasing an address only reachable once
    it is already on the tailnet — a chicken-and-egg that hangs `tailscale up` and fails the join.

    Fallback (and the notional Linux path, though default_host_ip only calls this on macOS): a UDP
    socket routing-table lookup. connect() on a datagram socket sends NO packets — it only resolves
    which local interface would carry traffic to the target — so it works offline and needs no
    egress. 192.0.2.1 is RFC 5737 TEST-NET-1. Loopback / non-IPv4 results are rejected."""
    if _host_is_macos():
        ip = _macos_lan_ipv4()
        if ip:
            return ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if _valid_ipv4(ip) and not ip.startswith("127.") else None


def _docker_bridge_gateway() -> str:
    """The docker bridge network's gateway IP (a real host interface on native Linux)."""
    out = subprocess.run(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def default_host_ip(override: str | None = None) -> str:
    """The host as seen from inside a container. Overridable via arg / env; otherwise derived per
    platform: the docker bridge gateway on native Linux (the single-host assumption), or the
    host's primary LAN IPv4 on macOS, where the bridge gateway is VM-internal
    and not host-bindable. The chosen value must be BOTH host-bindable and container-reachable (it
    feeds the compose publish bind, the cert IP SAN, server_url/DERP, and the router login URL)."""
    candidate = override or os.environ.get("XORCISE_HEADSCALE_HOST_IP")
    if candidate:
        if not _valid_ipv4(candidate):
            raise ProvisionError(f"invalid host IP {candidate!r} (want a dotted IPv4)")
        return candidate
    if _host_is_macos():
        ip = _primary_lan_ipv4()
        if not ip:
            raise ProvisionError(
                "could not determine a host-bindable IP on macOS — the docker bridge gateway runs "
                "inside the Docker Desktop VM and is not bindable on the host. Set "
                "XORCISE_HEADSCALE_HOST_IP to your host's LAN IP."
            )
        return ip
    ip = _docker_bridge_gateway()
    if not _valid_ipv4(ip):
        raise ProvisionError(
            "could not determine the docker bridge gateway IP — set XORCISE_HEADSCALE_HOST_IP"
        )
    return ip


def gen_certs(workdir: Path, hostname: str, host_ip: str | None = None) -> None:
    """Self-signed CA + SAN-signed server cert into workdir/certs/ (idempotent).

    When *host_ip* is given it is added as an IP SAN alongside the DNS name, so the
    tailnet join can target https://<ip>:443 and pass TLS without any /etc/hosts hostname
    mapping. The DNS SAN is kept so the router's hostname-based join keeps validating."""
    ca, crt, key = cert_paths(workdir, hostname)
    if ca.exists() and crt.exists() and (not host_ip or _cert_has_ip_san(crt, host_ip)):
        # keep the cached cert only when it still carries the desired IP SAN. A changed
        # host IP (DHCP / new network on macOS) must regenerate, or the by-IP TLS join would break.
        return
    certs = workdir / "certs"
    certs.mkdir(parents=True, exist_ok=True)
    ca_key = certs / "ca.key"
    csr = certs / "server.csr"
    san = certs / "san.cnf"

    def _ossl(*args: str) -> None:
        r = subprocess.run(["openssl", *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise ProvisionError(f"openssl {' '.join(args)} failed:\n{r.stderr}")

    _ossl("genrsa", "-out", str(ca_key), "4096")
    _ossl(
        "req",
        "-x509",
        "-new",
        "-nodes",
        "-key",
        str(ca_key),
        "-sha256",
        "-days",
        "3650",
        "-subj",
        "/CN=XORCISE Headscale CA",
        "-out",
        str(ca),
    )
    _ossl("genrsa", "-out", str(key), "4096")
    _ossl("req", "-new", "-key", str(key), "-subj", f"/CN={hostname}", "-out", str(csr))
    alt = f"DNS:{hostname}"
    if host_ip:
        alt += f",IP:{host_ip}"  # by-IP join validates without a hosts mapping
    san.write_text(f"subjectAltName={alt}\n")
    _ossl(
        "x509",
        "-req",
        "-in",
        str(csr),
        "-CA",
        str(ca),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-days",
        "3650",
        "-sha256",
        "-extfile",
        str(san),
        "-out",
        str(crt),
    )
    for f in (csr, san, certs / "ca.srl"):
        f.unlink(missing_ok=True)


def _strip_block_text(text: str) -> str:
    return re.sub(rf"\n?{re.escape(_BEGIN)}.*?{re.escape(_END)}\n?", "\n", text, flags=re.DOTALL)


def write_managed_block(
    config_path: Path, *, url: str, ca_cert: str, host_alias: str, advertise_host: str
) -> None:
    """Write/replace the managed block, preserving any other config (idempotent)."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    base = _strip_block_text(config_path.read_text()) if config_path.exists() else ""
    base = base.rstrip("\n")
    block = (
        f"{_BEGIN}\n"
        f'headscale_url = "{url}"\n'
        f'headscale_ca_cert = "{ca_cert}"\n'
        f'headscale_host_alias = "{host_alias}"\n'
        f'headscale_advertise_host = "{advertise_host}"\n'
        f"{_END}\n"
    )
    config_path.write_text((base + "\n\n" if base else "") + block)


def strip_managed_block(config_path: Path) -> None:
    if config_path.exists():
        config_path.write_text(_strip_block_text(config_path.read_text()).lstrip("\n"))


def managed_url(config_path: Path) -> str:
    """The headscale_url OUR managed block wrote, or "" when there is no managed block.

    Lets `up` tell its own provisioned control plane apart from an operator-chosen remote.
    Once merged into Settings the two are indistinguishable, and both confusions are harmful:
    reading ours as "external" makes `up` skip provisioning after a reboot (then every run
    creation 503s), while reading an operator's remote as ours silently ignores an explicit
    `config set-network --headscale-url`. Comparing against the value we wrote distinguishes
    them exactly, where a mere ownership marker cannot.
    """
    if not config_path.exists():
        return ""
    block = re.search(
        rf"{re.escape(_BEGIN)}(.*?){re.escape(_END)}", config_path.read_text(), flags=re.DOTALL
    )
    if not block:
        return ""
    found = re.search(r'^\s*headscale_url\s*=\s*"([^"]*)"', block.group(1), flags=re.MULTILINE)
    return found.group(1) if found else ""


def _marker(workdir: Path) -> Path:
    return workdir / ".owned"


def mark_owned(workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    _marker(workdir).write_text("xorcise-headscale\n")


def is_owned(workdir: Path) -> bool:
    return _marker(workdir).exists()


def clear_owned(workdir: Path) -> None:
    _marker(workdir).unlink(missing_ok=True)


def render_config(hostname: str, host_ip: str = "") -> str:
    """Headscale config.yaml — TLS + embedded DERP (region 999), database policy mode.

    server_url points at the control-plane IP when *host_ip* is given: the embedded DERP
    region auto-derives its HostName from server_url, so a by-IP server_url makes DERP reachable by
    IP too — no /etc/hosts mapping on the agent. TLS still serves the {hostname} cert (it carries an
    IP SAN), so both the by-IP and by-hostname joins validate. Falls back to the hostname when
    host_ip is empty (back-compat).

    Listener scoping: listen_addr + stun_listen_addr must stay on the container wildcard —
    docker-proxy delivers published traffic to the container IP, which a loopback bind would
    refuse. metrics + gRPC are neither published nor dialed from outside the container (the
    provisioner CLI goes through docker exec + the unix socket), so they bind the container
    loopback and stay invisible even to neighbors on the compose network."""
    server_host = host_ip or hostname
    return f"""\
server_url: https://{server_host}:443
listen_addr: 0.0.0.0:8443
metrics_listen_addr: 127.0.0.1:9090
grpc_listen_addr: 127.0.0.1:50443
grpc_allow_insecure: false
tls_cert_path: /etc/headscale/certs/{hostname}.crt
tls_key_path: /etc/headscale/certs/{hostname}.key
noise:
  private_key_path: /var/lib/headscale/noise_private.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential
derp:
  server:
    enabled: true
    region_id: 999
    region_code: headscale
    region_name: Headscale Embedded DERP
    stun_listen_addr: 0.0.0.0:3478
    private_key_path: /var/lib/headscale/derp_server_private.key
    automatically_add_embedded_derp_region: true
  urls: []
  paths: []
  auto_update_enabled: false
  update_frequency: 24h
disable_check_updates: true
database:
  type: sqlite
  sqlite:
    path: /var/lib/headscale/db.sqlite
log:
  level: info
  format: text
policy:
  mode: database
dns:
  magic_dns: false
  override_local_dns: false
  base_domain: mission.local
  nameservers:
    global: []
"""


def _collector_route(ip: str, topology: str) -> str:
    """The /32 the orchestrator-router advertises for the collector.

    Only the distributed topology routes the collector over the tailnet; local
    reaches it directly via the docker host-gateway, so no route/router.
    """
    return f"{ip}/32" if topology == "distributed" else ""


def render_compose(
    workdir: Path,
    hostname: str,
    *,
    collector_route: str = "",
    router_image: str = "tailscale/tailscale:stable",
    host_ip: str = "",
) -> str:
    """compose.yaml with absolute volume paths under `workdir`.

    When `collector_route` is non-empty, a persistent `orchestrator-router` Tailscale service is
    appended. The service runs in kernel mode (TS_USERSPACE=false + NET_ADMIN + /dev/net/tun),
    advertises `collector_route` as a subnet route, and joins the local Headscale. The orchestrator
    pre-auth key is NOT written to the file — it stays as `${XORCISE_ORCH_AUTHKEY}` so `compose up`
    interpolates it from the subprocess env at runtime (same pattern as the per-run router's
    `${XORCISE_AUTHKEY}` in netoverride.py).
    """
    if not host_ip:
        # ensure_up always resolves a real IP via default_host_ip (or raises), so an empty
        # host_ip here is a caller bug — refuse rather than publish 443 + 3478/udp host-wide.
        raise ProvisionError(
            "refusing to render the Headscale compose without a host IP — the control plane "
            "and STUN publishes would bind all interfaces. Set XORCISE_HEADSCALE_HOST_IP."
        )
    # Publish the control plane on host 443 → container 8443 (listen_addr). 443 is the port the
    # tailscale client forces for its noise/register dial, so it must answer there. Both publishes
    # bind the advertised IP only (the host-launched agent + routers dial the plane there; peers
    # dial STUN on the same IP via server_url/DERP) so provisioning never grabs a host-wide port —
    # the :443 collision that 8443 originally avoided.
    control_publish = f'"{host_ip}:443:8443"'
    stun_publish = f'"{host_ip}:3478:3478/udp"'
    router_stanza = ""
    if collector_route:
        # the orchestrator-router joins the local control plane BY IP (cert has an IP SAN),
        # so it needs no extra_hosts mapping.
        login_host = host_ip
        router_stanza = f"""
  orchestrator-router:
    image: {router_image}
    cap_add:
      - NET_ADMIN
      - NET_RAW
    devices:
      - "/dev/net/tun:/dev/net/tun"
    environment:
      - TS_EXTRA_ARGS=--login-server=https://{login_host}:443
      - TS_AUTHKEY=${{XORCISE_ORCH_AUTHKEY}}
      - TS_ROUTES={collector_route}
      - TS_USERSPACE=false
      - TS_HOSTNAME=xorcise-orchestrator
      - TS_STATE_DIR=/var/lib/tailscale
      - SSL_CERT_FILE=/etc/headscale/certs/ca.pem
    volumes:
      - {workdir}/certs:/etc/headscale/certs:ro
    restart: unless-stopped"""

    return f"""\
services:
  headscale:
    image: headscale/headscale:stable
    container_name: {CONTAINER}
    command: serve
    restart: unless-stopped
    ports:
      - {control_publish}
      - {stun_publish}
    environment:
      SSL_CERT_FILE: /etc/headscale/certs/ca.pem
    volumes:
      - {workdir}/config.yaml:/etc/headscale/config.yaml:ro
      - {workdir}/certs:/etc/headscale/certs:ro
      - {_DATA_VOLUME}:/var/lib/headscale
    healthcheck:
      test: ["CMD", "headscale", "health"]
      interval: 5s
      timeout: 5s
      retries: 12{router_stanza}
volumes:
  {_DATA_VOLUME}:
"""


@dataclass(frozen=True)
class ProvisionResult:
    url: str
    ca_cert: str
    host_alias: str
    advertise_host: str


def _compose(workdir: Path, *args: str, extra_env: dict[str, str] | None = None) -> None:
    env = {**os.environ, **(extra_env or {})}
    r = subprocess.run(
        ["docker", "compose", "-f", str(workdir / "compose.yaml"), "-p", PROJECT, *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if r.returncode != 0:
        raise ProvisionError(f"docker compose {' '.join(args)} failed:\n{r.stderr or r.stdout}")


def _wait_healthy(retries: int) -> None:
    for _ in range(retries):
        ok = subprocess.run(
            ["docker", "exec", CONTAINER, "headscale", "health"], capture_output=True
        )
        if ok.returncode == 0:
            return
        time.sleep(2)
    logs = subprocess.run(
        ["docker", "logs", "--tail", "40", CONTAINER], capture_output=True, text=True
    )
    raise ProvisionError(f"Headscale did not become healthy:\n{logs.stdout}{logs.stderr}")


def _ensure_user(user: str) -> None:
    listed = subprocess.run(
        ["docker", "exec", CONTAINER, "headscale", "users", "list", "-o", "json"],
        capture_output=True,
        text=True,
    ).stdout
    if f'"{user}"' not in listed:
        subprocess.run(
            ["docker", "exec", CONTAINER, "headscale", "users", "create", user],
            capture_output=True,
        )


def _mint_orchestrator_key(orchestrator_user: str) -> str:
    """Mint a reusable 24-hour pre-auth key for the orchestrator router node.

    Mirrors DockerExecHeadscaleCli.create_preauth_key:
      1. Fetch the numeric user id via `headscale users list -o json` (Headscale v0.28.x
         requires a numeric uid for `preauthkeys create -u`).
      2. Run `headscale preauthkeys create -u {uid} -e 24h --reusable -o json`.
      3. Parse and return `data["key"]`.

    The returned key must ONLY be passed as an env var to `_compose` — never written to disk.
    """
    import json as _json

    users_out = subprocess.run(
        ["docker", "exec", CONTAINER, "headscale", "users", "list", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if users_out.returncode != 0:
        raise ProvisionError(f"headscale users list failed:\n{users_out.stderr}")
    users: list[dict[str, object]] = _json.loads(users_out.stdout.strip() or "[]")
    uid: int | None = None
    for u in users:
        if u.get("name") == orchestrator_user:
            uid = int(str(u["id"]))
            break
    if uid is None:
        raise ProvisionError(
            f"Cannot mint orchestrator key: user {orchestrator_user!r} not found in Headscale"
        )
    key_out = subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER,
            "headscale",
            "preauthkeys",
            "create",
            "-u",
            str(uid),
            "-e",
            "24h",
            "--reusable",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if key_out.returncode != 0:
        raise ProvisionError(f"headscale preauthkeys create failed:\n{key_out.stderr}")
    data: dict[str, object] = _json.loads(key_out.stdout.strip())
    return str(data["key"])


def ensure_up(
    workdir: Path,
    config_path: Path,
    *,
    hostname: str = "headscale.local",
    host_ip: str | None = None,
    orchestrator_user: str = "orchestrator",
    health_retries: int = 30,
    deployment_topology: str = "local",
) -> ProvisionResult:
    """Provision a local Headscale (idempotent): render artifacts, gen certs, compose up,
    wait healthy, ensure the orchestrator user, write the managed block, mark ownership.

    Two-phase start: headscale is brought up first (`compose up -d headscale`), waited healthy,
    the orchestrator user is ensured, and a pre-auth key is minted in memory; then all services
    (including the orchestrator router) are started in a second `compose up -d` with
    XORCISE_ORCH_AUTHKEY injected via subprocess env so the key is never written to disk.
    """
    ip = default_host_ip(host_ip)
    collector_route = _collector_route(ip, deployment_topology)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "config.yaml").write_text(render_config(hostname, ip))  # server_url by IP
    (workdir / "compose.yaml").write_text(
        render_compose(workdir, hostname, collector_route=collector_route, host_ip=ip)
    )
    gen_certs(workdir, hostname, host_ip=ip)  # IP SAN → by-IP tailnet join
    # Step 2: Bring up headscale only first so we can wait for it to be healthy before minting
    # the orchestrator key. The router service is declared in compose.yaml but not started yet.
    _compose(workdir, "up", "-d", "headscale")
    # Step 3: Wait for headscale to be ready.
    _wait_healthy(health_retries)
    # Step 4: Ensure the orchestrator Headscale user exists.
    _ensure_user(orchestrator_user)
    # Step 5: Mint the orchestrator pre-auth key (in memory only — never touches disk).
    orch_key = _mint_orchestrator_key(orchestrator_user)
    # Step 6: Bring up ALL services (including orchestrator-router). Docker compose interpolates
    # ${XORCISE_ORCH_AUTHKEY} from the subprocess env — the key never lands in compose.yaml.
    _compose(workdir, "up", "-d", extra_env={"XORCISE_ORCH_AUTHKEY": orch_key})
    ca, _, _ = cert_paths(workdir, hostname)
    res = ProvisionResult(
        # advertise the control plane BY IP so the agent + routers join by IP (DERP too)
        # and never edit /etc/hosts. The cert's IP SAN validates the by-IP TLS. Port 443 is the
        # port the tailscale client forces for its noise/register dial (see render_compose).
        url=f"https://{ip}:443",
        ca_cert=str(ca),
        host_alias=f"{hostname}:{ip}",
        advertise_host=ip,
    )
    write_managed_block(
        config_path,
        url=res.url,
        ca_cert=res.ca_cert,
        host_alias=res.host_alias,
        advertise_host=res.advertise_host,
    )
    mark_owned(workdir)
    return res


def teardown(workdir: Path, config_path: Path) -> bool:
    """Tear down ONLY a control plane we provisioned (idempotent). Returns whether it acted."""
    if not is_owned(workdir):
        return False
    _compose(workdir, "down", "-v")
    strip_managed_block(config_path)
    clear_owned(workdir)
    return True
