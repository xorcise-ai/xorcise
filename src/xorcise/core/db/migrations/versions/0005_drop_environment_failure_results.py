"""drop the phantom results recorded for runs whose environment failed

Revision ID: 0005_drop_environment_failure_results
Revises: 0004_trace_seal_evidence_digest
Create Date: 2026-10-08

A run that ended `deploy_failed` (the readiness gate) or `crashed` (the boot reconcile) never gave
the agent a fair attempt, yet both were graded: the gate's close-out went straight through
grade_and_record, and the boot sweep for lost grades re-graded every crashed run moments after
reconcile had aborted it without one. Each recorded a 0.00 that the leaderboard and the GUI
averaged in as though the agent had tried and failed (#109). Such runs are no longer graded; this
removes the results already recorded for them, so the aggregates on an existing install are right
from the first boot of this build rather than only for runs made after it.

Only the result row goes. The run itself, its trigger and terminal_detail (why it ended), its
sealed telemetry and its events all stay — the run still lists, it just carries no score.

Not reversible: the deleted rows are scores of nothing, so there is nothing worth restoring, and a
downgraded build would simply regrade such a run on its next boot sweep anyway.
"""

from __future__ import annotations

from alembic import op

revision = "0005_drop_environment_failure_results"
down_revision = "0004_trace_seal_evidence_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Literals, not an import of contracts.run.UNGRADED_TRIGGERS: a migration is a frozen record of
    # what it did on the day it shipped, and must not change meaning if that set ever grows.
    #
    # Every column is table-qualified, and that is load-bearing: the runs key is `id`, not
    # `run_id`, and an unqualified `run_id` in the subquery silently resolves to the OUTER
    # results.run_id — a correlated subquery that is true for every row, deleting every result.
    op.execute(
        "DELETE FROM results WHERE results.run_id IN ("
        "SELECT runs.id FROM runs WHERE runs.terminal_trigger IN ('deploy_failed', 'crashed'))"
    )


def downgrade() -> None:
    pass  # see the module docstring — nothing to restore
