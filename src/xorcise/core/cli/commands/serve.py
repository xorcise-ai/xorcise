"""`xorcise serve` — foreground multi-app driver (cli).

Lives in its own module, registered LAST among the visible commands in app.py, so
the Advanced help panel renders after Getting started / Evaluate / Configuration.
It shares its machinery with `up` and calls it THROUGH the lifecycle module
namespace, so tests patching ``lifecycle.*`` intercept serve unchanged.
"""

from __future__ import annotations

import asyncio
import errno
import socket
from dataclasses import dataclass, field
from enum import StrEnum

import typer

from xorcise.core.cli._preflight import (
    IPV6_LOOPBACK,
    address_unavailable,
    bind_listener,
    conflict_message,
)
from xorcise.core.cli._shared import app, console, err_console
from xorcise.core.cli.commands import lifecycle
from xorcise.core.home import init_home, scaffold_config, xorcise_home
from xorcise.core.roles.activate import activate
from xorcise.core.roles.boot import AppSpec


class Role(StrEnum):
    """Roles `serve` can boot — a closed set so a typo (or `--role --help`) is a
    parse-time usage error listing the choices, never a deep UnknownRoleError."""

    all = "all"
    control = "control"
    runner = "runner"
    headscale = "headscale"
    collector = "collector"


# Module-level singleton (B008): a call in an argument default would re-run per import.
_ROLE_OPTION = typer.Option(
    Role.all,
    "--role",
    # "a skeleton" told the operator a verdict without a fact. What they need in one line is
    # WHICH way it is incomplete; the boot warning then names the specific caveat per role.
    help="Role to boot. EXPERIMENTAL unless 'all' — the others serve only part of the API.",
)

# Every role except `all` is EXPERIMENTAL scaffolding. The role work shipped the ACTIVATION
# machinery and says so explicitly ("not any role's real behaviour"; distributed transport is
# "define-don't-build"), so this is documented intent rather than a regression — but nothing
# said it to the operator at the one moment it matters, which is boot.
_EXPERIMENTAL_ROLES = frozenset({"control", "runner", "headscale", "collector"})

# What each experimental role actually gives you, verified by booting every one of them.
# Ordered worst-surprise-first inside each message.
_ROLE_CAVEATS: dict[str, tuple[str, ...]] = {
    "control": (
        "Run execution is SILENTLY STUBBED on this role: POST /runs answers 201 and returns a "
        "run id and image ref, but no container is ever launched and the run stays 'created'.",
        "Only 'all' and 'runner' own the Docker/Headscale planes (rest/run_create.py).",
    ),
    "runner": (
        "This role serves only GET /healthz — the real runner control-service is not built yet, "
        "so no XORCISE server can hand it work.",
    ),
    "headscale": (
        "This role serves only GET /healthz. The Headscale that runs actually use is the "
        "container provisioned by 'xorcise up', which is unrelated to this role.",
    ),
    "collector": (),  # genuinely receives and stores OTLP — no caveat beyond 'experimental'
}


def _warn_experimental_role(role: str) -> None:
    """Name the experimental role's limits at boot, before anything appears to work."""
    if role not in _EXPERIMENTAL_ROLES:
        return
    err_console.print(
        f"[warn]warning[/warn]: role {role!r} is EXPERIMENTAL — "
        f"multi-machine deployment is not finished.",
        highlight=False,
    )
    for caveat in _ROLE_CAVEATS.get(role, ()):
        err_console.print(f"[dim]  {caveat}[/dim]", highlight=False)
    err_console.print("[dim]  For a working install use 'xorcise up' (role 'all').[/dim]")


@dataclass
class BoundSpec:
    """One app, already bound on every address it must serve: ONE uvicorn.Server, N sockets.

    The previous shape — one uvicorn.Server per (app, address), all sharing the same app
    object — ran the ASGI lifespan once per SERVER, so every `@app.on_event("startup")` hook
    fired once per bind address: on Linux role:all resolves three agent-facing addresses, so
    the REST app got three reconciles, three budget watchdogs and three readiness gates
    scanning the same runs (two of them closing out the same failed run milliseconds apart,
    the loser dying on a 409), and shutdown stopped only the last one assigned. uvicorn runs
    the lifespan once per Server and opens one listener per socket it is handed
    (`Server.serve(sockets=…)` — the path gunicorn workers use), so one Server per app with a
    socket per address is the arrangement that matches what the hooks assume.
    """

    spec: AppSpec
    hosts: list[str] = field(default_factory=list)
    sockets: list[socket.socket] = field(default_factory=list)

    def close(self) -> None:
        for sock in self.sockets:
            sock.close()


class BindConflict(Exception):
    """A port `serve` needs is held by another process; `taken` maps host → ports."""

    def __init__(self, taken: dict[str, list[int]]) -> None:
        self.taken = taken
        super().__init__(", ".join(f"{h}:{p}" for h, ports in taken.items() for p in ports))


def bind_specs(specs: list[AppSpec], default_host: str) -> list[BoundSpec]:
    """Bind every spec on every address `_bind_hosts` resolves for it, BEFORE the event loop.

    Binding is the preflight: the same sockets are handed to uvicorn, so nothing can take a
    port between the check and the listen, and a conflict surfaces here as a per-address
    OSError we report cleanly — not from inside uvicorn's Server.startup(), which calls
    sys.exit(1) and escaped serve()'s handling as a raw traceback. Every address is probed in
    its own family, so a squatter on `[::1]:3001` is found the same as one on the IPv4 loopback
    (the IPv4-only probe used to pass it).

    The IPv6 loopback companion is the one address allowed to be missing: on a host with IPv6
    disabled it does not exist, which is not a conflict — the IPv4 listeners still serve, and
    one warning says so. Any other unbindable address is a configuration error and is fatal.
    On a conflict every socket bound so far is closed before BindConflict is raised.
    """
    bound: list[BoundSpec] = []
    taken: dict[str, list[int]] = {}
    for spec in specs:
        entry = BoundSpec(spec)
        bound.append(entry)
        for host in lifecycle._bind_hosts(spec.host or default_host):
            try:
                sock = bind_listener(host, spec.port)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    taken.setdefault(host, []).append(spec.port)
                    continue
                if host == IPV6_LOOPBACK and address_unavailable(exc):
                    err_console.print(
                        f"[warn]warning[/warn]: IPv6 loopback is not available on this host — "
                        f"port {spec.port} will listen on IPv4 only",
                        highlight=False,
                    )
                    continue
                for b in bound:
                    b.close()
                raise
            entry.hosts.append(host)
            entry.sockets.append(sock)
        if not entry.sockets and not taken:
            # Every address was skipped (only the IPv6 loopback was configured and it does not
            # exist here): a Server with no listener would boot "healthy" and answer nothing.
            for b in bound:
                b.close()
            raise OSError(errno.EADDRNOTAVAIL, f"no bindable address for port {spec.port}")
    if taken:
        for b in bound:
            b.close()
        raise BindConflict(taken)
    return bound


async def serve_bound(bound: list[BoundSpec]) -> list[BaseException]:
    """Run one uvicorn.Server per bound app until the first exits; return the failures."""
    import uvicorn

    servers = [
        (
            uvicorn.Server(
                uvicorn.Config(
                    b.spec.app,
                    # Only labels the "Uvicorn running on" line — the listeners are the sockets
                    # (bind_specs guarantees at least one).
                    host=b.hosts[0],
                    port=b.spec.port,
                    log_level=b.spec.log_level,
                )
            ),
            b.sockets,
        )
        for b in bound
    ]
    tasks = [asyncio.create_task(server.serve(sockets=socks)) for server, socks in servers]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for server, _ in servers:
        server.should_exit = True
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [
        r
        for r in results
        if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)
    ]


@app.command(rich_help_panel="Advanced")
def serve(
    role: Role = _ROLE_OPTION,
    stub: bool = typer.Option(False, "--stub", help="Force stub adapters (Docker-less dev/demo)."),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        min=1,
        max=65535,
        help="REST port (default from config; auto-increments when busy).",
    ),
    otlp_port: int | None = typer.Option(
        None,
        "--otlp-port",
        min=1,
        max=65535,
        help="OTLP port (default from config; auto-increments when busy).",
    ),
) -> None:
    """Run XORCISE in the foreground (advanced; see also: up, which runs it in the background).

    --role is EXPERIMENTAL: only 'all' (the default) is a complete install. The other roles
    boot, but multi-machine deployment is unfinished — see `xorcise role list`.

    Note: sibling commands (status/ui) discover relocated ports only for servers
    started via `up`.
    """
    from xorcise.core.config import get_settings

    # isinstance guards direct (non-CLI) calls that pass a plain role string.
    role_name = role.value if isinstance(role, Role) else str(role)
    # BEFORE any work: an operator who is about to get a skeleton should know it now, not after
    # a run silently does nothing. `up` cannot reach here with a non-'all' role (it spawns
    # `serve` with no --role), so this foreground path is the only way in.
    _warn_experimental_role(role_name)
    lifecycle._apply_runtime_env(role_name, stub=stub)
    # Bootstrap like `up`: a bare `serve` in a fresh home must not boot 'healthy'
    # against a schema-less DB and then 500 on every DB-backed call.
    init_home()
    scaffold_config(xorcise_home())
    lifecycle._ensure_db_ready()
    # Resolve ports BEFORE activate(): role_<x>.apps() captures get_settings() ports at
    # build time, so the resolved values must already be stamped when specs are built.
    resolved = lifecycle._resolve_role_ports(role_name, {"rest": port, "otlp": otlp_port})
    lifecycle._apply_runtime_env(role_name, stub=stub, ports=resolved)
    host = get_settings().host
    specs = activate(role_name)
    # Bind first, on every address each app must serve. The bound sockets ARE the preflight
    # (see bind_specs) and are what uvicorn listens on — one Server per app, so the startup
    # hooks run once per app rather than once per address.
    try:
        bound = bind_specs(specs, host)
    except BindConflict as exc:
        for taken_host, ports in exc.taken.items():
            err_console.print(f"[err]{conflict_message(taken_host, ports)}[/err]")
        raise typer.Exit(1) from None
    except OSError as exc:
        err_console.print(f"[err]error[/err]: cannot bind {exc.strerror or exc}", highlight=False)
        raise typer.Exit(1) from None

    console.print(lifecycle._serve_banner(role_name, specs))
    try:
        failures = asyncio.run(serve_bound(bound))
    except KeyboardInterrupt:
        console.print("shutting down")
        return
    finally:
        for b in bound:
            b.close()  # uvicorn closes what it adopted; this covers a bind that never got there
    if failures:
        # A crashed server plane must not exit 0 — surface the first failure.
        err_console.print(f"[err]error[/err]: server exited — {failures[0]}")
        raise typer.Exit(1)
