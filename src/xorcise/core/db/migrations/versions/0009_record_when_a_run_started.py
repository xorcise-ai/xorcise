"""record when a run started, and what showed it, as the run moves to xorcising (#209)

Revision ID: 0009_record_when_a_run_started
Revises: 0008_unique_telemetry_seq
Create Date: 2026-10-23

Nothing ever moved a run out of `created`: it went straight to `terminal`, so every surface read
"Created" for the whole life of a working run, and a stop before the agent started could not be
told from one after (#187). A run now moves to `xorcising` the first time the agent does something
only the agent can do, and `runs.started_at` and `runs.start_trigger` record when, and which.

Existing rows are backfilled from the evidence they already hold, earliest first:
  telemetry    the run's first trace or log batch
  run_control  its first submission (flag, artifact or intel), or its fetch of the brief or an
               attachment
  tailnet_key  its node's confirmed join to the tailnet. The key fetch itself was never recorded,
               so the confirmed join, which follows it, stands in for it: a backfilled time can be
               a little late, never early.
A run that is not terminal and holds any of these moves to `xorcising` now; a terminal run keeps
`terminal` and gains the two columns. A run that holds none of them is left as it was.

Literals, not imports of contracts.run: a migration is a frozen record of the schema at its
revision.

The downgrade moves every `xorcising` run back to `created`, the only other state an unfinished
run had, and drops the two columns. A run that ended before its agent started (`cancelled` /
`never_started`, #187, same release) becomes `crashed` with a note saying so: earlier builds do not
know either trigger, and would grade it as a 0.00, where `crashed` is a trigger they never grade.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0009_record_when_a_run_started"
down_revision = "0008_unique_telemetry_seq"
branch_labels = None
depends_on = None

# Earliest evidence per run, one query per signal. The table and column names are the schema at
# this revision.
_SIGNALS: tuple[tuple[str, str], ...] = (
    ("telemetry", "SELECT run_id, MIN(created_at) FROM traces GROUP BY run_id"),
    ("telemetry", "SELECT run_id, MIN(created_at) FROM logs GROUP BY run_id"),
    ("run_control", "SELECT run_id, MIN(created_at) FROM run_submissions GROUP BY run_id"),
    (
        "run_control",
        "SELECT run_id, MIN(created_at) FROM run_observed_facts "
        "WHERE kind = 'runcontrol-lifecycle' AND name IN ('prompt', 'attachment') "
        "GROUP BY run_id",
    ),
    (
        "tailnet_key",
        "SELECT run_id, MIN(created_at) FROM run_observed_facts "
        "WHERE kind = 'network-lifecycle' AND name = 'join' AND value = 'confirmed' "
        "GROUP BY run_id",
    ),
)


def _as_datetime(value: object) -> datetime | None:
    # SQLite hands a MIN() over a DateTime column back as text.
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def upgrade() -> None:
    with op.batch_alter_table("runs") as b:
        b.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("start_trigger", sa.String(length=16), nullable=True))

    bind = op.get_bind()
    earliest: dict[str, tuple[datetime, str]] = {}
    for trigger, query in _SIGNALS:
        for run_id, raw in bind.execute(sa.text(query)):
            at = _as_datetime(raw)
            if at is None:
                continue
            seen = earliest.get(run_id)
            # Compare naive: SQLite stores these without an offset, and every one is UTC.
            if seen is None or at.replace(tzinfo=None) < seen[0].replace(tzinfo=None):
                earliest[run_id] = (at, trigger)
    runs = sa.table(
        "runs",
        sa.column("id", sa.String),
        sa.column("state", sa.String),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("start_trigger", sa.String),
    )
    for run_id, (at, trigger) in earliest.items():
        bind.execute(
            runs.update().where(runs.c.id == run_id).values(started_at=at, start_trigger=trigger)
        )
        bind.execute(
            runs.update()
            .where(runs.c.id == run_id, runs.c.state == "created")
            .values(state="xorcising")
        )


def downgrade() -> None:
    op.execute("UPDATE runs SET state = 'created' WHERE state = 'xorcising'")
    op.execute(
        "UPDATE runs SET terminal_trigger = 'crashed', terminal_detail = COALESCE("
        "terminal_detail, 'Ended before the agent started (' || terminal_trigger || '). "
        "Recorded as crashed by a schema downgrade, so this build does not grade it.') "
        "WHERE terminal_trigger IN ('cancelled', 'never_started')"
    )
    with op.batch_alter_table("runs") as b:
        b.drop_column("start_trigger")
        b.drop_column("started_at")
