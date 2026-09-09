"""runs.terminal_detail — why a run ended, when the trigger alone does not say

Revision ID: 0003_run_terminal_detail
Revises: 0002_run_provenance
Create Date: 2026-09-09

The readiness gate closes out a run whose environment died or never came up as `deploy_failed`
and releases the environment — and with it the only record of WHY: the outer container's logs
(what the inner daemon wait, the image load and `compose up` said) and the inner daemon's own.
Diagnosing a deploy failure meant disabling the gate and reproducing by hand. The gate now
captures that evidence BEFORE releasing anything and records it here, beside the trigger.

Nullable: the ordinary triggers (done, timeout, operator) carry no detail, and rows predate the
column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_terminal_detail"
down_revision = "0002_run_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as b:
        b.add_column(sa.Column("terminal_detail", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as b:
        b.drop_column("terminal_detail")
