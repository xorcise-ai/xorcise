"""Host + channel health checks for `status` / `doctor` (cli).

Small, side-effect-light helpers returning a frozen Check. `status` renders these
as a table; `doctor` prints per-failure remediation. Layer: cli (may import inward).

Every prerequisite a real run shells out to lives here, so `doctor` can never be
green on a host where `xorcise up` cannot work: the Docker CLI, a reachable
daemon (permission-denied classified apart from not-running — different fix), the
**Compose v2 plugin** (`docker compose`, used by Headscale provisioning), openssl
(local TLS certs) and /dev/net/tun (mission networking).

`level` separates a blocker (fails the verdict) from a warning (reported, never
flips the exit code) so advisory checks can be added without breaking scripts.
"""

from __future__ import annotations

import contextlib
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from xorcise.core.home import xorcise_home

# Minimum interpreter (mirrors pyproject's requires-python).
_MIN_PYTHON = (3, 12)

# A mission image is ~1-3 GB and a local build needs roughly 3x that transiently.
_DISK_WARN_BYTES = 10 * 1024**3

_DAEMON_FIX = "start Docker: sudo systemctl start docker (macOS: open Docker Desktop)"
# The classic fresh-Ubuntu footgun: `usermod -aG docker` done, but the login
# session has not picked up the new group. "start Docker" is the WRONG advice here.
_GROUP_FIX = (
    "add yourself to the docker group and start a NEW login session: "
    "sudo usermod -aG docker $USER, then log out and back in "
    "(newgrp docker only affects the current shell)"
)
_COMPOSE_FIX = (
    "install the Docker Compose v2 plugin: sudo apt install docker-compose-v2 "
    "(or docker-compose-plugin from Docker's repo). The legacy 'docker-compose' "
    "v1 package does NOT provide 'docker compose'"
)
# Every real run needs the control plane, so a missing one is a total outage — and
# the least discoverable cause is a SAVED remote URL that no longer answers, which
# makes `up` skip provisioning a local one entirely.
_PLANE_FIX = (
    "restart it: xorcise down && xorcise up. If you set a remote control plane, "
    "clear it to go back to local: xorcise config set-network --headscale-url ''"
)
_EXTERNAL_PLANE_FIX = (
    "start that control plane, or go back to a local one: "
    "xorcise config set-network --headscale-url '' && xorcise down && xorcise up"
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str  # "ok" / "down" / "missing" / "unreachable" / "permission denied" / …
    remediation: str = ""
    # A warning is reported but never fails the verdict (advisory prerequisites).
    level: Literal["blocker", "warning"] = "blocker"


def python_version() -> Check:
    """The interpreter running XORCISE (informational — if it were too old, the
    package would not have installed)."""
    v = sys.version_info
    running = f"Python {v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= _MIN_PYTHON:
        return Check("python", True, running, level="warning")
    return Check(
        "python",
        False,
        f"{running} is too old",
        f"XORCISE needs Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ — create the venv with a "
        "newer interpreter (Ubuntu 22.04 ships 3.10; use 24.04+ or the deadsnakes PPA)",
        level="warning",
    )


def docker_present() -> Check:
    if shutil.which("docker"):
        return Check("docker", True, "present")
    return Check(
        "docker",
        False,
        "missing",
        "install Docker and put 'docker' on PATH: sudo apt install docker.io docker-compose-v2",
    )


def docker_daemon() -> Check:
    """Three outcomes from ONE `docker info`: ok, permission denied, unreachable.

    Permission-denied is its own branch because the fix is completely different
    (join the docker group + re-login, not 'start Docker')."""
    if not shutil.which("docker"):
        return Check("docker", False, "missing", "install Docker and put 'docker' on PATH")
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return Check("docker", False, "unreachable", _DAEMON_FIX)
    if result.returncode == 0:
        return Check("docker", True, "ok")
    if "permission denied" in (getattr(result, "stderr", "") or "").lower():
        return Check("docker", False, "permission denied", _GROUP_FIX)
    return Check("docker", False, "unreachable", _DAEMON_FIX)


def docker_compose_v2() -> Check:
    """`docker compose` — the v2 CLI plugin every non-stub `xorcise up` needs.

    Probed by EXIT CODE, never by parsing the error prose (the wording differs
    across Docker CLI versions). Daemon-free, so it answers even when Docker is
    not running. NOTE: the legacy standalone `docker-compose` (v1) does not
    satisfy this — XORCISE never invokes that binary."""
    if not shutil.which("docker"):
        return Check("docker compose", False, "missing", _COMPOSE_FIX)
    try:
        result = subprocess.run(
            ["docker", "compose", "version", "--short"], capture_output=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return Check("docker compose", False, "missing", _COMPOSE_FIX)
    if result.returncode == 0:
        return Check("docker compose", True, "ok")
    return Check("docker compose", False, "missing", _COMPOSE_FIX)


def openssl_present() -> Check:
    """openssl — XORCISE generates the local Headscale TLS certificate with it."""
    if shutil.which("openssl"):
        return Check("openssl", True, "present")
    return Check(
        "openssl",
        False,
        "missing",
        "XORCISE generates the local Headscale TLS certificate with it: sudo apt install openssl",
    )


def tun_device() -> Check:
    """/dev/net/tun — mission networking (the per-run Tailscale router) needs it.

    Advisory: absent only on unusual kernels/containers, and a stub-mode demo
    never touches it."""
    if platform.system() != "Linux":
        return Check("/dev/net/tun", True, "not required on this platform", level="warning")
    if Path("/dev/net/tun").exists():
        return Check("/dev/net/tun", True, "present", level="warning")
    return Check(
        "/dev/net/tun",
        False,
        "missing",
        "mission networking needs the tun kernel module: sudo modprobe tun "
        "(persist: echo tun | sudo tee /etc/modules-load.d/tun.conf)",
        level="warning",
    )


def _docker_root() -> str:
    """Where Docker stores images — that is the filesystem a pull fills up."""
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        result = subprocess.run(
            ["docker", "info", "--format", "{{.DockerRootDir}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        root = (result.stdout or "").strip()
        if result.returncode == 0 and root:
            return root
    return "/var/lib/docker"


def disk_space() -> Check:
    """Free space where Docker stores images (advisory)."""
    root = _docker_root()
    probe = Path(root)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return Check("disk space", True, "unknown", level="warning")
    gib = free / 1024**3
    if free >= _DISK_WARN_BYTES:
        return Check("disk space", True, f"{gib:.0f} GB free at {root}", level="warning")
    return Check(
        "disk space",
        False,
        f"only {gib:.1f} GB free at {root}",
        "a mission image is 1-3 GB (a local build needs ~3x that transiently) — "
        "reclaim space with: docker system prune -a",
        level="warning",
    )


def home_present() -> Check:
    """The data directory must exist AND be writable — an unwritable home fails
    every boot. Names the directory it actually checked, so a user on a
    non-default XORCISE_HOME can trust the diagnosis."""
    home = xorcise_home()
    label = str(home)
    with contextlib.suppress(ValueError):
        label = "~/" + str(home.relative_to(home.home()))
    if not home.exists():
        return Check("data directory", False, f"{label} missing", "run 'xorcise up' to create it")
    probe = home / ".xorcise-write-probe"
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        return Check(
            "data directory",
            False,
            f"{label} is not writable",
            f"fix the permissions on {label} (or unset XORCISE_HOME), then re-run xorcise doctor",
        )
    return Check("data directory", True, label)


def probe_channel(name: str, url: str, ok_statuses: tuple[int, ...] = (200,)) -> Check:
    try:
        code = httpx.get(url, timeout=1).status_code
    except httpx.HTTPError:
        return Check(name, False, "down")
    ok = code in ok_statuses
    return Check(name, ok, "ok" if ok else "down")


def control_plane(container: str = "headscale", *, timeout: float = 5.0) -> Check:
    """The Headscale control plane every real run needs, probed the way RUN CREATION
    probes it — `docker exec <container> headscale version` (rest/run_create.py).

    Deliberately container-based, not URL-based: control operations shell into this
    container regardless of `headscale_url`, so a URL probe can pass while every run
    creation still fails with a 503. Probing anything other than the real dependency
    just moves the false confidence somewhere new.

    Without this check `doctor` reported "No problems found" during a total run
    outage — Docker, disk, tun, ports and the data directory were all genuinely fine.
    """
    if not shutil.which("docker"):
        return Check("control plane", False, "docker is missing", _DAEMON_FIX)
    try:
        result = subprocess.run(
            ["docker", "exec", container, "headscale", "version"],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return Check("control plane", False, f"{container!r} is not reachable", _PLANE_FIX)
    if result.returncode == 0:
        return Check("control plane", True, f"{container!r} reachable")
    return Check("control plane", False, f"{container!r} is not reachable", _PLANE_FIX)


def _clip(text: str, limit: int = 140) -> str:
    """Keep one diagnostic to one readable line. A nested-runtime failure arrives as a whole
    OCI error chain whose meaning is at the END, so the tail is what survives the clip."""
    text = " ".join(text.split())
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def container_runtime() -> Check:
    """macOS only: which topology the mission stack will be composed with, and whether nested
    Rosetta actually works on this host.

    Advisory by design — being in host-daemon (sibling) mode is the shipped default, not a
    fault. It fails only when the operator PINNED `dind` on a host that cannot support it,
    which would otherwise surface as a mission that mysteriously cannot start amd64 services.

    Deliberately NOT part of `_environment_checks()`: it starts a small privileged container
    (~1-2 s, pulling a ~4 MB image the first time), which is fine for an explicit diagnostic
    command but not for the check list `up` runs on every start.

    The probe runs even when the setting pins the mode, because collecting real verdicts from
    real macOS hosts is the whole purpose of shipping the probe ahead of the default flip.
    """
    from xorcise.core.config import get_settings
    from xorcise.core.rest.docker_runtime import inspect_runtime

    def _client() -> object:
        import docker  # type: ignore[import-untyped]

        return docker.from_env()

    try:
        decision, probe = inspect_runtime(get_settings(), _client)
    except Exception as exc:  # noqa: BLE001 — an advisory check must never break `doctor`
        return Check("container runtime", True, f"undetermined ({exc})", level="warning")

    # Report the probe's own wording rather than a flattened "available". Tier 1 only proves the
    # binfmt handler is registered; Tier 2 proves an amd64 child actually runs, and the two can
    # disagree (they DO on the dind base XORCISE currently ships). A bare "available" would
    # promise the second while only having measured the first.
    verdict = "not run" if probe is None else _clip(probe.detail)
    reason = _clip(decision.reason)
    # Under `auto` the probe verdict IS the reason for the mode, and printing it twice pushes the
    # useful half of a long runtime error off the line.
    detail = f"{decision.mode}; probe: {verdict}"
    if reason != verdict:
        detail = f"{decision.mode} ({reason}); probe: {verdict}"
    if decision.mode == "dind" and probe is not None and not probe.ok:
        return Check(
            "container runtime",
            False,
            detail,
            "this host cannot run nested amd64 — go back to the default: "
            "XORCISE_MACOS_CONTAINER_RUNTIME=host-daemon",
            level="warning",
        )
    return Check("container runtime", True, detail, level="warning")


def external_control_plane(url: str, *, timeout: float = 5.0) -> Check:
    """Is a CONFIGURED remote control plane actually answering?

    `up` used to print "using external control plane at <url>" from the saved setting
    alone — a stored preference reported as a verified connection. A URL that stopped
    answering therefore looked like a healthy start, and every run creation afterwards
    failed with a 503 that named a control plane the operator believed was fine.

    Any HTTP reply proves something is listening (Headscale answers `/` with a 404), so
    only transport failures count as unreachable. TLS is intentionally not verified: the
    question is "is it there", not "is it trusted" — a local Headscale uses a
    self-signed CA and would fail verification while working perfectly.
    """
    try:
        httpx.get(url, timeout=timeout, verify=False)
    except httpx.HTTPError:
        return Check("control plane", False, f"{url} is unreachable", _EXTERNAL_PLANE_FIX)
    return Check("control plane", True, f"{url} answered")
