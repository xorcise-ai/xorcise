"""Run-scoped v2 terrain update store. Persists the ordered per-span terrain updates
the v2 map folds client-side: node/group/edge updates, plus `target_kind="none"`
marker rows recording a considered-but-no-op span (is_an_action=false) so that span is CACHED
(not re-attributed) and counts as considered without ever reaching the wire — the wire
`TerrainUpdate.target_kind` stays node|group|edge. Derived + rebuildable; never canonical.
Mirrors terrain_action_store.py's store shape."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from xorcise.core.contracts.terrain import TerrainUpdate
from xorcise.core.db import session_scope
from xorcise.core.runs.models import TerrainUpdateRow


@dataclass(frozen=True)
class _UpdateInput:
    """One terrain update to persist via `record_many`. `event_id=None` marks a deterministic
    infra update (no source agent_event)."""

    target_kind: Literal["node", "group", "edge"]
    target_id: str
    event_id: str | None = None
    state: Literal["discovered", "completed"] | None = None
    discovered: bool | None = None
    active: bool | None = None
    note: str | None = None


def _row_to_update(r: TerrainUpdateRow) -> TerrainUpdate:
    return TerrainUpdate(
        seq=r.seq,
        target_kind=r.target_kind,  # type: ignore[arg-type]  # DB text -> validated Literal by pydantic
        target_id=r.target_id,
        event_id=r.event_id,
        state=r.new_state,  # type: ignore[arg-type]
        discovered=r.discovered,
        active=r.active,
        note=r.note,
    )


class TerrainUpdateStore(ABC):
    @abstractmethod
    def record_many(self, run_id: str, updates: Sequence[_UpdateInput]) -> None: ...
    @abstractmethod
    def list_for_run(self, run_id: str) -> list[TerrainUpdate]: ...
    @abstractmethod
    def record_considered(self, run_id: str, event_ids: Iterable[str]) -> None: ...
    @abstractmethod
    def attributed_event_ids(self, run_id: str) -> set[str]: ...


class InMemoryTerrainUpdateStore(TerrainUpdateStore):
    def __init__(self) -> None:
        # key mirrors the DB UniqueConstraint(run_id, event_id, target_kind, target_id).
        self._rows: dict[tuple[str, str | None, str, str], TerrainUpdateRow] = {}
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record_many(self, run_id: str, updates: Sequence[_UpdateInput]) -> None:
        for u in updates:
            key = (run_id, u.event_id, u.target_kind, u.target_id)
            if key in self._rows:  # first write wins
                continue
            self._rows[key] = TerrainUpdateRow(
                seq=self._next_seq(),
                run_id=run_id,
                event_id=u.event_id,
                target_kind=u.target_kind,
                target_id=u.target_id,
                new_state=u.state,
                discovered=u.discovered,
                active=u.active,
                note=u.note,
            )

    def list_for_run(self, run_id: str) -> list[TerrainUpdate]:
        rows = [
            r for (rid, _, kind, _), r in self._rows.items() if rid == run_id and kind != "none"
        ]
        rows.sort(key=lambda r: r.seq)
        return [_row_to_update(r) for r in rows]

    def record_considered(self, run_id: str, event_ids: Iterable[str]) -> None:
        already = self.attributed_event_ids(run_id)
        for eid in event_ids:
            if eid in already:
                continue
            already.add(eid)
            key = (run_id, eid, "none", eid)
            self._rows[key] = TerrainUpdateRow(
                seq=self._next_seq(),
                run_id=run_id,
                event_id=eid,
                target_kind="none",
                target_id=eid,
                new_state=None,
                discovered=None,
                active=None,
            )

    def attributed_event_ids(self, run_id: str) -> set[str]:
        return {eid for (rid, eid, _, _) in self._rows if rid == run_id and eid is not None}


class SqliteTerrainUpdateStore(TerrainUpdateStore):
    def record_many(self, run_id: str, updates: Sequence[_UpdateInput]) -> None:
        if not updates:
            return
        with session_scope() as s:
            present = {
                (row.event_id, row.target_kind, row.target_id)
                for row in s.scalars(
                    select(TerrainUpdateRow).where(TerrainUpdateRow.run_id == run_id)
                ).all()
            }
            for u in updates:
                key = (u.event_id, u.target_kind, u.target_id)
                if key in present:
                    continue
                present.add(key)
                s.add(
                    TerrainUpdateRow(
                        run_id=run_id,
                        event_id=u.event_id,
                        target_kind=u.target_kind,
                        target_id=u.target_id,
                        new_state=u.state,
                        discovered=u.discovered,
                        active=u.active,
                        note=u.note,
                    )
                )

    def list_for_run(self, run_id: str) -> list[TerrainUpdate]:
        with session_scope() as s:
            rows = s.scalars(
                select(TerrainUpdateRow)
                .where(TerrainUpdateRow.run_id == run_id, TerrainUpdateRow.target_kind != "none")
                .order_by(TerrainUpdateRow.seq)
            ).all()
            return [_row_to_update(r) for r in rows]

    def record_considered(self, run_id: str, event_ids: Iterable[str]) -> None:
        event_ids = list(event_ids)
        if not event_ids:
            return
        with session_scope() as s:
            present = set(
                s.scalars(
                    select(TerrainUpdateRow.event_id).where(TerrainUpdateRow.run_id == run_id)
                ).all()
            )
            for eid in event_ids:
                if eid in present:
                    continue
                present.add(eid)
                s.add(
                    TerrainUpdateRow(
                        run_id=run_id,
                        event_id=eid,
                        target_kind="none",
                        target_id=eid,
                        new_state=None,
                        discovered=None,
                        active=None,
                    )
                )

    def attributed_event_ids(self, run_id: str) -> set[str]:
        with session_scope() as s:
            return {
                eid
                for eid in s.scalars(
                    select(TerrainUpdateRow.event_id).where(TerrainUpdateRow.run_id == run_id)
                ).all()
                if eid is not None
            }
