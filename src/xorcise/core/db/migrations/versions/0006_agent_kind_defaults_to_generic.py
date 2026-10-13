"""every agent has a kind: a missing or blank agents.kind becomes 'generic'

Revision ID: 0006_agent_kind_defaults_to_generic
Revises: 0005_drop_environment_failure_results
Create Date: 2026-10-13

An agent registered without choosing a harness was stored with kind NULL, or with whatever blank
string the client sent, and the register page showed that as a choice nobody had made (#141). Its
runs never saw the NULL: run creation reads `agent.kind or "generic"`, and runs.source_agent is
NOT NULL DEFAULT 'generic'. So `generic` is what that NULL always meant, and this writes it down.
NULL and blank kinds become 'generic', and the column becomes NOT NULL with that as its server
default. A real kind, including one no harness answers to, is left exactly as stored.

Runs are untouched: their source_agent was fixed when they were created.

The downgrade makes the column nullable again but does not restore the NULLs. Which rows held one
is not recorded, and an older build creates runs for a 'generic' agent exactly as it did for a
NULL one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_agent_kind_defaults_to_generic"
down_revision = "0005_drop_environment_failure_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The literal, not contracts.agent.GENERIC_KIND: a migration is a frozen record of what it did
    # on the day it shipped.
    agents = sa.table("agents", sa.column("id", sa.String), sa.column("kind", sa.String))
    bind = op.get_bind()
    # Blank is judged in Python, not with SQL TRIM: TRIM drops spaces only, while the rule new
    # writes follow (contracts.agent.normalise_kind) drops any whitespace.
    blank = [
        row.id
        for row in bind.execute(sa.select(agents.c.id, agents.c.kind))
        if not (row.kind or "").strip()
    ]
    if blank:
        bind.execute(agents.update().where(agents.c.id.in_(blank)).values(kind="generic"))
    # SQLite cannot alter a column in place, so batch mode rebuilds the table. That is safe here:
    # the migration connection does not enforce foreign keys, and runs' reference to `agents`
    # is by name, so it points at the rebuilt table.
    with op.batch_alter_table("agents") as b:
        b.alter_column(
            "kind",
            existing_type=sa.String(length=255),
            nullable=False,
            server_default="generic",
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as b:
        b.alter_column(
            "kind",
            existing_type=sa.String(length=255),
            nullable=True,
            server_default=None,
        )
