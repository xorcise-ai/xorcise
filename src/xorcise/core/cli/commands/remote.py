"""`xorcise remote` group — remote xorcise instance commands (cli)."""

from __future__ import annotations

import typer

from xorcise.core.cli._shared import app, console, emit_json

# Hidden: a stub that always answers 'no remotes registered' — invocable for
# forward-compat, but it must not advertise itself on a v1.0 --help.
remote_app = typer.Typer(help="Manage remote xorcise instances.", hidden=True, no_args_is_help=True)
app.add_typer(remote_app, name="remote", hidden=True)


@remote_app.command("list")
def list_remotes(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the remote list as JSON (for scripting)."
    ),
) -> None:
    """List registered remotes."""
    if as_json is True:
        # A stub still owes a STABLE machine contract: an empty list parses and iterates,
        # so a script written against it keeps working when real remotes land.
        emit_json([])
        return
    console.print("no remotes registered")
