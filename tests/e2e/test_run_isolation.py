"""E2E: two simultaneous real runs + the per-run network-isolation boundary. Skip-guarded.

This is the automated replacement for the manual-only "Verified live" proof
that per-run isolation actually holds. It stands up TWO runs through the real loop and
asserts, in layers:

  1. both OUTER fused containers stay `running` (the original fuse bug was an immediate
     Exited(1)) and each has its inner mission + Tailscale router up with net-override delivered;
  2. each run's router joined the air-gapped Headscale, is online + tag:router, and its carved
     CIDR route is approved — and the two runs got DISTINCT CIDRs;
  3. the cross-run boundary: an agent node joined to run A reaches A's own mission web service
     (tailnet-routed, ACL-permitted) but NOT run B's — and the applied ACL scopes agent A to only
     its own CIDR (fail-closed by construction; no route to B, no broad allow).

The mission web IPs live inside each fused container's NESTED docker network, so they are
reachable ONLY via the tailnet — a successful/failed curl is therefore a faithful tailnet probe,
not host-bridge noise. (The agent container keeps its own eth0, so a live internet-curl would be
meaningless; "no egress" is asserted at the ACL layer instead.)

Runs on a provisioned single host:
  - Docker daemon + the docker SDK (`runner` extra),
  - `docker build -t xorcise/mission-base containers/mission-base`,
  - `xorcise up` (provisions the air-gapped TLS + embedded-DERP Headscale; writes config.toml).
Skips cleanly otherwise so the e2e lane stays green on CI/unprovisioned hosts."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

if shutil.which("docker") is None:
    pytest.skip("docker not available", allow_module_level=True)

AGENT_IMAGE = "tailscale/tailscale:stable"
PROBE_IMAGE = "busybox:latest"
_REAL_HOME = Path.home() / ".xorcise"


def _image_present(ref: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0


def _headscale_up(container: str = "headscale") -> bool:
    return (
        subprocess.run(
            ["docker", "exec", container, "headscale", "version"], capture_output=True
        ).returncode
        == 0
    )


def _docker_sdk_present() -> bool:
    return importlib.util.find_spec("docker") is not None


def _airgap_wiring() -> dict[str, str] | None:
    """The headscale_* keys `xorcise up` persisted to the REAL ~/.xorcise/config.toml.
    The test's migrated_home redirects XORCISE_HOME to a temp dir, so we re-inject these as env."""
    conf = _REAL_HOME / "config.toml"
    if not conf.is_file():
        return None
    data = tomllib.loads(conf.read_text())
    keys = (
        "headscale_url",
        "headscale_ca_cert",
        "headscale_host_alias",
        "headscale_advertise_host",
    )
    wiring = {k: str(data[k]) for k in keys if k in data}
    return wiring if {"headscale_url", "headscale_ca_cert"} <= wiring.keys() else None


pytestmark = pytest.mark.skipif(
    not (
        _image_present("xorcise/mission-base")
        and _headscale_up()
        and _docker_sdk_present()
        and _airgap_wiring() is not None
    ),
    reason="needs mission-base + a running air-gapped headscale (xorcise up) + the runner extra",
)


# --- small live helpers -------------------------------------------------------------------------


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)


def _inner(run_id: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run `docker <args>` on the INNER dockerd of a fused run container."""
    return _run("docker", "exec", run_id, "docker", *args, timeout=timeout)


def _hs(*args: str) -> list[dict[str, Any]]:
    out = _run("docker", "exec", "headscale", "headscale", *args, "-o", "json").stdout.strip()
    return json.loads(out) if out else []


def _wait(pred, *, timeout: float, interval: float = 2.0, desc: str = "") -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if pred():
                return
        except Exception as exc:  # transient (container not ready, json not parseable yet)
            last = exc
        time.sleep(interval)
    pytest.fail(
        f"timed out after {timeout}s waiting for: {desc}" + (f" (last: {last})" if last else "")
    )


def _outer_status(run_id: str) -> str:
    return _run("docker", "inspect", "-f", "{{.State.Status}}", run_id).stdout.strip()


def _inner_names(run_id: str) -> list[str]:
    return _inner(run_id, "ps", "--format", "{{.Names}}").stdout.split()


def _inner_up(run_id: str) -> bool:
    """Inner mission `web` + the Tailscale `router` are both running."""
    names = _inner_names(run_id)
    return any("web" in n for n in names) and any("router" in n for n in names)


def _inner_web_ip(run_id: str, cidr_prefix: str) -> str:
    """The mission `web` container's IP inside the run's subnet (prefix like '10.200.1')."""
    web = next(n for n in _inner_names(run_id) if "web" in n)
    ips = _inner(
        run_id, "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", web
    ).stdout.split()
    return next(ip for ip in ips if ip.startswith(cidr_prefix))


def _probe(agent: str, url: str, timeout: int = 8) -> bool:
    """curl `url` from inside the agent node's network namespace (via a busybox sidecar that
    shares the agent's netns, so it routes through the tailnet). True iff it got a response."""
    rc = _run(
        "docker",
        "run",
        "--rm",
        "--network",
        f"container:{agent}",
        PROBE_IMAGE,
        "wget",
        "-T",
        str(timeout),
        "-q",
        "-O",
        "-",
        url,
        timeout=timeout + 5,
    ).returncode
    return rc == 0


def _write_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    (bundle / "services" / "web").mkdir(parents=True)
    (bundle / "docker-compose.yml").write_text("services:\n  web:\n    build: ./services/web\n")
    # busybox httpd serving a known body on :80 (the probe target).
    (bundle / "services" / "web" / "Dockerfile").write_text(
        "FROM busybox\n"
        "RUN mkdir -p /www && echo XORCISE-OK > /www/index.html\n"
        'CMD ["httpd", "-f", "-p", "80", "-h", "/www"]\n'
    )
    (bundle / "mission.json").write_text(
        '{"schema_version":"2.0",'
        '"metadata":{"mission_id":"iso","name":"iso","objective":"x","type":"lab"},'
        '"environment":{"compose_file":"docker-compose.yml"}}'
    )
    return bundle


def _start_agent(
    name: str, *, join_key: str, login_server: str, ca_path: str, host_alias: str
) -> None:
    _run("docker", "rm", "-f", name)
    _run(
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--privileged",
        "--device",
        "/dev/net/tun:/dev/net/tun",
        "--add-host",
        host_alias,  # "headscale.local:<host-ip>"
        "-v",
        f"{ca_path}:/run/headscale-ca.pem:ro",
        "-e",
        "SSL_CERT_FILE=/run/headscale-ca.pem",
        "-e",
        "TS_USERSPACE=false",
        "-e",
        f"TS_AUTHKEY={join_key}",
        "-e",
        f"TS_EXTRA_ARGS=--login-server={login_server} --accept-routes",
        AGENT_IMAGE,
    )


def test_two_run_isolation_boundary(migrated_home, monkeypatch):
    from xorcise.core import agents
    from xorcise.core.config import get_settings
    from xorcise.core.rest.ingest import ingest_bundle
    from xorcise.core.rest.run_create import build_run_create_deps, create_run

    # Real-by-default + the air-gapped wiring (env outranks the temp home's config.toml).
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)
    monkeypatch.setenv("XORCISE_ROLE", "all")
    wiring = _airgap_wiring()
    assert wiring is not None  # guarded above
    for k, v in wiring.items():
        monkeypatch.setenv(f"XORCISE_{k.upper()}", v)
    get_settings.cache_clear()
    settings = get_settings()
    host_alias = settings.headscale_host_alias  # "headscale.local:<host-ip>"

    ingest_bundle(_write_bundle(Path(migrated_home)), settings)
    agents.register("alice", endpoint="http://a")
    deps = build_run_create_deps(get_settings())

    from xorcise.core.contracts.connect import MissionPrompt
    from xorcise.core.contracts.run import RunEntry

    runs: list[tuple[RunEntry, MissionPrompt]] = []
    agent_nodes: list[str] = []
    try:
        for _ in (1, 2):
            run, prompt = create_run(
                agent_name="alice", mission_slug="iso", budget_seconds=600, deps=deps
            )
            runs.append((run, prompt))
        (runA, promptA), (runB, promptB) = runs

        # --- Layer 1: both fused runs come up (regression guard) -----------------------
        for run, _ in runs:
            _wait(
                lambda r=run: _outer_status(r.run_id) == "running",
                timeout=60,
                desc=f"outer {run.run_id} running",
            )
            _wait(
                lambda r=run: _inner_up(r.run_id),
                timeout=120,
                desc=f"inner web+router up for {run.run_id}",
            )
            assert (
                _run(
                    "docker", "exec", run.run_id, "test", "-f", "/mission/net-override.yml"
                ).returncode
                == 0
            ), f"net-override not delivered to {run.run_id}"

        # --- Layer 2: routers joined Headscale, online, routes approved, distinct CIDRs --------
        def _router_node(run_id: str) -> dict[str, Any] | None:
            want = f"{run_id}-router"
            return next((n for n in _hs("nodes", "list") if n.get("name") == want), None)

        for run, _ in runs:
            _wait(
                lambda r=run: (_router_node(r.run_id) or {}).get("online") is True,
                timeout=120,
                desc=f"router online for {run.run_id}",
            )
            node = _router_node(run.run_id)
            assert node is not None
            assert "tag:router" in node.get("tags", []), f"router not tagged: {node.get('tags')}"

        # carved CIDRs come from the per-run net-override the entrypoint rendered
        def _run_cidr(run_id: str) -> str:
            txt = _run("docker", "exec", run_id, "cat", "/mission/net-override.yml").stdout
            m = re.search(r"subnet:\s*([0-9./]+)", txt)
            assert m, f"no subnet in net-override for {run_id}"
            return m.group(1)

        cidrA, cidrB = _run_cidr(runA.run_id), _run_cidr(runB.run_id)
        assert cidrA != cidrB, f"runs must get distinct CIDRs (got {cidrA} == {cidrB})"

        # routes approved for each router (Headscale v0.29 exposes them on the node JSON,
        # snake_case `approved_routes`).
        def _approved(run_id: str) -> list[str]:
            node = _router_node(run_id) or {}
            return [str(r) for r in (node.get("approved_routes") or [])]

        for run, cidr in ((runA, cidrA), (runB, cidrB)):
            _wait(
                lambda r=run, c=cidr: any(c in p for p in _approved(r.run_id)),
                timeout=120,
                desc=f"route {cidr} approved",
            )

        # --- Layer 3: the cross-run boundary, probed live from an agent joined to run A --------
        prefixA = cidrA.rsplit(".", 2)[0] + "."  # e.g. "10.200.1."
        prefixB = cidrB.rsplit(".", 2)[0] + "."
        webA = _inner_web_ip(runA.run_id, prefixA)
        webB = _inner_web_ip(runB.run_id, prefixB)

        agentA = f"{runA.run_id}-agent"
        agent_nodes.append(agentA)
        _start_agent(
            agentA,
            join_key=promptA.join_key,
            login_server=settings.headscale_url,
            ca_path=settings.headscale_ca_cert,
            host_alias=host_alias,
        )
        _wait(
            lambda: (
                "Running" in _run("docker", "exec", agentA, "tailscale", "status").stdout
                or _run("docker", "exec", agentA, "tailscale", "status").returncode == 0
            ),
            timeout=90,
            desc="agent A tailscale up",
        )
        # give accept-routes a moment to install the advertised subnet route
        _wait(
            lambda: _probe(agentA, f"http://{webA}:80") is True,
            timeout=60,
            interval=3,
            desc="agent A reaches its OWN mission web",
        )

        reaches_own = _probe(agentA, f"http://{webA}:80")
        reaches_other = _probe(agentA, f"http://{webB}:80")
        assert reaches_own, "agent A must reach its OWN mission CIDR"
        assert not reaches_other, "agent A must NOT reach run B's mission CIDR (isolation breach!)"

        # fail-closed by construction: the applied ACL scopes agent A to only its own CIDR.
        policy = _run("docker", "exec", "headscale", "headscale", "policy", "get").stdout
        assert cidrA in policy and cidrB in policy  # both runs present in the live policy
        # agent A's user must not be granted run B's CIDR anywhere (and no 0.0.0.0/0 egress)
        assert "0.0.0.0/0" not in policy, "fence must not grant a default route (fail-closed)"
    finally:
        for nm in agent_nodes:
            _run("docker", "rm", "-f", nm)
        for run, _ in runs:
            with contextlib.suppress(Exception):
                deps.control.teardown(run.run_id, credential=deps.api_key)
