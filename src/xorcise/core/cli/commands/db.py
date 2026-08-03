"""`xorcise db` group — database management commands (cli)."""

from __future__ import annotations

import typer

from xorcise.core.cli._shared import app, console, err_console
from xorcise.core.db import upgrade as _upgrade

db_app = typer.Typer(help="Database maintenance (advanced).", no_args_is_help=True)
app.add_typer(db_app, name="db", rich_help_panel="Advanced")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply pending database migrations (explicit; never on boot)."""
    from alembic.util.exc import CommandError

    try:
        console.print(_upgrade())
    except CommandError as exc:
        # A DB stamped by a different build (renamed/removed migration) is a history
        # mismatch, not a crash — say what it is and how to get out of it.
        err_console.print(
            f"[err]error[/err]: migration history mismatch — {exc}. "
            "This database was created by a different build of xorcise. "
            "Back up ~/.xorcise/xorcise.db first; then run the build that created it, "
            "or re-initialise with [value]xorcise down --purge[/value] (destroys local data)."
        )
        raise typer.Exit(1) from exc
