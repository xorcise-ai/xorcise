"""xorcise.core.cli.app — Typer root + console-script entrypoint (cli).

Command modules self-register on the shared ``app`` when imported here; this
module is the CLI composition root. ``main()`` is the top-level error guard:
no known failure class reaches the user as a traceback — a one-line
``error: …`` on stderr and exit 1 instead (``XORCISE_DEBUG=1`` re-raises).
"""

from __future__ import annotations

import os

import typer

from xorcise import __version__
from xorcise.core.cli._errors import install_compact_errors
from xorcise.core.cli._shared import app, console, err_console

# Importing the command modules registers their commands/sub-apps on ``app`` — in
# HELP-PANEL order, not alphabetical: the first command registered fixes its panel's
# position in --help (Getting started → Evaluate → Configuration → Advanced), and
# within a panel commands list in registration order (the golden path).
# isort: off
from xorcise.core.cli.commands import (  # noqa: F401
    lifecycle,  # Getting started: up, ui, status, doctor, down
    agent,  # Evaluate: agent → mission → run → leaderboard (the golden path)
    mission,
    run,
    results,
    config,  # Configuration: config, catalog, system
    catalog,
    system,
    role,  # Advanced: role, db, serve
    db,
    serve,
    auth,  # hidden
    remote,  # hidden
)

# isort: on

# Swap typer's wide red usage-error panel for compact, actionable errors —
# installed at import time, so the binary and CliRunner get it alike.
install_compact_errors()

# Bare `xorcise` in a terminal: a compact brand header (the crosshair mark +
# wordmark + doctrine on one line), so the no-argument screen and --help feel
# like the same product. Suppressed when piped (non-TTY) and on --help/--version
# — decoration never reaches a parser.
_BANNER = "[accent]⊕ XORCISE[/] [dim]— Trust Evidence, not Claims.[/dim]"


def _version_callback(value: bool) -> None:
    if value:
        # typer.echo, not Rich: the version string is a machine-read artifact.
        typer.echo(f"xorcise {__version__}")
        raise typer.Exit()


@app.command(hidden=True)
def version() -> None:
    """Show the xorcise version and exit (same as --version)."""
    # Accept the bare word too — `xorcise version` is a natural guess, and a
    # usage error there is a needless guess-then-repeat moment.
    typer.echo(f"xorcise {__version__}")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the xorcise version and exit.",
    ),
) -> None:
    """XORCISE — evaluate cyber AI agents."""
    if ctx.invoked_subcommand is None:
        if console.is_terminal:
            console.print(_BANNER)
        typer.echo(ctx.get_help())
        raise typer.Exit()


def main() -> None:
    # Imported here, not at module top: the guard needs the error taxonomy only
    # when a command actually fails, and the CLI package must stay import-light.
    from xorcise.core.contracts.errors import ContractError
    from xorcise.core.headscale.cli import HeadscaleError
    from xorcise.core.headscale.provision import ProvisionError
    from xorcise.core.roles.registry import RoleError

    debug = os.environ.get("XORCISE_DEBUG") == "1"
    try:
        app()
    except BrokenPipeError:
        # Downstream pipe closed early (e.g. `| head`) — not an error; exit quietly.
        raise SystemExit(0) from None
    except (ContractError, RoleError, HeadscaleError, ProvisionError, OSError) as exc:
        if debug:
            raise
        err_console.print(f"[err]error[/err]: {exc}")
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        if debug:
            raise
        err_console.print(
            f"[err]unexpected error[/err]: {exc} — re-run with XORCISE_DEBUG=1 "
            "for the full traceback"
        )
        raise SystemExit(1) from exc
