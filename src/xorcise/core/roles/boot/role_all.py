"""role:all composition root — wires the server's parts into ASGI apps.

LAYER: roles/boot. The ONLY place routers/tools are registered (boot-only
registration, generalised to all roles).
"""

from __future__ import annotations

import platform
import socket
import subprocess
from typing import TYPE_CHECKING

from fastapi import FastAPI

from xorcise.core.config import LOOPBACK, LOOPBACK_HOSTS, get_settings
from xorcise.core.rest.app import create_app, mount_ui
from xorcise.core.rest.routers import (
    agents,
    catalog,
    config,
    fs,
    harnesses,
    health,
    missions,
    runcontrol,
    runs,
    system,
)
from xorcise.core.roles.boot import AppSpec

if TYPE_CHECKING:
    from xorcise.core.rest.budget_watchdog import BudgetWatchdog
    from xorcise.core.rest.run_readiness import ReadinessWatchdog


def build_rest_app() -> FastAPI:
    """Compose the REST app: bare factory + routers + /ui static mount."""
    from datetime import UTC, datetime

    app = create_app()
    app.include_router(health.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(runcontrol.router, prefix="/api")
    app.include_router(missions.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(fs.router, prefix="/api")
    app.include_router(harnesses.router, prefix="/api")
    mount_ui(app)

    # Process-wide background singletons. Every startup hook below is GUARDED, because the
    # ASGI lifespan runs once per uvicorn.Server, and nothing in this module controls how many
    # Servers `serve` builds around this one app object. It used to build one per bind address
    # (three on Linux: loopback, ::1, the docker bridge gateway), so each hook fired three times:
    # three reconciles, three budget watchdogs, three readiness gates scanning the same runs —
    # two of which closed out the same failed run milliseconds apart, the loser's teardown dying
    # on 409 "removal of container … already in progress" — and, since each hook assigns its
    # instance to a nonlocal, shutdown stopped only the last one. `serve` now runs one Server per
    # app (sockets per address), which fires each hook once; the guards stay because they hold
    # under ANY ASGI host arrangement, which this module cannot see. Each shutdown hook RELEASES
    # its guard, so a lifespan that restarts on the same app object (two `TestClient(app)`
    # blocks; a host that cycles the lifespan) comes back with live singletons, not with a
    # stopped watchdog and no gate silently left behind by the previous cycle.
    _watchdog: BudgetWatchdog | None = None
    _readiness: ReadinessWatchdog | None = None
    # Separate from `_readiness` because the gate cannot be assigned until after an await; see
    # the claim in _start_readiness_gate.
    _readiness_claimed = False

    _reconcile_task: object | None = None

    @app.on_event("startup")
    async def _reconcile_on_startup() -> None:
        # converge live Headscale/Docker to the persisted non-terminal runs after a
        # (re)start. Run in a worker thread so the Docker/Headscale I/O never blocks the event loop
        # or delays serving; it is idempotent + best-effort, so a failure is logged, not fatal.
        import asyncio
        import logging

        from xorcise.core.rest.reconcile import reconcile_all_on_startup

        nonlocal _reconcile_task
        if _reconcile_task is not None:
            return  # a second listener on the same app — one reconcile per process

        async def _run() -> None:
            try:
                # Worker thread: the Docker/Headscale I/O never blocks the event loop or boot.
                await asyncio.to_thread(reconcile_all_on_startup)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # best-effort: never let reconcile crash boot
                # One line at WARNING (an operator's console is not a crash log);
                # the full traceback stays available at DEBUG.
                log = logging.getLogger(__name__)
                log.warning("startup reconcile failed: %s", exc)
                log.debug("startup reconcile failure detail", exc_info=True)

        _reconcile_task = asyncio.create_task(_run())

    @app.on_event("shutdown")
    async def _cancel_reconcile() -> None:
        # A reconcile still in flight during shutdown must not spray errors from
        # subprocesses that lost the race with teardown.
        import asyncio
        import contextlib

        nonlocal _reconcile_task
        task = _reconcile_task
        _reconcile_task = None  # release the guard for a lifespan restart
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @app.on_event("startup")
    async def _start_watchdog() -> None:
        nonlocal _watchdog
        if _watchdog is not None:
            return  # a second listener on the same app — one watchdog per process
        from xorcise.core import runs
        from xorcise.core.rest.budget_watchdog import BudgetWatchdog
        from xorcise.core.rest.run_terminate import terminate_run

        _watchdog = BudgetWatchdog(
            terminate=terminate_run,
            list_active=runs.active_runs_with_deadline,
            now_fn=lambda: datetime.now(UTC),
            interval=get_settings().budget_watchdog_interval_seconds,
        )
        _watchdog.start()

    @app.on_event("shutdown")
    async def _stop_watchdog() -> None:
        nonlocal _watchdog
        watchdog, _watchdog = _watchdog, None  # release the guard for a lifespan restart
        if watchdog is not None:
            await watchdog.stop()

    @app.on_event("startup")
    async def _start_readiness_gate() -> None:
        # Close out runs whose environment died at deploy or never came up: deploy() does not wait
        # for the inner mission stack, so without this such a run stays non-terminal forever with
        # a live agent working a target that never existed. Best-effort, like the boot reconcile —
        # it is a safety net, so a plane we cannot reach disables it rather than failing boot.
        nonlocal _readiness, _readiness_claimed
        # Claim the slot SYNCHRONOUSLY, before the first await. Guarding on `_readiness is not
        # None` alone is a check-then-act race: this hook awaits build_run_create_deps (Docker +
        # Headscale probes) before it can assign, and a concurrently-starting second listener
        # reaches the same check while that await is still pending — so both proceed and two
        # gates end up scanning the same runs. asyncio is single-threaded, so a flag set with no
        # await between check and set is atomic against the other startup task.
        if _readiness_claimed:
            return  # another listener on the same app already owns the gate
        _readiness_claimed = True
        import asyncio
        import logging

        from xorcise.core import runs as runs_repo
        from xorcise.core.rest.run_create import build_run_create_deps
        from xorcise.core.rest.run_readiness import ReadinessWatchdog
        from xorcise.core.rest.run_terminate import terminate_run

        settings = get_settings()
        if settings.readiness_timeout_seconds <= 0:
            return  # explicitly disabled
        try:
            # Worker thread: wiring the real deps probes Docker/Headscale.
            deps = await asyncio.to_thread(build_run_create_deps, settings)
        except Exception as exc:  # noqa: BLE001 — never let the safety net break boot
            logging.getLogger(__name__).warning("readiness gate disabled: %s", exc)
            return
        _readiness = ReadinessWatchdog(
            deps=deps,
            list_pending=runs_repo.deployed_non_terminal_runs,
            terminate=terminate_run,
            now_fn=lambda: datetime.now(UTC),
            timeout_seconds=settings.readiness_timeout_seconds,
            interval=settings.readiness_scan_interval_seconds,
        )
        _readiness.start()

    @app.on_event("shutdown")
    async def _stop_readiness_gate() -> None:
        nonlocal _readiness, _readiness_claimed
        gate, _readiness = _readiness, None
        _readiness_claimed = False  # release the claim for a lifespan restart
        if gate is not None:
            await gate.stop()

    _prewarmed = False

    @app.on_event("shutdown")
    async def _reset_prewarm() -> None:
        nonlocal _prewarmed
        _prewarmed = False  # a restarted lifespan may warm again

    @app.on_event("startup")
    async def _prewarm_nested_support() -> None:
        # Compute the host nesting verdict at boot so the FIRST real run-create reads it instantly
        # instead of paying the 20-40 s probe inline (which, under the CLI's read timeout, is what
        # made the first run appear to hang and get retried into a duplicate). Best-effort and
        # memoised: a warm-up failure just leaves it cold for the first run to recompute.
        #
        # Fire-and-forget on a DAEMON THREAD — not awaited, and deliberately NOT
        # `asyncio.to_thread`. to_thread runs on the loop's DEFAULT executor, which
        # `asyncio.run()` joins before the process may exit, so a probe nobody awaits held
        # `serve` open for the whole 20-40 s after every listener had already closed. Cancelling
        # could never have helped: cancellation abandons the `await`, never the thread sitting
        # inside the blocking call. Nothing joins a daemon thread at exit, so teardown simply
        # walks away from the warm-up — which is precisely what its own contract says a failed
        # warm-up costs: a cold memo the first run recomputes.
        nonlocal _prewarmed
        if _prewarmed:
            return  # several Servers may share this app — warm once per lifespan
        _prewarmed = True
        import logging
        import threading

        settings = get_settings()
        if settings.use_stubs or settings.nested_container_check == "skip":
            return  # nothing real to probe

        def _warm() -> None:
            try:
                from xorcise.core.rest.docker_runtime import prewarm_nested_support

                def _client() -> object:
                    import docker  # type: ignore[import-untyped]

                    return docker.from_env()

                prewarm_nested_support(settings, _client)
            except Exception as exc:  # noqa: BLE001 — a warm-up must never break anything
                logging.getLogger(__name__).debug("nested-support pre-warm skipped: %s", exc)

        threading.Thread(target=_warm, name="nested-support-prewarm", daemon=True).start()

    return app


def build_otel_app() -> FastAPI:
    """Compose the OTLP receiver app."""
    from xorcise.core.otel.ingest.embedded import create_otel_app
    from xorcise.core.otel.mirror import resolve_mirror
    from xorcise.core.otel.store import SqliteSealStore

    resolve_mirror(get_settings())  # fail fast if the reserved mirror is enabled
    return create_otel_app(seal_store=SqliteSealStore())


def _system() -> str:
    return platform.system()


def _docker_bridge_gateway() -> str:
    """The default docker bridge gateway — what `host-gateway` resolves to on native Linux."""
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
    return out.stdout.strip() if out.returncode == 0 else ""


def _locally_bindable(ip: str) -> bool:
    """True when *ip* is an address of THIS host (Docker Desktop reports a VM-internal
    gateway that uvicorn could never bind — binding it would crash boot)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((ip, 0))
    except OSError:
        return False
    return True


def _agent_facing_bind_hosts(configured_host: str) -> tuple[str, ...]:
    """The narrowest bind set that keeps the agent container's path to the brain working.

    Binding REST + OTLP to the IPv4 wildcard lets containers reach them via
    host.docker.internal — but that also exposed the unauthenticated /api surface to the
    LAN, breaking the loopback-bind assumption the local no-auth posture rests on. The
    container path does not need the wildcard: Docker Desktop (macOS/Windows) proxies
    host.docker.internal to the host loopback, and native Linux maps it to the docker
    bridge gateway — a concrete host interface we can bind alongside loopback. ::1 keeps
    the loopback hostname (IPv6-first on many systems) working. A configured non-loopback
    host stays served (it was covered by the wildcard before), and 0.0.0.0 remains an
    explicit opt-back-in. Only the Linux gateway-unknown case falls back wide: a silent
    loopback-only bind there would break every run instead of locking anything down.
    """
    hosts = agent_facing_loopbacks(configured_host)
    if hosts == ("0.0.0.0",):
        return hosts
    if _system() == "Linux":
        gateway = _docker_bridge_gateway()
        if not (gateway and _locally_bindable(gateway)):
            return ("0.0.0.0",)
        hosts = (*hosts, gateway)
    return hosts


def agent_facing_loopbacks(configured_host: str) -> tuple[str, ...]:
    """The addresses every local agent-facing spec binds BEFORE the docker bridge gateway is
    considered: the IPv4 + IPv6 loopbacks, plus a configured non-loopback host; the wildcard
    stays an explicit opt-in. Pure and docker-free, so port resolution (cli) can probe the same
    set `serve` will bind without a subprocess — the gateway it leaves out is a specific IPv4
    interface, which resolution's wildcard probe already refuses a squatter on."""
    if configured_host == "0.0.0.0":
        return ("0.0.0.0",)
    hosts: tuple[str, ...] = (LOOPBACK, "::1")
    if configured_host not in hosts and configured_host not in LOOPBACK_HOSTS:
        hosts = (*hosts, configured_host)
    return hosts


def apps() -> list[AppSpec]:
    """role:all — REST + OTLP receiver in one process."""
    s = get_settings()
    # Local: bind the agent-facing apps to the narrowest set that still lets the agent
    # container reach them via host.docker.internal (loopback + the docker-reach interface),
    # instead of the IPv4 wildcard that also exposed the unauthenticated /api to the LAN.
    bind = _agent_facing_bind_hosts(s.host) if s.deployment_topology == "local" else None
    return [
        AppSpec(build_rest_app(), s.rest_port, host=bind),
        AppSpec(build_otel_app(), s.otlp_port, "warning", host=bind),
    ]
