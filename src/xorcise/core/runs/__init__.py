"""xorcise.core.runs — run lifecycle/state machine.

LAYER: APPLICATION (domain module). A run is created for a registered agent and
tagged to it; runs do not outlive their agent. The full
lifecycle/state machine lives with run-control; this module persists create/list/get
and the delete cascade.
"""

from __future__ import annotations

from xorcise.core.runs.repository import (
    active_cidrs,
    active_run_networks,
    active_runs_to_reconcile,
    active_runs_with_deadline,
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
    mark_terminal,
    reserve_run,
    terminal_state,
)

__all__ = [
    "active_cidrs",
    "active_run_networks",
    "active_runs_to_reconcile",
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
    "mark_terminal",
    "reserve_run",
    "terminal_state",
]
