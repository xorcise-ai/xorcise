"""Shared mission pull-job orchestration (cli).

`mission pull` and `run create` both install a library mission by starting a
server-side pull job and watching it to a terminal state. The watch, the Ctrl-C
cancel contract and the progress render must be identical in both places — one
progress bar, one interrupt behaviour — so they live here, not in either command
module. What a terminal state MEANS stays with the caller: 'installed' is the
goal for `mission pull` but only a step on the way to a run for `run create`.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import typer

from xorcise.core.cli._shared import err_console
from xorcise.core.cli.rest_client import RestClient

# Pull-job poll cadence + cap: a multi-GB image download can take many minutes; the job
# keeps running server-side past the cap (exit 3 = still in progress, not failure).
PULL_POLL_SECONDS = 0.7
PULL_POLL_CAP_SECONDS = 1800.0

# Pull-job phases → the words a user is waiting on (server phases stay internal).
PHASE_LABELS = {
    "resolving": "resolving mission",
    "pulling_image": "downloading container image",
    "downloading_bundle": "downloading mission bundle",
    "installing": "installing",
    "done": "done",
}


def watch_pull(client: RestClient, job_id: str) -> dict[str, Any]:
    """Poll a pull job to a terminal state, rendering honest progress on stderr.

    TTY: a live bar fed by the server's real byte counts (indeterminate spinner while
    totals are unknown — progress is never fabricated). Non-TTY: one line per phase
    change, no cursor movement. Returns the final job view (may still be 'pulling'
    when the cap expires — the job continues server-side)."""
    deadline = time.monotonic() + PULL_POLL_CAP_SECONDS

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
            task = progress.add_task(PHASE_LABELS["resolving"], total=None)
            while True:
                view = _poll()
                total = view.get("bytes_total") or 0
                progress.update(
                    task,
                    description=PHASE_LABELS.get(view.get("phase") or "", "working"),
                    total=total if total > 0 else None,
                    completed=view.get("bytes_current") or 0,
                )
                if view.get("status") != "pulling" or time.monotonic() >= deadline:
                    return view
                time.sleep(PULL_POLL_SECONDS)
    last_phase = None
    while True:
        view = _poll()
        if view.get("phase") != last_phase:
            last_phase = view.get("phase")
            err_console.print(f"{PHASE_LABELS.get(last_phase or '', 'working')}…", markup=False)
        if view.get("status") != "pulling" or time.monotonic() >= deadline:
            return view
        time.sleep(PULL_POLL_SECONDS)


def request_cancel(client: RestClient, job_id: str) -> None:
    """Best-effort server-side cancel of an in-flight pull job (Ctrl-C).

    A failed cancel POST (e.g. the server went away) must NOT mask the interrupt — the exit 130
    stands regardless — so RestClient's clean-exit (typer.Exit) is swallowed. A SECOND Ctrl-C
    landing inside this POST is swallowed too: the caller still falls through to `raise Exit(130)`,
    so an impatient double-interrupt keeps the documented interrupted exit code."""
    with contextlib.suppress(typer.Exit, KeyboardInterrupt):
        client.post(f"/missions/pull-jobs/{job_id}/cancel", json={})


def pull_to_terminal(client: RestClient, mission_id: str) -> dict[str, Any]:
    """Start a pull job for *mission_id* and watch it to a terminal view.

    Ctrl-C stops the pull server-side too (not just this client): request the cancel, then
    exit 130 (interrupted). The worker aborts the download and records the job 'cancelled'.
    Every other terminal state is returned for the caller to render."""
    started = client.post(f"/missions/{mission_id}/pull-jobs", json={})
    job_id = started["job_id"]
    try:
        return watch_pull(client, job_id)
    except KeyboardInterrupt:
        err_console.print("\ncancelling the pull…")
        request_cancel(client, job_id)
        raise typer.Exit(130) from None
