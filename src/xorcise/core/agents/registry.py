"""Thin agent registry domain logic (application layer). Stores a declaration, not analysis."""

from __future__ import annotations

from datetime import UTC
from typing import Literal
from uuid import uuid4

from sqlalchemy import select

from xorcise.core.agents.models import AgentRow
from xorcise.core.contracts.agent import AgentEntry
from xorcise.core.db import session_scope


class DuplicateAgentError(ValueError):
    """Raised when registering a name that is already taken."""


def _to_entry(row: AgentRow) -> AgentEntry:
    # SQLite drops tzinfo on read even for DateTime(timezone=True) columns; the
    # stored value IS UTC (the column default), so restamp it — every other DTO
    # timestamp is Z-suffixed and scripts join agent + run timelines.
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return AgentEntry(
        id=row.id,
        name=row.name,
        endpoint=row.endpoint,
        otel=row.otel,
        model=row.model,
        kind=row.kind,
        launch_command_template=row.launch_command_template,
        launch_tips=tuple(row.launch_tips) if row.launch_tips is not None else None,
        mission_preamble=(
            tuple(row.mission_preamble) if row.mission_preamble is not None else None
        ),
        launch_mode=row.launch_mode,
        created_at=created,
        version=row.version,
    )


def register(
    name: str,
    endpoint: str | None = None,
    otel: str | None = None,
    model: str | None = None,
    kind: str | None = None,
    launch_command_template: str | None = None,
    launch_tips: tuple[str, ...] | None = None,
    mission_preamble: tuple[str, ...] | None = None,
    launch_mode: Literal["host", "container"] | None = None,
) -> AgentEntry:
    """Register a thin agent declaration. Rejects a duplicate name."""
    with session_scope() as s:
        if s.scalar(select(AgentRow).where(AgentRow.name == name)) is not None:
            raise DuplicateAgentError(f"agent '{name}' already registered")
        row = AgentRow(
            id=uuid4().hex,
            name=name,
            endpoint=endpoint,
            otel=otel,
            model=model,
            kind=kind,
            launch_command_template=launch_command_template,
            launch_tips=list(launch_tips) if launch_tips is not None else None,
            mission_preamble=list(mission_preamble) if mission_preamble is not None else None,
            launch_mode=launch_mode,
            version=1,
        )
        s.add(row)
        s.flush()
        return _to_entry(row)


def update_agent(
    name: str,
    *,
    endpoint: str | None = None,
    otel: str | None = None,
    model: str | None = None,
    kind: str | None = None,
    launch_command_template: str | None = None,
    launch_tips: tuple[str, ...] | None = None,
    mission_preamble: tuple[str, ...] | None = None,
    launch_mode: Literal["host", "container"] | None = None,
    new_name: str | None = None,
) -> AgentEntry | None:
    """Update an existing agent's declaration and bump its version (same id).

    This is a full re-declaration at a new version: the supplied endpoint/otel/model/kind
    REPLACE the previous values (None means "unset"). A differing ``new_name`` renames the
    agent — the id stays stable, so runs and history survive — and raises
    DuplicateAgentError when that name is taken. Returns None if the agent is absent;
    the caller decides whether to 404.
    """
    with session_scope() as s:
        row = s.scalar(select(AgentRow).where(AgentRow.name == name))
        if row is None:
            return None
        if new_name is not None and new_name != name:
            if s.scalar(select(AgentRow).where(AgentRow.name == new_name)) is not None:
                raise DuplicateAgentError(f"agent '{new_name}' already registered")
            row.name = new_name
        row.endpoint = endpoint
        row.otel = otel
        row.model = model
        row.kind = kind
        row.launch_command_template = launch_command_template
        row.launch_tips = list(launch_tips) if launch_tips is not None else None
        row.mission_preamble = list(mission_preamble) if mission_preamble is not None else None
        row.launch_mode = launch_mode
        row.version = row.version + 1
        s.flush()
        return _to_entry(row)


def get(name: str) -> AgentEntry | None:
    """Resolve a registered agent by name, or None if absent."""
    with session_scope() as s:
        row = s.scalar(select(AgentRow).where(AgentRow.name == name))
        return _to_entry(row) if row is not None else None


def get_by_id(agent_id: str) -> AgentEntry | None:
    """Resolve a registered agent by stable id, or None if absent."""
    with session_scope() as s:
        row = s.get(AgentRow, agent_id)
        return _to_entry(row) if row is not None else None


def list_agents() -> list[AgentEntry]:
    """All registered agents, oldest first."""
    with session_scope() as s:
        rows = s.scalars(select(AgentRow).order_by(AgentRow.created_at)).all()
        return [_to_entry(r) for r in rows]


def remove(name: str) -> bool:
    """Hard-delete by name. Returns True iff a row was removed."""
    with session_scope() as s:
        row = s.scalar(select(AgentRow).where(AgentRow.name == name))
        if row is None:
            return False
        s.delete(row)
        return True
