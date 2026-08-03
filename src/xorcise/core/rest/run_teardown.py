"""Release a terminal run's environment (rest layer).

Completing/aborting a run must stop its mission container AND remove its tailnet nodes, or the
tailnet accumulates online subnet routers that later runs collide with. Reached from
run_terminate.grade_and_record — the single point every terminal path (endpoint background tasks
AND the budget watchdog) passes through. Both underlying teardowns are idempotent; this is
best-effort — a teardown error must never break grading, so failures are logged and swallowed."""

from __future__ import annotations

import logging

from xorcise.core.config import get_settings
from xorcise.core.rest.run_create import build_run_create_deps

log = logging.getLogger(__name__)


def teardown_run(run_id: str) -> None:
    """Stop the run's container + remove its tailnet nodes. Idempotent, best-effort."""
    deps = build_run_create_deps(get_settings())
    try:
        deps.control.teardown(run_id, credential=deps.api_key)
    except Exception:  # best-effort — never break grading on a teardown failure
        log.warning("teardown: control.teardown failed for %s", run_id, exc_info=True)
    try:
        deps.fence.teardown_run_network(run_id)
    except Exception:
        log.warning("teardown: fence.teardown_run_network failed for %s", run_id, exc_info=True)
