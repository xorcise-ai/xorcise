"""Delete everything recorded for a run, and sweep up what earlier deletes left behind (#117).

A run's records live in modules that may not import each other: the otel plane's telemetry, the
run-control submissions, the result, the runs module's own rows, and the run directory under
`<home>/runs`. Each owner lists and deletes its own. This delivery-layer module, which may import
them all, holds the one ordered list of them that both the delete and the boot sweep walk.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable, Collection
from dataclasses import dataclass

from xorcise.core import reporting, runs
from xorcise.core.otel import store as otel_store
from xorcise.core.rest.events_export import run_dir, runs_root
from xorcise.core.runcontrol import store as submissions

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RunScopedStore:
    run_ids: Callable[[], set[str]]
    delete: Callable[[Collection[str]], None]


def _run_dir_ids() -> set[str]:
    root = runs_root()
    if not root.is_dir():
        return set()
    return {entry.name for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink()}


def _delete_run_dirs(run_ids: Collection[str]) -> None:
    # Only a name already listed under runs/ is removed. Run ids also come from rows an OTLP
    # export names, so joining one onto a path unchecked could reach outside runs/.
    for run_id in _run_dir_ids().intersection(run_ids):
        shutil.rmtree(run_dir(run_id))


# The runs rows go last: a purge that fails partway leaves the run row in place, so the run still
# shows and deleting it again finishes the job.
_STORES = (
    _RunScopedStore(otel_store.held_run_ids, otel_store.delete_for_runs),
    _RunScopedStore(submissions.held_run_ids, submissions.delete_for_runs),
    _RunScopedStore(reporting.held_run_ids, reporting.delete_for_runs),
    _RunScopedStore(_run_dir_ids, _delete_run_dirs),
    _RunScopedStore(runs.held_run_ids, runs.delete_for_runs),
)


def purge_runs(run_ids: Collection[str]) -> None:
    """Delete every record and file of these runs, their run rows last."""
    if not run_ids:
        return
    for store in _STORES:
        store.delete(run_ids)


def sweep_orphaned_runs() -> None:
    """Purge every run whose records outlived its run row: a delete from before #117, telemetry
    exported for a run id that never existed, a grade that finished after its run was deleted."""
    # Read the held ids before the live ones, so a run created in between counts as live.
    held = {run_id for store in _STORES for run_id in store.run_ids()}
    orphans = held - {run.run_id for run in runs.list_runs()}
    purge_runs(orphans)
    if orphans:
        log.info("reconcile: cleared %d orphaned run(s)", len(orphans))
