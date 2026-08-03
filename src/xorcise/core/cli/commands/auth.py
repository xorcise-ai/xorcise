"""`xorcise auth` group — authentication commands (cli)."""

from __future__ import annotations

import typer

from xorcise.core.cli._shared import app, console

# Hidden: no working auth surface ships yet — invocable for forward-compat, but it
# must not advertise itself on a v1.0 --help.
auth_app = typer.Typer(help="Authentication helpers.", hidden=True, no_args_is_help=True)
app.add_typer(auth_app, name="auth", hidden=True)


@auth_app.command("aws")
def auth_aws() -> None:
    """AWS authentication (coming soon)."""
    console.print("AWS auth is coming soon.")
