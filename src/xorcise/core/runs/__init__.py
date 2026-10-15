"""xorcise.core.runs — run lifecycle/state machine.

LAYER: APPLICATION (domain module). A run is created for a registered agent and
tagged to it; runs do not outlive their agent. The full
lifecycle/state machine lives with run-control; this module persists create/list/get
and the delete cascade.
"""

from __future__ import annotations

from xorcise.core.runs.repository import (
    AwaitingRelease,
    ReleaseBacklog,
    ReleaseState,
    active_cidrs,
    active_run_networks,
    active_runs_to_reconcile,
    active_runs_with_deadline,
    active_sandbox_refs,
    authenticate,
    count_runs_for,
    create_run,
    delete_for_agent,
    delete_run,
    deployed_non_terminal_runs,
    finalize_run,
    get,
    get_join_key,
    get_prompt,
    has_environment,
    is_budget_expired,
    list_runs,
    mark_released,
    mark_terminal,
    record_release_failure,
    release_backlog,
    release_state,
    reserve_run,
    terminal_runs_awaiting_release,
    terminal_state,
)

__all__ = [
    "AwaitingRelease",
    "ReleaseBacklog",
    "ReleaseState",
    "active_cidrs",
    "active_run_networks",
    "active_runs_to_reconcile",
    "active_sandbox_refs",
    "deployed_non_terminal_runs",
    "has_environment",
    "active_runs_with_deadline",
    "authenticate",
    "count_runs_for",
    "create_run",
    "delete_for_agent",
    "delete_run",
    "finalize_run",
    "get",
    "get_join_key",
    "get_prompt",
    "is_budget_expired",
    "list_runs",
    "mark_released",
    "mark_terminal",
    "record_release_failure",
    "release_backlog",
    "release_state",
    "reserve_run",
    "terminal_runs_awaiting_release",
    "terminal_state",
]
