"""trace_seals.evidence_digest — make the seal tamper-evident, not just a timestamp

Revision ID: 0004_trace_seal_evidence_digest
Revises: 0003_run_terminal_detail
Create Date: 2026-09-16

Sealing recorded only `sealed_at`: when the evidence stopped growing, and nothing about whether
the bytes still say what they said. A span edited after the seal read back clean, and a regrade
could not show it had graded the same evidence as the first pass.

This column holds a digest over everything the run is graded from, taken at the moment of sealing.

Nullable, and deliberately so: runs sealed before this column existed have no digest, and their
verification answers "unknown" rather than "tampered" — reporting an old run as altered because we
never recorded a digest would be the loudest possible false accusation.

The downgrade DESTROYS every recorded digest, and re-upgrading brings the column back empty:
`attach_digest` is first-wins and nothing backfills, so a down/up cycle permanently drops every
existing run to "unknown". That is by design — the alternative is re-hashing evidence on a schema
migration, and a digest re-taken later attests to whatever the bytes say then, which is exactly the
question the seal exists to answer. Downgrade only if you are willing to lose the seals.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_trace_seal_evidence_digest"
down_revision = "0003_run_terminal_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trace_seals") as b:
        b.add_column(sa.Column("evidence_digest", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trace_seals") as b:
        b.drop_column("evidence_digest")
