"""`xorcise agent` group — thin REST client (cli)."""

from __future__ import annotations

import typer

from xorcise.core.cli._resolve import mission_names_by_id, resolve_agent_name
from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import (
    DASH,
    confirm_or_abort,
    fail,
    fmt_score,
    kind_label,
    next_step,
    print_table,
    ux_table,
)
from xorcise.core.cli.rest_client import RestClient

agent_app = typer.Typer(help="Register and manage the agents you evaluate.", no_args_is_help=True)
app.add_typer(agent_app, name="agent", rich_help_panel="Evaluate")

_NAME_HELP = "Agent name (see: xorcise agent list)."
_LAUNCH_MODES = {"host", "container"}


def _validate_launch_mode(value: str | None) -> str | None:
    if value is not None and value not in _LAUNCH_MODES:
        fail(
            "launch mode must be 'host' or 'container'",
            example="xorcise agent register --name my-agent --launch-mode container",
            code=2,
        )
    return value


@agent_app.command("list")
def list_agents(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also show internal agent ids and endpoints."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw agent list as JSON (for scripting)."
    ),
) -> None:
    """List registered agents."""
    agents = RestClient().get("/agents")
    if as_json is True:
        emit_json(agents)
        return
    if not agents:
        console.print(
            "no agents registered — register one: xorcise agent register --name <name>",
            markup=False,
        )
        return
    columns = ["Name", "Kind", "Version", "Model"]
    if verbose is True:
        columns += ["Id", "Endpoint"]
    table = ux_table(*columns, title="Agents")
    for a in sorted(agents, key=lambda a: str(a.get("name") or "").lower()):
        row = [
            str(a.get("name") or DASH),
            kind_label(a.get("kind")),
            f"v{a.get('version', 1)}",
            str(a.get("model") or DASH),
        ]
        if verbose is True:
            row += [str(a.get("id") or DASH), str(a.get("endpoint") or DASH)]
        table.add_row(*row)
    print_table(table)


@agent_app.command("register")
def register_agent(
    name: str = typer.Option(..., "--name", help="Unique agent name."),
    endpoint: str | None = typer.Option(None, "--endpoint", help="How it connects."),
    otel: str | None = typer.Option(None, "--otel", help="How it emits its OTel trace."),
    model: str | None = typer.Option(None, "--model", help="Agent's disclosed model (optional)."),
    kind: str | None = typer.Option(
        None, "--kind", help="Agent harness for replay-adapter selection, e.g. 'claude-code'."
    ),
    launch_mode: str | None = typer.Option(
        None,
        "--launch-mode",
        help="Where its command runs: host (loopback addresses) or container.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw registered agent as JSON (for scripting)."
    ),
) -> None:
    """Register an agent by name (connection details are optional).

    --model discloses the model at registration (version 1); omit it to leave \
the model undisclosed rather than registering then updating (which would \
bump the version to 2).
    """
    if not name.strip():
        # An empty / whitespace-only name creates an agent that renders as the
        # missing-value sentinel and can't be addressed — reject it up front (exit 2).
        fail(
            "agent name cannot be empty",
            example="xorcise agent register --name my-agent",
            code=2,
        )
    client = RestClient()
    # Detect the duplicate BEFORE the POST so the answer is a next action, not a 409.
    if any(a.get("name") == name for a in client.get("/agents")):
        fail(
            f"agent '{name}' is already registered",
            example=f"xorcise agent update --name {name}",
            see=("xorcise agent list",),
        )
    body = {
        "name": name,
        "endpoint": endpoint,
        "otel": otel,
        "model": model,
        "kind": kind,
        "launch_mode": _validate_launch_mode(launch_mode),
    }
    created = client.post("/agents", json=body)
    if as_json is True:
        emit_json(created)
        return
    console.print(f"registered agent '{name}'", markup=False)
    next_step("xorcise mission list")


@agent_app.command("update")
def update_agent(
    name: str = typer.Option(..., "--name", help=_NAME_HELP),
    endpoint: str | None = typer.Option(None, "--endpoint", help="New connection endpoint."),
    otel: str | None = typer.Option(None, "--otel", help="New OTel trace endpoint."),
    model: str | None = typer.Option(None, "--model", help="Agent's disclosed model."),
    kind: str | None = typer.Option(
        None, "--kind", help="Agent harness for replay-adapter selection, e.g. 'claude-code'."
    ),
    launch_mode: str | None = typer.Option(
        None,
        "--launch-mode",
        help="Where its command runs: host (loopback addresses) or container.",
    ),
    rename_to: str | None = typer.Option(
        None, "--rename-to", help="New unique name for this agent."
    ),
) -> None:
    """Update an agent's declaration and bump its version (same agent id).

    Only the fields you pass change — omitted fields keep their current values.
    --rename-to changes the agent's name; runs and history stay attached (same id).
    """
    if all(v is None for v in (endpoint, otel, model, kind, launch_mode, rename_to)):
        # An empty update still PUT the declaration back and bumped the version —
        # a silent mutation from a no-op request. Refuse, like the config setters.
        fail(
            "nothing to update — pass at least one of --endpoint / --otel / --model / "
            "--kind / --launch-mode / --rename-to",
            example=f"xorcise agent update --name {name} --model claude-sonnet-5",
            code=2,
        )
    # Merge-before-PUT: the endpoint replaces the whole declaration, so overlay the
    # passed options onto the current one — otherwise an update that sets only --model
    # would silently clear endpoint/otel/kind.
    client = RestClient()
    name = resolve_agent_name(client, name)
    entry = next(a for a in client.get("/agents") if a["name"] == name)
    body = {
        "name": rename_to or name,
        "endpoint": endpoint if endpoint is not None else entry.get("endpoint"),
        "otel": otel if otel is not None else entry.get("otel"),
        "model": model if model is not None else entry.get("model"),
        "kind": kind if kind is not None else entry.get("kind"),
        "launch_command_template": entry.get("launch_command_template"),
        "launch_tips": entry.get("launch_tips"),
        "mission_preamble": entry.get("mission_preamble"),
        "launch_mode": (
            _validate_launch_mode(launch_mode)
            if launch_mode is not None
            else entry.get("launch_mode")
        ),
    }
    updated = client.put(f"/agents/{name}", json=body)
    v = updated.get("version") if isinstance(updated, dict) else None
    console.print(
        f"updated agent '{body['name']}'" + (f" (v{v})" if v is not None else ""), markup=False
    )


@agent_app.command("rename")
def rename_agent(
    old: str = typer.Argument(..., help="Current agent name."),
    new: str = typer.Argument(..., help="New unique agent name."),
) -> None:
    """Rename an agent, keeping its id, version history, and runs."""
    client = RestClient()
    old = resolve_agent_name(client, old)
    entry = next(a for a in client.get("/agents") if a["name"] == old)
    body = {
        "name": new,
        "endpoint": entry["endpoint"],
        "otel": entry["otel"],
        "model": entry["model"],
        "kind": entry["kind"],
        "launch_command_template": entry.get("launch_command_template"),
        "launch_tips": entry.get("launch_tips"),
        "mission_preamble": entry.get("mission_preamble"),
        "launch_mode": entry.get("launch_mode"),
    }
    client.put(f"/agents/{old}", json=body)
    console.print(f"renamed agent '{old}' → '{new}'", markup=False)


@agent_app.command("history")
def agent_history(
    name: str = typer.Argument(..., help=_NAME_HELP),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw history rows as JSON (for scripting)."
    ),
) -> None:
    """List an agent's recorded results over time, labelled by agent/mission version."""
    client = RestClient()
    name = resolve_agent_name(client, name)
    rows = client.get(f"/agents/{name}/history")
    if as_json is True:
        emit_json(rows)
        return
    if not rows:
        console.print(f"no recorded results for agent '{name}'")
        return
    # A history row carries only the run id — resolve each run's mission so the
    # table is readable standalone (no copying ids back into `run list`).
    runs_by_id = {str(r.get("run_id")): r for r in client.get("/runs")}
    mission_names = mission_names_by_id(client)
    table = ux_table(
        "Run",
        "Mission",
        "Overall",
        "Deterministic",
        "Judge",
        "Agent ver",
        "Mission ver",
        "Model",
        title=f"History — {name}",
    )
    for r in rows:  # oldest → newest (server order)
        c = r.get("conditions", {})
        overall = fmt_score(r["overall"]) + (" ⚠ partial" if r.get("partial") else "")
        slug = str(runs_by_id.get(str(r["run_id"]), {}).get("mission") or DASH)
        table.add_row(
            str(r["run_id"])[:8],
            mission_names.get(slug, slug),
            overall,
            fmt_score(r["deterministic"]),
            fmt_score(r["judge"]),
            f"v{c.get('agent_version', 1)}",
            f"v{c.get('mission_version', 1)}",
            str(c.get("model") or "not disclosed"),
        )
    print_table(table)


@agent_app.command("rm")
@agent_app.command("delete", hidden=True)
def remove_agent(
    name: str = typer.Argument(..., help="Agent name to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove a registered agent by name. Available as both `rm` and `delete`.

    Removes the agent, its version history, and its recorded runs/results.
    """
    client = RestClient()
    name = resolve_agent_name(client, name)
    confirm_or_abort(f"Remove agent '{name}' and its recorded runs?", assume_yes=yes)
    client.delete(f"/agents/{name}")
    console.print(f"removed agent '{name}'")
