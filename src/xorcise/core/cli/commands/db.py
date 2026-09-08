"""`xorcise db` group — database management commands (cli)."""

from __future__ import annotations

import typer

from xorcise.core.cli._shared import app, console, err_console
from xorcise.core.db import upgrade as _upgrade

db_app = typer.Typer(help="Database maintenance (advanced).", no_args_is_help=True)
app.add_typer(db_app, name="db", rich_help_panel="Advanced")


_FORCE_OPTION = typer.Option(
    False,
    "--force",
    help="Migrate even if a XORCISE server of this home appears to be running (unsafe).",
)


@db_app.command("upgrade")
def db_upgrade(force: bool = _FORCE_OPTION) -> None:
    """Apply pending database migrations (explicit; never on boot).

    Refuses while a server of this home is running: the current migrations rebuild tables in
    place (SQLite batch mode — copy, swap, rename), so migrating under a live server replaces
    the tables it has open and renames columns its ORM is still using. The boot-time guard
    only ever pointed HERE ("run 'xorcise db upgrade' first"), and it fires precisely when an
    older server may still be up — so this end has to hold the line too (#74).
    """
    from alembic.util.exc import CommandError

    from xorcise.core.cli.commands.lifecycle import InstanceUndetermined, _live_instance
    from xorcise.core.config import get_settings

    # `is True`: a direct (non-CLI) call gets the typer OptionInfo as the default, and that
    # object is truthy — `if force:` would skip the guard that stops database corruption.
    if force is True:
        live = None
    else:
        try:
            live = _live_instance(get_settings())
        except InstanceUndetermined as exc:
            # Cannot tell whether a server holds the DB: the safe answer is no migration.
            err_console.print(
                f"[err]error[/err]: {exc}. Not migrating while that is unknown "
                "(--force overrides; unsafe.)",
                highlight=False,
            )
            raise typer.Exit(1) from None
    if live is not None:
        where = f"pid {live.pid}" if live.pid is not None else f"port {live.rest_port}"
        err_console.print(
            f"[err]error[/err]: XORCISE is running ({where}) and holds this database open — "
            "migrating it underneath a live server rewrites the tables it is using. "
            "Stop it first: [value]xorcise down[/value], then [value]xorcise db upgrade[/value], "
            "then [value]xorcise up[/value]. (--force overrides; unsafe.)"
        )
        raise typer.Exit(1)

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
