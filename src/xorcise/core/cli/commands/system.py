"""`xorcise system` — the read-only Reflect view over GET /system (cli, thin REST client).

CLI half of the Settings Environment + Modules cards: the running
instance's services plus catalog/remotes, with the deployment internals (role, topology, db
schema, db url, home) behind --verbose. Distinct from `status`, which probes the ports from
THIS process — `system` asks the server what IT sees.
"""

from __future__ import annotations

from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import DASH, print_table, service_label, ux_table
from xorcise.core.cli.rest_client import RestClient


@app.command("system", rich_help_panel="Configuration")
def system(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Also show deployment internals (role, topology, db schema/url, home).",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw system view as JSON (for scripting)."
    ),
) -> None:
    """Show detailed information about the running XORCISE instance.

    See also: status, which probes the ports from this machine."""
    info: dict[str, Any] = RestClient().get("/system")
    if as_json is True:
        emit_json(info)
        return
    table = ux_table("Service", "State", "Address", title="XORCISE system")
    for plane in info.get("planes") or ():
        # Three states, not two. A module this host's role does not run carries ok=false
        # because it is not up — but it is ABSENT, not broken, and colouring it red would
        # report a correctly-configured single-role host as failing. The GUI makes the same
        # distinction; these two surfaces must never disagree about what is wrong.
        if plane.get("state") == "not_deployed":
            mark = "[dim]Not on this host[/dim]"
        elif plane.get("ok"):
            mark = "[ok]Healthy[/ok]"
        else:
            mark = f"[err]{plane.get('detail') or 'down'}[/err]"
        table.add_row(service_label(plane.get("name")), mark, plane.get("location") or DASH)
    print_table(table)
    # Configuration vs live state, never conflated: the /system block reflects the
    # service's own last look at the library — point at `catalog status` for a
    # fresh live answer instead of printing a bare 'connected'.
    cat = info.get("catalog") or {}
    cat_state = cat.get("state")
    if cat_state == "disconnected":
        console.print("mission library: disabled (enable: xorcise catalog connect)")
    elif cat_state == "error":
        console.print(
            "mission library: enabled — last live check failed (details: xorcise catalog status)"
        )
    else:
        console.print("mission library: enabled (live status: xorcise catalog status)")
    remotes = info.get("remotes") or ()
    console.print(f"remotes:   {', '.join(remotes) if remotes else '— (none registered)'}")
    if verbose is True:
        # Name the role's maturity here too: `role list` says it, so the deployment readout
        # must not quietly present an experimental role as an ordinary configuration value.
        role = info.get("role") or DASH
        suffix = "" if role in {"all", DASH} else "  [warn](experimental)[/warn]"
        console.print(f"role:      {role}{suffix}")
        console.print(f"topology:  {info.get('topology') or DASH}")
        schema = info.get("db_schema") or "unknown"
        if schema == "behind":
            console.print(
                "db schema: behind — run [value]xorcise db upgrade[/value], "
                "then restart ([value]xorcise down && xorcise up[/value])"
            )
        else:
            console.print(f"db schema: {schema}")
        console.print(f"db url:    {info.get('db_url') or DASH}", markup=False)
        console.print(f"home:      {info.get('home') or DASH}", markup=False)
