"""record when a run's environment was released, so a failed teardown is retried (#173)

Revision ID: 0007_record_environment_release
Revises: 0006_agent_kind_defaults_to_generic
Create Date: 2026-10-14

A run's teardown (its container and its tailnet nodes) was best-effort and never retried: a down
Headscale or Docker, or one failed call, leaked the environment, and nothing revisited a terminal
run. runs.released_at is now set only once both halves are gone, release_attempts counts the
failures, and release_error keeps the last one for the run page.

Existing rows are deliberately NOT backfilled. Every terminal run that predates this column starts
out unreleased, so the release sweeper revisits each one once. A run that was released properly
costs one idempotent no-op teardown, and one whose tailnet user or router node leaked under an
earlier build is finally cleaned up: nothing else ever would be, because the boot orphan sweep only
covers Docker. The sweeper takes a bounded batch per tick, so a large history does not stall it.

The downgrade drops the three columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_record_environment_release"
down_revision = "0006_agent_kind_defaults_to_generic"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as b:
        b.add_column(sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(
            sa.Column("release_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        b.add_column(sa.Column("release_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as b:
        b.drop_column("release_error")
        b.drop_column("release_attempts")
        b.drop_column("released_at")
