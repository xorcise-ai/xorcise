"""`xorcise serve` — foreground multi-app driver (cli).

Lives in its own module, registered LAST among the visible commands in app.py, so
the Advanced help panel renders after Getting started / Evaluate / Configuration.
It shares its machinery with `up` and calls it THROUGH the lifecycle module
namespace, so tests patching ``lifecycle.*`` intercept serve unchanged.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum

import typer

from xorcise.core.cli._preflight import conflict_message, ports_in_use
from xorcise.core.cli._shared import app, console, err_console
from xorcise.core.cli.commands import lifecycle
from xorcise.core.home import init_home, scaffold_config, xorcise_home
from xorcise.core.roles.activate import activate


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
    import uvicorn

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
    taken = ports_in_use(host, [s.port for s in specs])
    if taken:
        err_console.print(f"[err]{conflict_message(host, taken)}[/err]")
        raise typer.Exit(1)

    async def _run() -> list[BaseException]:
        servers = [
            uvicorn.Server(uvicorn.Config(s.app, host=h, port=s.port, log_level=s.log_level))
            for s in specs
            for h in lifecycle._bind_hosts(s.host or host)
        ]
        tasks = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for s in servers:
            s.should_exit = True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            r
            for r in results
            if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)
        ]

    console.print(lifecycle._serve_banner(role_name, specs))
    try:
        failures = asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("shutting down")
        return
    if failures:
        # A crashed server plane must not exit 0 — surface the first failure.
        err_console.print(f"[err]error[/err]: server exited — {failures[0]}")
        raise typer.Exit(1)
