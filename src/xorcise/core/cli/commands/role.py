"""`xorcise role` group — service-role inspection for operators (cli, advanced).

Roles matter only for multi-machine (distributed) deployments, where `serve
--role <key>` boots a subset of the services on each machine. A default local
install runs role `all` and never needs to touch this.
"""

from __future__ import annotations

import os

import typer

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import print_table, ux_table

role_app = typer.Typer(
    help="Show or list the service roles `serve` can boot (advanced, EXPERIMENTAL).",
    no_args_is_help=True,
)
app.add_typer(role_app, name="role", rich_help_panel="Advanced")

# Role key → (label, purpose, what-it-means sentence, maturity). Keys stay visible — they
# are exactly what `xorcise serve --role <key>` takes. `maturity` is the honest state of each
# role, established by booting all five and probing them and consistent with
# the role-activation milestone: activation machinery only, NOT the roles' real behaviour.
_READY = "Ready"
_EXPERIMENTAL = "Experimental"

_ROLES: dict[str, tuple[str, str, str, str]] = {
    "all": (
        "All services",
        "Run all XORCISE services on this machine",
        "This installation runs the control plane, runner, networking, and "
        "telemetry services together.",
        _READY,
    ),
    "control": (
        "Control",
        "Run the API and control-plane services",
        "This installation runs only the API and control-plane services. Run execution is "
        "stubbed on this role — runs are accepted but never launched.",
        _EXPERIMENTAL,
    ),
    "runner": (
        "Runner",
        "Run mission execution services",
        "Serves only a health endpoint today — the runner control-service is not built yet.",
        _EXPERIMENTAL,
    ),
    "headscale": (
        "Headscale",
        "Run the private-network coordination service",
        "Serves only a health endpoint today. The Headscale that runs actually use is the "
        "container provisioned by 'xorcise up'.",
        _EXPERIMENTAL,
    ),
    "collector": (
        "Collector",
        "Receive agent telemetry",
        "This installation runs only the telemetry receiver, which does receive and store "
        "agent traces.",
        _EXPERIMENTAL,
    ),
}

_EXPERIMENTAL_NOTE = (
    "Only 'all' is a complete install. The other roles boot, but multi-machine deployment "
    "is unfinished — treat them as experimental."
)


@role_app.command("show")
def role_show(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the role record as JSON (for scripting)."
    ),
) -> None:
    """Show the service role THIS SHELL would boot (advanced; EXPERIMENTAL).

    You only change roles in a multi-machine deployment — via `xorcise serve
    --role <key>` or the XORCISE_ROLE environment variable."""
    key = os.environ.get("XORCISE_ROLE", "all")
    label, purpose, meaning, maturity = _ROLES.get(key, (key.title(), "", "", _EXPERIMENTAL))
    if as_json is True:
        # `experimental` is a BOOLEAN as well as the display string: a script gating on
        # maturity should not have to string-match "Experimental". `source` says where the
        # key came from, because this reads the shell env, not a running server.
        emit_json(
            {
                "key": key,
                "label": label,
                "purpose": purpose,
                "maturity": maturity,
                "experimental": maturity != _READY,
                "meaning": meaning,
                "source": "XORCISE_ROLE" if "XORCISE_ROLE" in os.environ else "default",
            }
        )
        return
    console.print("[bold]Service role for this shell[/bold]\n")
    console.print(f"Role:     {label}")
    console.print(f"Key:      {key}")
    console.print(f"Maturity: {maturity}")
    if meaning:
        console.print(f"\n{meaning}")
    if maturity == _EXPERIMENTAL:
        console.print(f"\n[warn]{_EXPERIMENTAL_NOTE}[/warn]")
    # This reads XORCISE_ROLE from the environment — it is what a NEW `serve` here would boot,
    # not what any already-running server is serving. Saying "Active role" while reporting the
    # shell's env is how this command could confidently name a role nothing is running.
    console.print(
        "\n[dim]This is this shell's setting. For the role a running server is actually "
        "serving, use: xorcise system[/dim]"
    )


@role_app.command("list")
def role_list(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the role list as JSON (for scripting)."
    ),
) -> None:
    """List the service roles `serve` can boot (advanced; EXPERIMENTAL).

    The Key column is what `xorcise serve --role <key>` takes; a default local
    install runs 'all' and never needs another role."""
    from xorcise.core.roles.registry import load_manifest

    keys = list(load_manifest().roles)
    if as_json is True:
        emit_json(
            [
                {
                    "key": k,
                    "label": lbl,
                    "purpose": purpose,
                    "maturity": maturity,
                    "experimental": maturity != _READY,
                }
                for k in keys
                for lbl, purpose, _m, maturity in [
                    _ROLES.get(k, (k.title(), "—", "", _EXPERIMENTAL))
                ]
            ]
        )
        return

    table = ux_table("Role", "Key", "Maturity", "Purpose")
    for key in keys:
        label, purpose, _meaning, maturity = _ROLES.get(key, (key.title(), "—", "", _EXPERIMENTAL))
        style = "[ok]Ready[/ok]" if maturity == _READY else "[warn]Experimental[/warn]"
        table.add_row(label, key, style, purpose)
    print_table(table)
    console.print(f"\n[warn]{_EXPERIMENTAL_NOTE}[/warn]")
    # The note above already says "multi-machine", so the hint does not repeat it — it names the
    # step that was missing instead: booting a role is only half a distributed test, the machines
    # still have to be told how to reach each other, and only the CLI sets those addresses.
    console.print("[dim]boot one:  xorcise serve --role <key>[/dim]")
    console.print("[dim]then point the machines at each other:  xorcise config set-network[/dim]")
