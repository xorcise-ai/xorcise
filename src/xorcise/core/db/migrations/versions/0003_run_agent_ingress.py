"""run.agent_ingress — whether the mission expects the target to call back to the agent

Its own revision rather than a column folded into 0001: every existing install is already
stamped at 0001, so alembic would never re-run it and the column would silently never appear.

Revision ID: 0002_run_agent_ingress
Revises: 0002_run_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_run_agent_ingress"
down_revision = "0002_run_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("agent_ingress", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("runs", "agent_ingress")
