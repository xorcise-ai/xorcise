"""Run persistence (domain module). A run is tagged to its agent_id."""

from __future__ import annotations

import hmac
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select

from xorcise.core.contracts.run import RunEntry, TerminalTrigger, as_terminal_trigger
from xorcise.core.db import session_scope
from xorcise.core.runs.models import RunObservedFactRow, RunRow, TerrainUpdateRow


def _utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to UTC-aware, patching the SQLite naive round-trip."""
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _utc_required(dt: datetime) -> datetime:
    """Normalize a non-nullable datetime to UTC-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _to_entry(row: RunRow) -> RunEntry:
    return RunEntry(
        run_id=row.id,
        agent_id=row.agent_id,
        mission=row.mission,
        # Legacy rows predate the name column — fall back to the mission so a run always has a
        # readable label.
        name=row.name or row.mission,
        state=row.state,
        created_at=_utc_required(row.created_at),
        budget_seconds=row.budget_seconds,
        terminal_trigger=as_terminal_trigger(row.terminal_trigger),
        terminal_detail=row.terminal_detail,
        completed_at=_utc(row.completed_at),
        model=row.model,
        sandbox_ref=row.sandbox_ref,
        agent_version=row.agent_version,
        install_revision=row.install_revision,
        mission_version=row.mission_version,
        mission_base_version=row.mission_base_version,
        content_hash=row.content_hash,
        platform=row.platform,
        index_digest=row.index_digest,
        platform_digest=row.platform_digest,
        source_agent=row.source_agent,
        intel_policy=row.intel_policy,
    )


def create_run(
    agent_id: str,
    mission: str,
    *,
    run_id: str | None = None,
    name: str | None = None,
    budget_seconds: int = 0,
    prompt: str = "",
    run_control_key: str = "",
    join_key: str = "",
    network_cidr: str = "",
    entry_cidrs: str = "",
    model: str | None = None,
    sandbox_ref: str | None = None,
    agent_version: int = 1,
    install_revision: int = 1,
    mission_version: str | None = None,
    mission_base_version: str | None = None,
    content_hash: str | None = None,
    platform: str | None = None,
    index_digest: str | None = None,
    platform_digest: str | None = None,
    source_agent: str = "generic",
    intel_policy: str = "all",
) -> RunEntry:
    """Persist a new run tagged to agent_id, in state 'created'."""
    with session_scope() as s:
        row = RunRow(
            id=run_id or uuid4().hex,
            agent_id=agent_id,
            mission=mission,
            name=name or None,
            budget_seconds=budget_seconds,
            prompt=prompt,
            run_control_key=run_control_key,
            join_key=join_key,
            network_cidr=network_cidr,
            entry_cidrs=entry_cidrs,
            model=model,
            sandbox_ref=sandbox_ref,
            agent_version=agent_version,
            install_revision=install_revision,
            mission_version=mission_version,
            mission_base_version=mission_base_version,
            content_hash=content_hash,
            platform=platform,
            index_digest=index_digest,
            platform_digest=platform_digest,
            source_agent=source_agent,
            intel_policy=intel_policy,
        )
        s.add(row)
        s.flush()
        return _to_entry(row)


def reserve_run(
    run_id: str,
    agent_id: str,
    mission: str,
    *,
    network_cidr: str,
    entry_cidrs: str = "",
    name: str | None = None,
    intel_policy: str = "all",
    sandbox_ref: str | None = None,
) -> RunEntry:
    """Insert a minimal 'created' run row with its allocated subnet, BEFORE deploy.

    Makes the subnet reservation durable + visible to every process the instant it is taken (via
    active_cidrs), closing the pre-persist in-flight window that _RESERVED_CIDRS could not span
    across processes/crashes. The partial unique index on network_cidr (non-terminal runs) makes a
    concurrent same-subnet reservation raise IntegrityError — the caller retries the next free
    subnet. finalize_run fills the row once deploy succeeds; delete_run releases it on failure."""
    with session_scope() as s:
        row = RunRow(
            id=run_id,
            agent_id=agent_id,
            mission=mission,
            name=name or None,
            network_cidr=network_cidr,
            entry_cidrs=entry_cidrs,
            intel_policy=intel_policy,
            sandbox_ref=sandbox_ref,
        )
        s.add(row)
        s.flush()  # surfaces the unique-index collision here (rolled back by session_scope)
        return _to_entry(row)


def finalize_run(
    run_id: str,
    *,
    budget_seconds: int = 0,
    prompt: str = "",
    run_control_key: str = "",
    join_key: str = "",
    model: str | None = None,
    sandbox_ref: str | None = None,
    agent_version: int = 1,
    install_revision: int = 1,
    mission_version: str | None = None,
    mission_base_version: str | None = None,
    content_hash: str | None = None,
    platform: str | None = None,
    index_digest: str | None = None,
    platform_digest: str | None = None,
    source_agent: str = "generic",
    intel_policy: str = "all",
) -> RunEntry:
    """Fill in a reserved run row with the fields known only after deploy. Keeps state
    'created'; network_cidr/entry_cidrs were set at reserve_run."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None:
            raise LookupError(f"finalize_run: no reserved run {run_id!r}")
        row.budget_seconds = budget_seconds
        row.prompt = prompt
        row.run_control_key = run_control_key
        row.join_key = join_key
        row.model = model
        row.sandbox_ref = sandbox_ref
        row.agent_version = agent_version
        row.install_revision = install_revision
        row.mission_version = mission_version
        row.mission_base_version = mission_base_version
        row.content_hash = content_hash
        row.platform = platform
        row.index_digest = index_digest
        row.platform_digest = platform_digest
        row.source_agent = source_agent
        row.intel_policy = intel_policy
        s.flush()
        return _to_entry(row)


def delete_run(run_id: str) -> None:
    """Delete one run row — releases a reservation whose create failed pre-finalize."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is not None:
            s.delete(row)


def get_prompt(run_id: str) -> str | None:
    """The stored connect prompt for a run, or None if the run is absent."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        return row.prompt if row is not None else None


def list_runs() -> list[RunEntry]:
    """All runs, NEWEST first — so every consumer of GET /runs (the Run History page, the
    dashboard's Recent-runs panel, the CLI `run list`) leads with the most recent run without
    re-sorting. Surfaces that need chronological order (the score trend) sort locally."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).order_by(RunRow.created_at.desc())).all()
        return [_to_entry(r) for r in rows]


def count_runs_for(agent_id: str, mission: str) -> int:
    """How many runs already exist for this agent + mission — the counter behind the lazy default
    run name (`<mission> · <agent> #<n>`), where n is this count + 1."""
    with session_scope() as s:
        rows = s.scalars(
            select(RunRow.id).where(RunRow.agent_id == agent_id, RunRow.mission == mission)
        ).all()
        return len(rows)


def active_cidrs() -> set[str]:
    """The /24 of every non-terminal run — the subnets currently in use on the tailnet.

    Subnet allocation excludes these so a new run never reuses a running run's
    subnet. Terminal runs are excluded: their subnet is freed on teardown."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).where(RunRow.state != "terminal")).all()
        return {r.network_cidr for r in rows if r.network_cidr}


def active_sandbox_refs() -> set[str]:
    """The mission image of every non-terminal run: the images that must not be removed.

    Run create records the image on the reservation, before deploy, so the image is protected
    from the moment the run is reserved, not only once finalize_run runs."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).where(RunRow.state != "terminal")).all()
        return {r.sandbox_ref for r in rows if r.sandbox_ref}


def active_run_networks() -> list[tuple[str, tuple[str, ...]]]:
    """(run_id, entry_cidrs) for every non-terminal run with non-empty entry_cidrs.

    The authoritative source for rendering the per-run ACL: the rest layer maps these to the
    fence's RunNetwork set so every process renders the same complete policy from the shared DB,
    instead of each clobbering the others from its own in-process view."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).where(RunRow.state != "terminal")).all()
        return [(r.id, tuple(r.entry_cidrs.split(","))) for r in rows if r.entry_cidrs]


def active_runs_to_reconcile() -> list[tuple[str, bool]]:
    """(run_id, was_deployed) for every non-terminal run, for boot reconcile.

    was_deployed is True once finalize_run has run (prompt set) — the run was deployed — and False
    for a row that is still only a reservation (reserve_run, prompt empty): the server crashed
    between reserving the subnet and finalizing the deploy. The reconciler adopts/aborts the former
    and releases the latter. Terminal runs are excluded (already closed out)."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).where(RunRow.state != "terminal")).all()
        return [(r.id, bool(r.prompt)) for r in rows]


def deployed_non_terminal_runs() -> list[tuple[str, datetime]]:
    """(run_id, created_at) for every non-terminal run that actually has an ENVIRONMENT.

    Feeds the readiness gate: it needs `created_at` to know whether a run is still inside its
    startup window. Two exclusions, both load-bearing:
      * reservation-only rows (deploy never finished, prompt empty) — the boot reconciler's
        business, not readiness;
      * STATIC (attachment-only) runs — they have no subnet, no fence and no container by design,
        so they hold an empty network_cidr. Polling them would find no container, read that as a
        dead environment, and terminate a perfectly healthy run once its window expired."""
    with session_scope() as s:
        rows = s.scalars(select(RunRow).where(RunRow.state != "terminal")).all()
        return [(r.id, _utc_required(r.created_at)) for r in rows if r.prompt and r.network_cidr]


def has_environment(run_id: str) -> bool:
    """True iff this run owns a deployed ENVIRONMENT (subnet + fence + container).

    False for a STATIC (attachment-only) run, which has none by design and so holds an empty
    network_cidr — the discriminator callers need to avoid reporting "no container" as a fault."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        return bool(row is not None and row.network_cidr)


def get(run_id: str) -> RunEntry | None:
    """Resolve a run by id, or None if absent."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        return _to_entry(row) if row is not None else None


def held_run_ids() -> set[str]:
    """Every run id that has a run row, an observed fact or a terrain update."""
    with session_scope() as s:
        return (
            set(s.scalars(select(RunRow.id)))
            | set(s.scalars(select(RunObservedFactRow.run_id).distinct()))
            | set(s.scalars(select(TerrainUpdateRow.run_id).distinct()))
        )


def delete_for_runs(run_ids: Collection[str]) -> None:
    """Delete these runs' observed facts and terrain updates, then their rows, in one
    transaction (the run-delete cascade)."""
    with session_scope() as s:
        s.execute(delete(RunObservedFactRow).where(RunObservedFactRow.run_id.in_(run_ids)))
        s.execute(delete(TerrainUpdateRow).where(TerrainUpdateRow.run_id.in_(run_ids)))
        s.execute(delete(RunRow).where(RunRow.id.in_(run_ids)))


def authenticate(run_id: str, key: str) -> bool:
    """True iff `key` is the non-empty per-run run-control bearer for `run_id`.

    Constant-time compare; an absent run or an unset/empty stored key never authenticates."""
    if not key:
        return False
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None or not row.run_control_key:
            return False
        return hmac.compare_digest(row.run_control_key, key)


def get_join_key(run_id: str) -> str | None:
    """The per-run tailnet join key (secret), or None if the run is absent.

    Narrow getter so the secret never rides the general RunEntry list/get surface — mirrors
    how `authenticate` reads run_control_key without exposing it."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        return row.join_key if row is not None else None


def mark_terminal(
    run_id: str, trigger: TerminalTrigger, at: datetime, detail: str | None = None
) -> str:
    """Transition a run to terminal, first-wins + idempotent. Returns the recorded trigger.

    The first call stamps state='terminal', terminal_trigger, completed_at and (when given) the
    terminal_detail — why the run ended, for triggers whose name alone does not say. Later calls
    (any trigger, any detail) are no-ops and return the trigger that already stuck.
    Returns the recorded trigger, or '' if the run does not exist."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None:
            return ""  # absent run: explicit empty-sentinel; nothing was recorded
        if row.state == "terminal":
            return row.terminal_trigger if row.terminal_trigger is not None else trigger
        row.state = "terminal"
        row.terminal_trigger = trigger
        row.completed_at = at
        row.terminal_detail = detail or None
        return trigger


def terminal_state(run_id: str) -> tuple[bool, str | None, datetime | None]:
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None or row.state != "terminal":
            return (False, None, None)
        return (True, row.terminal_trigger, _utc(row.completed_at))


@dataclass(frozen=True)
class ReleaseState:
    """Where a run's environment release stands (#173). `released_at` None means not yet."""

    released_at: datetime | None
    attempts: int
    error: str | None


# A traceback-free error line is what the run page shows; a runaway message must not bloat the row.
_RELEASE_ERROR_MAX = 500


def release_state(run_id: str) -> ReleaseState | None:
    """The run's release state, or None if the run does not exist."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None:
            return None
        return ReleaseState(_utc(row.released_at), row.release_attempts, row.release_error)


def mark_released(run_id: str, at: datetime) -> None:
    """Record that the run's container and tailnet nodes are gone. First-wins and idempotent: a
    second release (the sweeper racing the grade's own teardown) keeps the first timestamp."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None or row.released_at is not None:
            return
        row.released_at = at
        row.release_error = None


def record_release_failure(run_id: str, error: str) -> int:
    """Count a failed release attempt and keep its error. Returns the attempts so far (0 when the
    run is gone or was released meanwhile, so the caller does not report a stale failure)."""
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None or row.released_at is not None:
            return 0
        row.release_attempts = (row.release_attempts or 0) + 1
        row.release_error = error[:_RELEASE_ERROR_MAX]
        return row.release_attempts


@dataclass(frozen=True)
class AwaitingRelease:
    """A terminal run whose environment is not released yet (#173)."""

    run_id: str
    trigger: str | None
    completed_at: datetime | None
    attempts: int
    error: str | None


def terminal_runs_awaiting_release() -> list[AwaitingRelease]:
    """Every terminal run whose environment is not released yet, oldest first. STATIC runs are
    excluded: they own no container and no tailnet nodes (empty network_cidr), so there is nothing
    to release."""
    with session_scope() as s:
        rows = s.scalars(
            select(RunRow)
            .where(
                RunRow.state == "terminal",
                RunRow.released_at.is_(None),
                RunRow.network_cidr != "",
            )
            .order_by(RunRow.completed_at)
        ).all()
        return [
            AwaitingRelease(
                r.id, r.terminal_trigger, _utc(r.completed_at), r.release_attempts, r.release_error
            )
            for r in rows
        ]


@dataclass(frozen=True)
class ReleaseBacklog:
    """Finished runs whose teardown FAILED and is still being retried (#173)."""

    pending: int
    oldest_ended_at: datetime | None
    oldest_error: str | None


def release_backlog() -> ReleaseBacklog:
    """A count and the oldest of them, for /system. Two small queries rather than loading every
    unreleased row: the GUI polls /system, and right after an upgrade the backlog can be long.
    Runs merely waiting for their grade (no failed attempt yet) are not counted."""
    failed = (
        RunRow.state == "terminal",
        RunRow.released_at.is_(None),
        RunRow.network_cidr != "",
        RunRow.release_attempts > 0,
    )
    with session_scope() as s:
        pending = s.scalar(select(func.count()).select_from(RunRow).where(*failed)) or 0
        oldest = s.scalar(select(RunRow).where(*failed).order_by(RunRow.completed_at).limit(1))
        if oldest is None:
            return ReleaseBacklog(pending, None, None)
        return ReleaseBacklog(pending, _utc(oldest.completed_at), oldest.release_error)


def is_budget_expired(run_id: str, now: datetime) -> bool:
    with session_scope() as s:
        row = s.scalar(select(RunRow).where(RunRow.id == run_id))
        if row is None or row.budget_seconds <= 0:
            return False
        created = _utc_required(row.created_at)
        return now >= created + timedelta(seconds=row.budget_seconds)


def active_runs_with_deadline() -> list[tuple[str, datetime]]:
    with session_scope() as s:
        rows = s.scalars(
            select(RunRow).where(RunRow.state != "terminal", RunRow.budget_seconds > 0)
        ).all()
        return [
            (r.id, _utc_required(r.created_at) + timedelta(seconds=r.budget_seconds)) for r in rows
        ]
