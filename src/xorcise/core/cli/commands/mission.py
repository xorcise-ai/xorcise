"""`xorcise mission` group — thin REST client (cli)."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

import typer

from xorcise.core.cli._resolve import resolve_mission
from xorcise.core.cli._shared import app, console, emit_json, err_console
from xorcise.core.cli._ux import (
    DASH,
    confirm_or_abort,
    difficulty_label,
    fail,
    field,
    heading,
    next_step,
    print_table,
    prose,
    source_label,
    ux_table,
)
from xorcise.core.cli.rest_client import RestClient

mission_app = typer.Typer(help="Browse, install, and manage missions.", no_args_is_help=True)
app.add_typer(mission_app, name="mission", rich_help_panel="Evaluate")

# Pull-job poll cadence + cap: a multi-GB image download can take many minutes; the job
# keeps running server-side past the cap (exit 3 = still in progress, not failure).
_PULL_POLL_SECONDS = 0.7
_PULL_POLL_CAP_SECONDS = 1800.0

_MISSION_HELP = "Mission id or name (see: xorcise mission list)."

# Filter vocabularies. A DOCUMENTED value that simply matches nothing is an empty
# result (exit 0), never a usage error — only a value outside the vocabulary is a
# mistake. (Validating against what happens to be installed rejected 'your-own'
# on any machine that had no local missions yet.)
_SOURCES = {"library": "library", "your-own": "your_own", "your_own": "your_own"}

# 'static' / 'lab' are internal type slugs — say what they MEAN for the agent.
_ENVIRONMENT_LABELS = {
    "static": "Static — no live environment; the agent works from the attachments",
    "lab": "Lab — a live containerised environment the agent connects to",
}
# The XORCISE proficiency ladder, ascending. Lower-cased because both sides of the
# filter are lower-cased before comparing. This is only the BUILT-IN half of the
# accepted set: whatever the live library carries is unioned in below, so a mission
# on an older scale still filters correctly while it is installed. Keeping retired
# words here instead would be worse than useless — they pass the vocabulary check,
# match nothing, and hand back an empty result the caller cannot distinguish from
# a genuinely empty library.
_DIFFICULTIES = ("novice", "advance beginner", "competent", "proficient", "expert")

# Pull-job phases → the words a user is waiting on (server phases stay internal).
_PHASE_LABELS = {
    "resolving": "resolving mission",
    "pulling_image": "downloading container image",
    "downloading_bundle": "downloading mission bundle",
    "installing": "installing",
    "done": "done",
}

# Module-level singleton (B008): a call in an argument default would re-run per import.
# Optional and unvalidated while `ingest` is disabled — see ingest_cmd for why.
_BUNDLE_DIR_ARGUMENT = typer.Argument(
    None,
    help="Mission bundle directory (contains mission.json).",
)


@mission_app.command("list")
def list_missions(
    installed: bool = typer.Option(False, "--installed", help="Only installed missions."),
    available: bool = typer.Option(False, "--available", help="Only missions not yet installed."),
    source: str | None = typer.Option(
        None, "--source", help="Filter by source: library or your-own."
    ),
    difficulty: str | None = typer.Option(
        None, "--difficulty", help="Filter by difficulty, e.g. expert."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw mission list as JSON (for scripting)."
    ),
) -> None:
    """Browse missions — your own and the free XORCISE library."""
    if installed and available:
        fail("--installed and --available are mutually exclusive", code=2)
    missions = RestClient().get("/missions")
    catalog = list(missions)
    filtered = (
        installed is True
        or available is True
        or (isinstance(source, str) and bool(source))
        or (isinstance(difficulty, str) and bool(difficulty))
    )
    if installed is True:
        missions = [c for c in missions if c.get("installed")]
    if available is True:
        missions = [c for c in missions if not c.get("installed")]
    if isinstance(source, str) and source:
        wanted = _SOURCES.get(source.lower())
        if wanted is None:
            fail(
                f"no source '{source}' — available: library, your-own",
                see=("xorcise mission list",),
                code=2,
            )
        missions = [c for c in missions if (c.get("source") or "").lower() == wanted]
    if isinstance(difficulty, str) and difficulty:
        wanted_level = difficulty.lower()
        # Accept the known vocabulary plus anything actually present (a catalog may
        # introduce a level this build has not heard of yet).
        known = set(_DIFFICULTIES) | {
            str(c.get("proficiency") or "").lower() for c in catalog if c.get("proficiency")
        }
        if wanted_level not in known:
            fail(
                f"no difficulty '{difficulty}' — available: {', '.join(sorted(known))}",
                see=("xorcise mission list",),
                code=2,
            )
        missions = [c for c in missions if (c.get("proficiency") or "").lower() == wanted_level]
    if as_json is True:
        emit_json(missions)
        return
    if not missions:
        if filtered:
            console.print("no missions match the filters — clear a filter and retry")
        else:
            console.print(
                "no missions yet — connect the catalog (xorcise catalog connect), "
                "then install one (xorcise mission pull <id>)",
                markup=False,
            )
        return
    table = ux_table("Source", "Id", "Name", "Difficulty", "State", title="Missions")
    ordered = sorted(
        missions,
        key=lambda c: (not c.get("installed"), str(c.get("name") or "").lower()),
    )
    for c in ordered:
        table.add_row(
            source_label(c.get("source")),
            str(c.get("mission_id") or DASH),
            str(c.get("name") or DASH),
            difficulty_label(c.get("proficiency")),
            "Installed" if c.get("installed") else "Available",
        )
    print_table(table)


@mission_app.command("show")
def show_mission(
    mission_id: str = typer.Argument(..., help=_MISSION_HELP),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw mission.json manifest as JSON (for scripting)."
    ),
) -> None:
    """Show a mission's details — your own copy if installed, else the library entry."""
    # The CLI half of the GUI mission-detail page (GET /missions/{id}/manifest); the
    # default view is the operator summary, --json emits the raw mission.json v2 object.
    client = RestClient()
    entry = resolve_mission(client, mission_id)
    mission_id = str(entry["mission_id"])
    m: dict[str, Any] = client.get(f"/missions/{mission_id}/manifest")
    if as_json is True:
        emit_json(m)
        return
    md = m.get("metadata") or {}
    installed = bool(entry.get("installed"))

    # Header: the name a person recognises, then the copy-critical id and state.
    console.print(f"[bold]{md.get('name') or entry.get('name') or mission_id}[/bold]")
    state = "Installed" if installed else "Not installed"
    console.print(
        f"[dim]{mission_id}  ·  {source_label(entry.get('source'))}  ·  {state}[/dim]",
        highlight=False,
    )

    console.print("")
    env = str(md.get("type") or "").lower()
    field("Environment", _ENVIRONMENT_LABELS.get(env, md.get("type") or DASH))
    field("Difficulty", difficulty_label(md.get("proficiency")))
    field("Specialty", str(md.get("specialty") or DASH).replace("-", " ").title())

    # Prose, wrapped. These were single unwrapped lines — a real objective runs to
    # ~1,500 characters and broke `| less`, editors and pasted tickets.
    if md.get("objective"):
        heading("Objective")
        console.print("[dim]Given to the agent when the run starts.[/dim]")
        prose(str(md["objective"]))
    if md.get("summary"):
        heading("Description")
        console.print("[dim]Context for you — not given to the agent.[/dim]")
        prose(str(md["summary"]))
    if md.get("skills"):
        heading("Security skills")
        prose(", ".join(str(s).replace("-", " ").title() for s in md["skills"]))
    if md.get("technologies"):
        heading("Technologies")
        prose(", ".join(str(t) for t in md["technologies"]))

    # The old `rubric=20 checks=8 …` run meant nothing to a human — name each one.
    heading("What's in it")
    counts = (
        ("Artifacts", len(m.get("artifacts") or ()), "the agent submits"),
        ("Rubric", len(m.get("rubric") or ()), "criteria scored by the judge model"),
        ("Automated checks", len(m.get("checks") or ()), "scored without a model"),
        ("Intel", len(m.get("intel") or ()), "the agent can request"),
        ("Attachments", len(m.get("attachments") or ()), "given to the agent"),
    )
    for label, n, note in counts:
        if n:
            field(label, f"{n} {note}", width=17)
    if m.get("terrain"):
        field("Attack path", "mapped — the intended route through the mission", width=17)

    console.print("")
    if installed:
        next_step(f"xorcise run create --agent <name> --mission {mission_id}")
    else:
        console.print("Not installed — pull it before you can create a run.")
        next_step(f"xorcise mission pull {mission_id}")


def _watch_pull(client: RestClient, job_id: str) -> dict[str, Any]:
    """Poll a pull job to a terminal state, rendering honest progress on stderr.

    TTY: a live bar fed by the server's real byte counts (indeterminate spinner while
    totals are unknown — progress is never fabricated). Non-TTY: one line per phase
    change, no cursor movement. Returns the final job view (may still be 'pulling'
    when the cap expires — the job continues server-side)."""
    deadline = time.monotonic() + _PULL_POLL_CAP_SECONDS

    def _poll() -> dict[str, Any]:
        view: dict[str, Any] = client.get(f"/missions/pull-jobs/{job_id}")
        return view

    if err_console.is_terminal:
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=err_console,
            transient=True,
        ) as progress:
            task = progress.add_task(_PHASE_LABELS["resolving"], total=None)
            while True:
                view = _poll()
                total = view.get("bytes_total") or 0
                progress.update(
                    task,
                    description=_PHASE_LABELS.get(view.get("phase") or "", "working"),
                    total=total if total > 0 else None,
                    completed=view.get("bytes_current") or 0,
                )
                if view.get("status") != "pulling" or time.monotonic() >= deadline:
                    return view
                time.sleep(_PULL_POLL_SECONDS)
    last_phase = None
    while True:
        view = _poll()
        if view.get("phase") != last_phase:
            last_phase = view.get("phase")
            err_console.print(f"{_PHASE_LABELS.get(last_phase or '', 'working')}…", markup=False)
        if view.get("status") != "pulling" or time.monotonic() >= deadline:
            return view
        time.sleep(_PULL_POLL_SECONDS)


def _request_cancel(client: RestClient, job_id: str) -> None:
    """Best-effort server-side cancel of an in-flight pull job (Ctrl-C).

    A failed cancel POST (e.g. the server went away) must NOT mask the interrupt — the exit 130
    stands regardless — so RestClient's clean-exit (typer.Exit) is swallowed. A SECOND Ctrl-C
    landing inside this POST is swallowed too: the caller still falls through to `raise Exit(130)`,
    so an impatient double-interrupt keeps the documented interrupted exit code."""
    with contextlib.suppress(typer.Exit, KeyboardInterrupt):
        client.post(f"/missions/pull-jobs/{job_id}/cancel", json={})


@mission_app.command("pull")
def pull_mission_cmd(
    mission_id: str = typer.Argument(..., help=_MISSION_HELP),
) -> None:
    """Install a library mission so it becomes runnable (downloads its image)."""
    client = RestClient()
    entry = resolve_mission(client, mission_id)
    mission_id = str(entry["mission_id"])
    if entry.get("installed"):
        # Safe no-op: converging on 'already installed' is success, not an error.
        console.print(f"'{entry.get('name') or mission_id}' is already installed", markup=False)
        next_step(f"xorcise run create --agent <name> --mission {mission_id}")
        return
    started = client.post(f"/missions/{mission_id}/pull-jobs", json={})
    job_id = started["job_id"]
    try:
        view = _watch_pull(client, job_id)
    except KeyboardInterrupt:
        # Ctrl-C stops the pull server-side too (not just this client): request the cancel, then
        # exit 130 (interrupted). The worker aborts the download and records the job 'cancelled'.
        err_console.print("\ncancelling the pull…")
        _request_cancel(client, job_id)
        raise typer.Exit(130) from None
    if view.get("status") == "installed":
        name = (view.get("entry") or {}).get("name") or entry.get("name") or mission_id
        console.print(f"installed '{name}' ({mission_id})", markup=False)
        next_step(f"xorcise run create --agent <name> --mission {mission_id}")
        return
    if view.get("status") == "error":
        fail(
            f"pull failed — {view.get('detail') or 'unknown error'}",
            see=("xorcise doctor",),
        )
    if view.get("status") == "cancelled":
        # Terminal: the pull was cancelled (by the GUI or another client — this CLI's own Ctrl-C
        # exits 130 above) and nothing was installed. A clean, intentional stop: not a failure
        # (exit 1) and not still-in-progress (exit 3), so exit 0 — parity with the GUI's terminal
        # "Pull cancelled." state.
        err_console.print(f"pull cancelled — '{mission_id}' was not installed", markup=False)
        raise typer.Exit(0)
    # Cap expired with the job still running server-side: in progress, not failure.
    console.print(
        f"still pulling — the download continues in the background (job {job_id}); "
        "check [value]xorcise mission list[/value]"
    )
    raise typer.Exit(3)


@mission_app.command("delete")
@mission_app.command("rm", hidden=True)
def delete_mission_cmd(
    mission_id: str = typer.Argument(..., help=_MISSION_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Uninstall an installed mission. Available as both `delete` and `rm`.

    Removes the local install so the mission leaves Your Own / drops back to \
not-installed in the library. Recorded runs and results are kept; the fused \
image stays in the local store.
    """
    client = RestClient()
    entry = resolve_mission(client, mission_id)
    mission_id = str(entry["mission_id"])
    if not entry.get("installed"):
        # Resolves to a real catalog entry but nothing is installed — answer with the
        # recovery pointer every other not-found carries, not a bare server 404.
        fail(
            f"'{entry.get('name') or mission_id}' is not installed — nothing to uninstall",
            see=("xorcise mission list --installed",),
        )
    confirm_or_abort(
        f"Uninstall mission '{mission_id}'? (runs and results are kept)", assume_yes=yes
    )
    client.delete(f"/missions/{mission_id}")
    console.print(f"deleted mission '{mission_id}'", markup=False)


@mission_app.command("ingest")
def ingest_cmd(bundle_dir: Path | None = _BUNDLE_DIR_ARGUMENT) -> None:
    """Add your own mission from a local bundle (coming soon).

    Authoring your own missions is not part of this release. Browse and install the
    published missions with `xorcise mission list` and `xorcise mission pull` instead.
    """
    # The argument is OPTIONAL and unvalidated while this is disabled, so every form of the
    # command reaches this message. A required, exists=True argument would fail first and
    # tell the operator their path was wrong, when the real answer is that the feature is
    # not available yet.
    console.print("Ingesting your own mission bundle is coming soon.")
    console.print(
        "For now, browse the published missions with [value]xorcise mission list[/value] "
        "and install one with [value]xorcise mission pull <id>[/value]."
    )
