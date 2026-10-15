"""`xorcise system` — the read-only Reflect view over GET /system (cli, thin REST client).

CLI half of the Settings Environment + Modules cards: the running
instance's services plus catalog/remotes, with the deployment internals (role, topology, db
schema, db url, home) behind --verbose. Distinct from `status`, which probes the ports from
THIS process — `system` asks the server what IT sees.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.markup import escape

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import (
    DASH,
    humanize_when,
    print_table,
    service_label,
    short_id,
    ux_table,
)
from xorcise.core.cli.rest_client import RestClient


def boot_reconcile_lines(view: dict[str, Any] | None, *, verbose: bool = False) -> list[str]:
    """What the server's start-up reconcile did, as console lines (#173). Shared by `system` and
    `up`. Quiet when there is nothing to act on, unless `verbose`: a clean start needs no line."""
    if not view:
        return []
    state = view.get("state")
    if state in {"pending", "running"}:
        return (
            ["[dim]boot reconcile: checking the runs that were live before this start…[/dim]"]
            if verbose
            else []
        )
    if state == "failed":
        return [
            "[err]boot reconcile failed[/err]: "
            + escape(str(view.get("error") or "unknown error")),
            "  runs that were live before this start may not have been checked. It runs again on "
            "the next start, once Docker and Headscale are up "
            "([value]xorcise down && xorcise up[/value]).",
        ]
    parts = []
    if view.get("adopted"):
        parts.append(f"{view['adopted']} still running (picked back up)")
    if view.get("aborted"):
        parts.append(f"{view['aborted']} closed out as crashed (not graded)")
    if view.get("released"):
        parts.append(f"{view['released']} unfinished deploy(s) cleaned up")
    lines = []
    if parts:
        lines.append("boot reconcile: " + "; ".join(parts))
    elif verbose:
        lines.append("boot reconcile: nothing to recover")
    deferred = [str(r) for r in view.get("deferred") or ()]
    if deferred:
        lines.append(
            f"[warn]boot reconcile: {len(deferred)} unfinished deploy(s) could not be torn down"
            "[/warn] and are kept for the next start, so their tailnet nodes are not lost: "
            + ", ".join(short_id(r) for r in deferred)
        )
    return lines


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
    # Environments still up after a failed teardown (#173). Shown only when there are any: the
    # line exists to say something needs attention, usually a plane that is down.
    for line in boot_reconcile_lines(info.get("boot_reconcile"), verbose=verbose is True):
        console.print(line)
    releases = info.get("releases") or {}
    if releases.get("pending"):
        console.print(
            f"[warn]releases:  {releases['pending']} finished run(s) still hold their container or "
            f"tailnet nodes[/warn] (oldest ended {humanize_when(releases.get('oldest_ended_at'))}) "
            "— retried automatically"
        )
        if releases.get("oldest_error"):
            console.print(
                f"           [dim]oldest run's error: {escape(str(releases['oldest_error']))}[/dim]"
            )
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
