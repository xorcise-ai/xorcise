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
        try:
            shutil.rmtree(run_dir(run_id))
        except OSError:
            # Its run row is already gone, so this is now an orphan the boot sweep removes.
            log.warning("could not remove the directory of deleted run %s", run_id, exc_info=True)


# The order is what keeps a partial failure harmless. A run row without its result is the one
# state that does damage: the boot regrade grades it again from whatever evidence is left. So the
# result goes only after the run row. A failure before the row leaves the run, result intact, to be
# deleted again; a failure after it leaves orphans, which the boot sweep removes.
_STORES = (
    _RunScopedStore(otel_store.held_run_ids, otel_store.delete_for_runs),
    _RunScopedStore(submissions.held_run_ids, submissions.delete_for_runs),
    _RunScopedStore(runs.held_run_ids, runs.delete_for_runs),
    _RunScopedStore(reporting.held_run_ids, reporting.delete_for_runs),
    _RunScopedStore(_run_dir_ids, _delete_run_dirs),
)


def purge_runs(run_ids: Collection[str]) -> None:
    """Delete every record and file of these runs, the result only after the run row."""
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
