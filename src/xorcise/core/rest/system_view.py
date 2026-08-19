"""System-info coordinator (delivery/rest layer) — the read-only Reflect view.

Mirrors what `status`/`role`/`db`/`catalog status`/`remote list` show on the CLI, for the GUI System
card. The probe helpers are reimplemented here (small) rather than imported from `cli._diagnostics`,
because cli is ABOVE rest in the layer order and rest must not import upward (.importlinter layers).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Literal

import httpx

from xorcise.core.config import Settings
from xorcise.core.contracts.config import (
    CatalogStatusView,
    MissionBaseView,
    PlaneStatus,
    SystemInfo,
)
from xorcise.core.rest.catalog_view import build_catalog_view_deps

_DB_SCHEMA = {"ready": "head", "stale": "behind", "fresh": "fresh"}

# Module → the service role that owns it, and its human name. The role keys are exactly what
# `xorcise serve --role <key>` takes; the labels mirror cli/_ux.py::_SERVICE_LABELS so the CLI
# and the GUI say the same words for the same thing.
_OWNER: dict[str, tuple[str, str]] = {
    "rest": ("control", "REST API"),
    "docker": ("runner", "Docker"),
    "headscale": ("headscale", "Headscale"),
    "otlp": ("collector", "OTLP receiver"),
}


# Which modules a role actually serves or drives on its own host. `rest` sits BELOW `roles` in
# .importlinter (cli > roles > rest | frontend > …), so ROLE_MANIFEST.toml cannot be read
# from here; this is a deliberate mirror, parity-checked against the manifest in the topology
# lane. Docker + Headscale are listed for the roles that DRIVE them, which is what
# rest/run_create.py::_use_real_docker already encodes ({"all", "runner"}).
_ROLE_MODULES: dict[str, frozenset[str]] = {
    "all": frozenset({"rest", "otlp", "docker", "headscale"}),
    "control": frozenset({"rest"}),
    "runner": frozenset({"docker", "headscale"}),
    "headscale": frozenset({"headscale"}),
    "collector": frozenset({"otlp"}),
}


def _plane_state(
    name: str, *, state: Literal["ok", "down", "not_deployed"], detail: str, location: str
) -> PlaneStatus:
    """Build one module row. `ok` is derived, never passed — it must stay exactly
    `state == "ok"` so every existing reader of the boolean keeps agreeing with `state`."""
    role, label = _OWNER.get(name, ("", name))
    return PlaneStatus(
        name=name,
        ok=state == "ok",
        detail=detail,
        location=location,
        role=role,
        label=label,
        state=state,
    )


def _plane(name: str, *, ok: bool, detail: str, location: str) -> PlaneStatus:
    return _plane_state(name, state="ok" if ok else "down", detail=detail, location=location)


def _not_deployed(name: str) -> PlaneStatus:
    """A module this host's role does not run — reported as absent, never as broken."""
    return _plane_state(name, state="not_deployed", detail="not on this host", location="")


# This view backs a status bar that EVERY page polls, so its cost is now a UX property.
# Measured on a healthy local install, per call:
#     rest 0.039s · otlp 0.012s · docker 0.053s · headscale 0.068s
#     catalog status 0.670s   <-- a REMOTE http call to the live catalog API
# The loopback probes are trivial and stay UNCACHED, so module reachability is always live.
# The two `docker` subprocesses and — dominating everything — the remote catalog round-trip are
# memoised. Each TTL must EXCEED the GUI's 15s poll, or every poll misses and the cache buys
# nothing but complexity.
# Catalog gets much longer: it is by far the priciest probe and the least volatile (a library
# connection does not flap), and `xorcise catalog status` / the Catalog card remain the live check.
_TTL_SECONDS: dict[str, float] = {
    "docker": 20.0,
    "headscale": 20.0,
    "catalog": 120.0,
    # Same reasoning as catalog: a remote round-trip, and a promoted base changes rarely.
    "mission_base": 120.0,
}
_DEFAULT_TTL = 20.0
_probe_cache: dict[str, tuple[float, object]] = {}


def _cached[T](key: str, produce: Callable[[], T]) -> T:
    hit = _probe_cache.get(key)
    now = time.monotonic()
    if hit is not None and (now - hit[0]) < _TTL_SECONDS.get(key, _DEFAULT_TTL):
        return hit[1]  # type: ignore[return-value]
    fresh = produce()
    _probe_cache[key] = (now, fresh)
    return fresh


def reset_probe_cache() -> None:
    """Drop the memoised slow probes (tests; and any caller needing a forced re-probe)."""
    _probe_cache.clear()


def _probe(name: str, url: str, location: str) -> PlaneStatus:
    """A plain loopback health check: 200 is up, anything else is down.

    There is no `ok_statuses` escape hatch any more — it existed only for the MCP plane, whose
    FastMCP endpoint answered 406 to a plain GET (it wanted SSE headers), so "alive" and
    "healthy" had to be spelled differently for it. Both remaining planes have real health
    endpoints that answer 200."""
    try:
        code = httpx.get(url, timeout=1).status_code
    except httpx.HTTPError:
        return _plane(name, ok=False, detail="down", location=location)
    good = code == 200
    return _plane(name, ok=good, detail="ok" if good else "down", location=location)


def _docker_plane(settings: Settings) -> PlaneStatus:
    # Stub mode runs without Docker on purpose — `_use_real_docker` (rest/mission_pull.py)
    # gates on the same flag, so runs are served by StubDockerDriver and never touch a daemon.
    # README offers `xorcise up --stub` as the answer to "no Docker on the box", so probing for
    # one there reports a correctly-configured install as broken. Absent by design, not down —
    # the same call the headscale probe makes, which is why both take `settings`.
    if settings.use_stubs:
        return _plane_state(
            "docker", state="not_deployed", detail="not used in stub mode", location="local daemon"
        )
    if not shutil.which("docker"):
        return _plane("docker", ok=False, detail="missing", location="local daemon")
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return _plane("docker", ok=False, detail="unreachable", location="local daemon")
    ok = res.returncode == 0
    return _plane("docker", ok=ok, detail="ok" if ok else "unreachable", location="local daemon")


def _managed_headscale_url() -> str:
    """The headscale_url OUR OWN `xorcise up` provisioning wrote, or "" if we never wrote one.

    Imported lazily: `xorcise.core.headscale` is a part-island, and role:control builds this same
    REST app — a module-scope import would leak the tailnet plane into a role that must not have
    it and trip `roles.activate`'s isolation assertion. (In practice control never reaches here,
    since headscale is `not_deployed` for that role, but the import must stay lazy regardless.)
    """
    try:
        from pathlib import Path

        from xorcise.core.headscale import provision
        from xorcise.core.home import xorcise_home

        return provision.managed_url(Path(xorcise_home()) / "config.toml")
    except Exception:
        return ""


def _headscale_plane(settings: Settings) -> PlaneStatus:
    """The tailnet coordinator — probed by whichever route THIS host actually uses to reach it.

    There is no single correct probe, because the dependency itself differs by deployment:

    * A LOCAL install drives Headscale by shelling into the container
      (`rest/run_create.py::_real_headscale_cli` → `DockerExecHeadscaleCli`). The container is
      the real dependency, and `cli/_diagnostics.py::control_plane` documents why a URL probe is
      not good enough here: control operations exec into that container whatever `headscale_url`
      says, so a URL that answers can sit alongside run creation 503-ing.

    * A host pointed at an EXTERNAL control plane has no such container by design. Probing for
      one would report a correctly-configured remote deployment as permanently broken — the
      failure the operator would hit the moment they edit the Headscale URL to another machine.

    So the two cases are told apart the same way `cli/commands/lifecycle.py` already does it:
    a `headscale_url` that differs from the one our own provisioning wrote is external.
    Either way the row is ADDRESSED by the login server, because that is the address an
    operator recognises and the thing that changes when the plane moves host.
    """
    # Stub mode deliberately runs without a tailnet (StubHeadscaleCli), so there is no container
    # to find and its absence is not a fault. Reporting it down would put a permanent red module
    # on every `--stub` demo — a false alarm about a dependency that install does not have.
    if settings.use_stubs:
        return _plane_state(
            "headscale", state="not_deployed", detail="not used in stub mode", location=""
        )

    url = (settings.headscale_url or "").strip()
    where = url or f"{settings.host}:{settings.headscale_port}"

    if url and url != _managed_headscale_url():
        # External control plane: HTTP is the only route this host has to it.
        # verify=False because a tailnet control plane normally presents a self-signed
        # certificate; this is a liveness probe, never a channel for anything sensitive.
        try:
            code = httpx.get(url, timeout=2, verify=False).status_code  # noqa: S501
        except httpx.HTTPError:
            return _plane("headscale", ok=False, detail="unreachable", location=where)
        # Any non-5xx answer proves a control plane is listening; Headscale's root is not a
        # health endpoint, so the status code itself carries no stronger meaning than that.
        ok = code < 500
        return _plane("headscale", ok=ok, detail="ok" if ok else "unreachable", location=where)

    container = settings.headscale_container
    if not shutil.which("docker"):
        return _plane("headscale", ok=False, detail="docker is missing", location=where)
    try:
        res = subprocess.run(
            ["docker", "exec", container, "headscale", "version"], capture_output=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return _plane("headscale", ok=False, detail="unreachable", location=where)
    ok = res.returncode == 0
    return _plane("headscale", ok=ok, detail="ok" if ok else "unreachable", location=where)


def _db_schema() -> str:
    from xorcise.core import db

    try:
        return _DB_SCHEMA.get(db.boot_state(), "unknown")
    except Exception:
        return "unknown"


def build_system_info(settings: Settings) -> SystemInfo:
    from xorcise.core.home import xorcise_home

    host = settings.host
    rest_loc = f"{host}:{settings.rest_port}"
    otlp_loc = f"{host}:{settings.otlp_port}"
    # Ordered by role so the GUI renders them grouped without re-sorting: control, runner,
    # headscale, collector. A module this role does not run is reported `not_deployed` and is
    # NOT probed — probing it would report a correctly-configured host as broken (and pay for
    # the probe to do so). An unmapped role falls back to probing everything.
    served = _ROLE_MODULES.get(settings.role, frozenset(_OWNER))
    probes: dict[str, Callable[[], PlaneStatus]] = {
        "rest": lambda: _probe("rest", f"http://{rest_loc}/api/health", rest_loc),
        "docker": lambda: _cached("docker", lambda: _docker_plane(settings)),
        "headscale": lambda: _cached("headscale", lambda: _headscale_plane(settings)),
        "otlp": lambda: _probe("otlp", f"http://{otlp_loc}/healthz", otlp_loc),
    }
    planes = tuple(
        probe() if name in served else _not_deployed(name) for name, probe in probes.items()
    )
    # The single most expensive thing here (a remote round-trip) — memoised like the subprocess
    # probes. `xorcise catalog status` / the catalog card remain the live, uncached check.
    source = build_catalog_view_deps(settings).source
    status = _cached("catalog", lambda: source.status())
    return SystemInfo(
        role=settings.role,
        planes=planes,
        db_schema=_db_schema(),  # type: ignore[arg-type]
        catalog=CatalogStatusView(
            state=status.state, message=status.message, last_sync=status.last_sync
        ),
        remotes=(),  # `remote` is a reserved stub today (no registered remotes)
        home=str(xorcise_home()),
        db_url=settings.database_url,
        topology=settings.deployment_topology,
        mission_base=_cached("mission_base", lambda: _mission_base_view(source)),
    )


def _mission_base_view(source: object) -> MissionBaseView:
    """§36 version visibility: what this client requires vs what the catalog promotes.

    The required MAJOR and the client's own version are local facts and always present; the
    promoted side degrades to None on a pre-contract catalog (its /v1/mission-base 404s), the
    stub, or any failure — unknown, never fabricated."""
    from importlib.metadata import PackageNotFoundError, version

    # Lazy: the runner island stays off this module's import path until actually needed.
    from xorcise.core.runner.docker.build import REQUIRED_BASE_MAJOR

    try:
        client_version = version("xorcise")
    except PackageNotFoundError:  # editable/dev installs without metadata
        client_version = ""
    promoted = None
    try:
        promoted = source.mission_base()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — a probe failure must never take the system view down
        promoted = None
    return MissionBaseView(
        required_major=REQUIRED_BASE_MAJOR,
        client_version=client_version,
        promoted_version=promoted.version if promoted else None,
        promoted_index_digest=promoted.index_digest if promoted else None,
    )
