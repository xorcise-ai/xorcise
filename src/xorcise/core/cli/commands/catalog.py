"""`xorcise catalog` group — the online mission library (cli, thin REST client).

Two distinct states, never conflated: **Configuration** (is the library enabled —
a saved setting, from /config) and **Live status** (is the remote endpoint
answering right now — a probe, from /catalog/status). `connect`/`disconnect`
change the setting (the same one the GUI Settings switch writes); `status`
reports both. A saved preference is never reported as a verified connection.
"""

from __future__ import annotations

from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json, err_console
from xorcise.core.cli._ux import DASH, humanize_when, next_step
from xorcise.core.cli.rest_client import RestClient

catalog_app = typer.Typer(
    help="Connect or disconnect the online mission library.", no_args_is_help=True
)
app.add_typer(catalog_app, name="catalog", rich_help_panel="Configuration")

# The live check makes the SERVICE probe the remote library — give it headroom
# past the 5s control-call default so a slow remote degrades gracefully here
# instead of surfacing as a generic client timeout.
_LIVE_CHECK_TIMEOUT_SECONDS = 15.0


def _catalog_config(client: RestClient) -> dict[str, Any]:
    """The saved library setting (enabled + endpoint) from /config."""
    view: dict[str, Any] = client.get("/config")
    return dict(view.get("catalog") or {})


def _live_status(client: RestClient) -> dict[str, Any] | None:
    """The live probe, or None when the service didn't answer it in time."""
    live = client.get_or_none("/catalog/status", timeout=_LIVE_CHECK_TIMEOUT_SECONDS)
    return dict(live) if isinstance(live, dict) else None


@catalog_app.command("status")
def status(
    as_json: bool = typer.Option(
        False, "--json", help="Emit configured + live state as JSON (for scripting)."
    ),
) -> None:
    """Check the mission library connection.

    Shows the saved setting (enabled/disabled) AND whether the online library is
    actually reachable right now — non-zero exit when it is enabled but unreachable.
    """
    client = RestClient()
    cfg = _catalog_config(client)  # service down → the clean connection error, here
    enabled = bool(cfg.get("connected"))
    live = _live_status(client)
    reachable = bool(live and live.get("state") == "connected")
    if as_json is True:
        body: dict[str, Any] = (
            dict(live)
            if live is not None
            else {
                "state": "unreachable",
                "message": "the XORCISE service did not answer the live check",
            }
        )
        body["configured"] = enabled
        body["url"] = cfg.get("url")
        emit_json(body)
        if enabled and not reachable:
            raise typer.Exit(1)
        return
    console.print("[bold]XORCISE Mission Library[/bold]\n")
    console.print(f"Configuration: {'Enabled' if enabled else 'Disabled'}")
    console.print(f"Endpoint:      {cfg.get('url') or DASH}", markup=False)
    if not enabled:
        console.print(f"Live status:   {DASH} (library disabled)")
        console.print("\nenable it: [value]xorcise catalog connect[/value]")
        return
    if reachable:
        console.print("Live status:   [ok]Reachable[/ok]")
    elif live is None:
        console.print("Live status:   [err]Unreachable[/err] — no answer to the live check")
    else:
        detail = live.get("message") or "last check failed"
        console.print(f"Live status:   [err]Unreachable[/err] — {detail}", highlight=False)
    console.print(f"Last success:  {humanize_when((live or {}).get('last_sync'))}")
    if not reachable:
        err_console.print(
            "\nThe library is enabled, but XORCISE could not reach it.\n"
            "diagnose: [value]xorcise doctor[/value]"
        )
        raise typer.Exit(1)


@catalog_app.command("connect")
def connect(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw config view as JSON (for scripting)."
    ),
) -> None:
    """Enable access to the online XORCISE mission library."""
    client = RestClient()
    current: dict[str, Any] = client.get("/config")
    if dict(current.get("catalog") or {}).get("connected"):
        # Already enabled is a SUCCESS (exit 0), so --json has to answer in the SAME shape
        # the setter path emits. It used to fall through to prose here, which broke
        # `xorcise catalog connect --json | jq` exactly when the library was connected —
        # i.e. in the normal state.
        if as_json is True:
            emit_json(current)
            return
        console.print("the online mission library is already enabled")
        next_step("xorcise catalog status", label="check availability")
        return
    view: dict[str, Any] = client.put("/config/catalog", {"connected": True})
    if as_json is True:
        emit_json(view)
        return
    # The setting saved; now say whether the library actually answers — a saved
    # preference must never read as a verified network connection.
    live = _live_status(client)
    if live and live.get("state") == "connected":
        console.print("online mission library enabled")
        next_step("xorcise mission list")
    else:
        console.print("online mission library enabled, but the endpoint could not be reached")
        console.print("your setting was saved; local missions remain available")
        next_step("xorcise doctor", label="diagnose")


@catalog_app.command("disconnect")
def disconnect(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw config view as JSON (for scripting)."
    ),
) -> None:
    """Disable the online library and show local missions only."""
    client = RestClient()
    current: dict[str, Any] = client.get("/config")
    if not dict(current.get("catalog") or {}).get("connected"):
        # Same idempotent-early-return bug as `connect` (see there). The audit reported only
        # `connect` because the catalog was CONNECTED during the run, so `disconnect` never
        # reached this branch — the defect was symmetric all along.
        if as_json is True:
            emit_json(current)
            return
        console.print("the online mission library is already disabled")
        return
    view: dict[str, Any] = client.put("/config/catalog", {"connected": False})
    if as_json is True:
        emit_json(view)
        return
    console.print("online mission library disabled")
    console.print("`xorcise mission list` will now show local missions only")
