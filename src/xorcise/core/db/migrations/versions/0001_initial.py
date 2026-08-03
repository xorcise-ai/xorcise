"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-30

The complete storage schema for the first public release: agents, runs and their
results; the RAW telemetry stores (traces, logs) with the seal marker; the derived
agent-event projection cache; run submissions and observed facts; the terrain
attribution/update caches; and the background job tables (ingest_jobs, pull_jobs).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- agents: the registered agent catalogue -----------------------------------
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=True),
        sa.Column("otel", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kind", sa.String(length=255), nullable=True),
        # Per-agent launch overrides. Null means inherit the selected harness launch
        # provider; JSON [] is intentionally distinct — it suppresses the provider's
        # tips or mission preamble.
        sa.Column("launch_command_template", sa.Text(), nullable=True),
        sa.Column("launch_tips", sa.JSON(), nullable=True),
        sa.Column("mission_preamble", sa.JSON(), nullable=True),
        # Null inherits the harness provider. 'host' selects loopback run-control and
        # OTLP addresses; 'container' selects addresses reachable from a container.
        sa.Column("launch_mode", sa.String(length=16), nullable=True),
        sa.UniqueConstraint("name", name="uq_agents_name"),
    )
    op.create_index("ix_agents_name", "agents", ["name"])

    # --- runs: one row per evaluation run ------------------------------------------
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("mission", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("budget_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column(
            "run_control_key",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("('')"),
        ),
        sa.Column("terminal_trigger", sa.String(length=16), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("sandbox_ref", sa.String(length=255), nullable=True),
        sa.Column("agent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mission_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "join_key", sa.String(length=255), nullable=False, server_default=sa.text("('')")
        ),
        sa.Column(
            "network_cidr", sa.String(length=64), nullable=False, server_default=sa.text("('')")
        ),
        sa.Column("entry_cidrs", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("source_agent", sa.String(length=255), nullable=False, server_default="generic"),
        # Operator-facing run label; the repository maps a NULL name to the mission.
        sa.Column("name", sa.String(length=255), nullable=True),
        # Per-run intel disclosure policy: 'all' / '' => everything authored may be
        # disclosed, 'none' => nothing, 'i1,i3' => only those ids. The disclosure gate
        # and prompt read this column; grading never does.
        sa.Column("intel_policy", sa.String(length=255), nullable=False, server_default="all"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_runs_agent_id"),
    )
    op.create_index("ix_runs_agent_id", "runs", ["agent_id"])
    # Two non-terminal runs can never share a subnet — enforced at the storage layer
    # so a concurrent (even cross-process) reservation collides atomically
    # (IntegrityError) instead of silently dual-assigning. Terminal runs and empty
    # cidrs are exempt so subnets free up on teardown.
    _cidr_where = "state != 'terminal' AND network_cidr != ''"
    op.create_index(
        "ix_runs_network_cidr_active",
        "runs",
        ["network_cidr"],
        unique=True,
        sqlite_where=sa.text(_cidr_where),
        postgresql_where=sa.text(_cidr_where),
    )

    # --- results: the graded outcome of a run --------------------------------------
    op.create_table(
        "results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("overall", sa.Float(), nullable=False),
        sa.Column("deterministic", sa.Float(), nullable=False),
        sa.Column("judge", sa.Float(), nullable=False),
        sa.Column("trace_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("judge_model", sa.String(length=255), nullable=True),
        sa.Column("budget_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sandbox_ref", sa.String(length=255), nullable=True),
        sa.Column("agent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mission_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("partial_trigger", sa.String(length=16), nullable=True),
        sa.Column("stats_json", sa.Text(), nullable=False, server_default=sa.text("('')")),
    )
    op.create_index("ix_results_run_id", "results", ["run_id"])
    op.create_index("ix_results_agent_id", "results", ["agent_id"])

    # --- traces / logs: RAW OTLP signal stores, sealed at run end -------------------
    op.create_table(
        "traces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_traces_run_id", "traces", ["run_id"])

    op.create_table(
        "trace_seals",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- run_submissions: artifacts the agent submitted during the run -------------
    op.create_table(
        "run_submissions",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- run_observed_facts: facts observed about the run environment --------------
    op.create_table(
        "run_observed_facts",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- ingest_jobs: background mission-ingest jobs, durable across restarts ------
    op.create_table(
        "ingest_jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="building"),
        sa.Column("logs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("slug", sa.String(length=255), nullable=True),
        sa.Column("image", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
    )

    # --- agent_event_runs + agent_events: derived projection cache -----------------
    # Rebuildable from the RAW stores — never canonical. The header row carries the
    # staleness triple + run-level view metadata; events are one row per normalized
    # AgentEvent, ordered by `ord` within a run, with the small variable `data` map
    # kept as JSON in `data_json`.
    op.create_table(
        "agent_event_runs",
        sa.Column("run_id", sa.String(length=36), primary_key=True),
        sa.Column("adapter_name", sa.String(length=255), nullable=False),
        sa.Column("adapter_version", sa.String(length=255), nullable=False),
        sa.Column("source_max_seq", sa.Integer(), nullable=False),
        sa.Column("source_agent", sa.String(length=255), nullable=False),
        sa.Column("fallback", sa.Boolean(), nullable=False),
        sa.Column("next_since", sa.Integer(), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("log_max_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_log_since", sa.Integer(), nullable=False, server_default="-1"),
    )

    op.create_table(
        "agent_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=512), nullable=False),
        sa.Column("ts", sa.String(length=64), nullable=False),
        sa.Column("source_agent", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subkind", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("group_id", sa.String(length=512), nullable=True),
        sa.Column("parent_id", sa.String(length=512), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=255), nullable=True),
        sa.Column("conversation_id", sa.String(length=255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("raw_seq", sa.Integer(), nullable=False),
        sa.Column("span_id", sa.String(length=255), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("signal", sa.String(length=8), nullable=False, server_default="trace"),
        sa.Column("received_at", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_agent_events_run_ord", "agent_events", ["run_id", "ord"])
    op.create_index("ix_agent_events_run_seq", "agent_events", ["run_id", "raw_seq"])
    op.create_index("ix_agent_events_kind", "agent_events", ["kind"])
    op.create_index(
        "ix_agent_events_run_signal_seq",
        "agent_events",
        ["run_id", "signal", "raw_seq"],
    )

    # --- logs: RAW OTLP logs signal, peer of `traces` -------------------------------
    op.create_table(
        "logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_logs_run_id", "logs", ["run_id"])

    # --- terrain_actions: BYOM attribution cache ------------------------------------
    op.create_table(
        "terrain_actions",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("action_seq", sa.Integer(), nullable=False),
        sa.Column("src_node_id", sa.String(length=128), nullable=False),
        sa.Column("dst_node_id", sa.String(length=128), nullable=False),
        sa.Column("edge_kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("sets_state", sa.String(length=16), nullable=True),
        sa.Column("applicable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "event_id", name="uq_terrain_actions_run_event"),
    )

    # --- terrain_updates: terrain update store --------------------------------------
    op.create_table(
        "terrain_updates",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("event_id", sa.String(length=255), nullable=True),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("new_state", sa.String(length=16), nullable=True),
        sa.Column("discovered", sa.Boolean(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.UniqueConstraint(
            "run_id",
            "event_id",
            "target_kind",
            "target_id",
            name="uq_terrain_updates_run_event_target",
        ),
    )

    # --- pull_jobs: background mission-pull jobs with byte progress -----------------
    op.create_table(
        "pull_jobs",
        sa.Column("job_id", sa.String(length=36), primary_key=True),
        sa.Column("mission_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pulling"),
        sa.Column("phase", sa.String(length=32), nullable=False, server_default="resolving"),
        sa.Column("bytes_current", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("image", sa.String(length=255), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # The dedup + reload-resume lookups filter on mission_id (active_for).
    op.create_index("ix_pull_jobs_mission_id", "pull_jobs", ["mission_id"])


def downgrade() -> None:
    # Reverse creation order; `runs` goes before `agents` (fk_runs_agent_id).
    op.drop_table("pull_jobs")
    op.drop_table("terrain_updates")
    op.drop_table("terrain_actions")
    op.drop_table("logs")
    op.drop_table("agent_events")
    op.drop_table("agent_event_runs")
    op.drop_table("ingest_jobs")
    op.drop_table("run_observed_facts")
    op.drop_table("run_submissions")
    op.drop_table("trace_seals")
    op.drop_table("traces")
    op.drop_table("results")
    op.drop_table("runs")
    op.drop_table("agents")
