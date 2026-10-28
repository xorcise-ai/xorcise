"""make a telemetry batch's seq unique per run, and add the admission lock row (#147, #184)

Revision ID: 0008_unique_telemetry_seq
Revises: 0007_record_environment_release
Create Date: 2026-10-15

The OTLP receiver numbered each batch with `seq = len(read(run_id))`, a read and a write with
nothing holding them together and nothing making `(run_id, seq)` unique. Concurrent batches could
share a seq, and the evidence digest hashes in seq order. `traces` and `logs` now have a unique
`(run_id, seq)` index, which is also the index the receiver's `MAX(seq)` seeks.

`otel_admissions` holds one row per run. Admitting a batch and sealing the run both update it
first, which orders the two on any database: a batch commits before the seal, and is hashed, or
sees the seal and is refused. Rows are created as runs export, so nothing is backfilled.

A run that already holds a duplicate seq is renumbered, 0 to n-1 in the digest's `(seq, payload)`
order, and logged as a WARNING, because the unique index cannot be built over it. A server's ingest
ran on one event loop, which could not interleave two batches, so a normal install has none.

The digest covers each seq, so a SEALED run that is renumbered no longer matches its own digest,
and left alone its report would accuse untouched evidence of tampering. Its digest's scheme is
renamed instead, `xorcise-evidence-v1:` to `xorcise-evidence-v1-renumbered-0008:`, hex kept. No
build verifies that scheme, so the run reads as unverifiable, which is never an accusation, and
the record of why stays in the database rather than in a log line.

The unique index leads with run_id, so the old run_id-only indexes on both tables are dropped:
they only cost every insert a second index write.

The downgrade drops the table and the unique indexes and restores the run_id indexes. It does not
undo a renumbering or a renamed scheme: the original numbering was the defect.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0008_unique_telemetry_seq"
down_revision = "0007_record_environment_release"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

_TABLES = ("traces", "logs")


# Literals, not imports from evidence_seal: a migration is a frozen record of what it did.
_DIGEST_SCHEME = "xorcise-evidence-v1:"
_RENUMBERED_SCHEME = "xorcise-evidence-v1-renumbered-0008:"


def _renumber_duplicates(table_name: str) -> set[str]:
    """Renumber every run in `table_name` that holds a duplicate seq; return those runs."""
    bind = op.get_bind()
    table = sa.table(
        table_name,
        sa.column("id", sa.Integer),
        sa.column("run_id", sa.String),
        sa.column("seq", sa.Integer),
        sa.column("payload", sa.Text),
    )
    clashing = sorted(
        bind.execute(
            sa.select(table.c.run_id)
            .group_by(table.c.run_id, table.c.seq)
            .having(sa.func.count() > 1)
            .distinct()
        ).scalars()
    )
    for run_id in clashing:
        ids = bind.execute(
            sa.select(table.c.id)
            .where(table.c.run_id == run_id)
            .order_by(table.c.seq, table.c.payload, table.c.id)
        ).scalars()
        # Two passes, through negative numbers, so no step collides with a seq not yet moved.
        renumbered = list(enumerate(ids))
        for seq, row_id in renumbered:
            bind.execute(table.update().where(table.c.id == row_id).values(seq=-1 - seq))
        for seq, row_id in renumbered:
            bind.execute(table.update().where(table.c.id == row_id).values(seq=seq))
    return set(clashing)


def _mark_renumbered_seals(run_ids: set[str]) -> list[str]:
    """Rename the digest scheme of the sealed runs among `run_ids`; return the runs marked."""
    bind = op.get_bind()
    seals = sa.table("trace_seals", sa.column("run_id", sa.String), sa.column("evidence_digest"))
    marked = []
    for run_id, digest in bind.execute(
        sa.select(seals.c.run_id, seals.c.evidence_digest).where(seals.c.run_id.in_(run_ids))
    ).all():
        # Only a v1 digest is renamed. None, or the unavailable sentinel, already verifies as
        # nothing, so renumbering cannot turn it into an accusation.
        if digest and digest.startswith(_DIGEST_SCHEME):
            renamed = _RENUMBERED_SCHEME + digest.removeprefix(_DIGEST_SCHEME)
            bind.execute(
                seals.update().where(seals.c.run_id == run_id).values(evidence_digest=renamed)
            )
            marked.append(run_id)
    return sorted(marked)


def upgrade() -> None:
    renumbered: set[str] = set()
    for table_name in _TABLES:
        renumbered |= _renumber_duplicates(table_name)
        # An index, not a constraint: SQLite adds one without rebuilding the table, and these
        # are the largest tables in the database.
        op.create_index(f"uq_{table_name}_run_seq", table_name, ["run_id", "seq"], unique=True)
        op.drop_index(f"ix_{table_name}_run_id", table_name=table_name)
    if renumbered:
        marked = _mark_renumbered_seals(renumbered)
        log.warning(
            "renumbered the telemetry seq of %d run(s) that held duplicates, so it can be made "
            "unique: %s. Sealed runs among them now report their seal as unverifiable rather "
            "than mismatched (scheme %s): %s. No payload was changed.",
            len(renumbered),
            ", ".join(sorted(renumbered)),
            _RENUMBERED_SCHEME.rstrip(":"),
            ", ".join(marked) or "none",
        )
    op.create_table(
        "otel_admissions",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("batches", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("otel_admissions")
    for table_name in _TABLES:
        op.create_index(f"ix_{table_name}_run_id", table_name, ["run_id"])
        op.drop_index(f"uq_{table_name}_run_seq", table_name=table_name)
