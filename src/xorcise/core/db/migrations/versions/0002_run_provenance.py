"""run/result artifact provenance (mission-versioning contract §31)

Revision ID: 0002_run_provenance
Revises: 0001_initial
Create Date: 2026-08-19

Two changes, one story. The int column called `mission_version` was the mission's monotonic
LOCAL install counter — renamed `install_revision`, because the versioning contract reserves
`mission_version` for the creator-owned SemVer string. Then the provenance the contract wants
copied immutably into run evidence at create time: the mission SemVer, the base SemVer it was
fused on, the bundle content hash, and exactly what this machine pulled and executed (platform,
index digest, per-platform digest). Results carry the labelling subset (SemVers + platform);
the digest chain lives on the run row they share a run_id with.

All new columns are nullable: pre-contract runs, and runs of your_own local fuses, have no
artifact identity — that is a fact about the artifact, not missing data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_run_provenance"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two batch blocks per table: SQLite batch mode rebuilds the table per block, and the
    # rename must complete before a NEW column may reuse the old name.
    with op.batch_alter_table("runs") as b:
        b.alter_column("mission_version", new_column_name="install_revision")
    with op.batch_alter_table("runs") as b:
        b.add_column(sa.Column("mission_version", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("mission_base_version", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
        b.add_column(sa.Column("platform", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("index_digest", sa.String(length=96), nullable=True))
        b.add_column(sa.Column("platform_digest", sa.String(length=96), nullable=True))

    with op.batch_alter_table("results") as b:
        b.alter_column("mission_version", new_column_name="install_revision")
    with op.batch_alter_table("results") as b:
        b.add_column(sa.Column("mission_version", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("mission_base_version", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("platform", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("results") as b:
        b.drop_column("platform")
        b.drop_column("mission_base_version")
        b.drop_column("mission_version")
    with op.batch_alter_table("results") as b:
        b.alter_column("install_revision", new_column_name="mission_version")

    with op.batch_alter_table("runs") as b:
        b.drop_column("platform_digest")
        b.drop_column("index_digest")
        b.drop_column("platform")
        b.drop_column("content_hash")
        b.drop_column("mission_base_version")
        b.drop_column("mission_version")
    with op.batch_alter_table("runs") as b:
        b.alter_column("install_revision", new_column_name="mission_version")
