"""Observed facts: the XORCISE-owned second evidence stream.

Run-control facts are DERIVED from the run's submission kinds (no new capture path); the
projector takes plain data, never the runcontrol store, so the runs module does not import a
sibling module (the non-importing-siblings rule). Network-lifecycle/ACL facts are
recorded run-scoped via ObservedFactsStore at run-network create. Pure projectors are
deterministic for a fixed input — the reproducible anchor the deterministic half corroborates
against. The exact v1 fact vocabulary is deliberately small.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.db import session_scope
from xorcise.core.runs.models import RunObservedFactRow

log = logging.getLogger(__name__)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite drops timezone metadata; persisted timestamps are UTC by contract."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def run_control_facts(kinds: Sequence[str]) -> dict[str, object]:
    """Project the run's ordered submission kinds into coarse run-control observed facts.

    `kinds` is the sequence of `RunSubmission.kind` values for the run (flag | artifact | intel |
    complete) — passed as plain data by the delivery-layer assembler, never the runcontrol store.
    """
    ks = list(kinds)
    return {
        "submission-count": sum(k in ("flag", "artifact") for k in ks),
        "artifact-count": ks.count("artifact"),
        "flag-submitted": "flag" in ks,
        "intel-count": ks.count("intel"),
        "completed": "complete" in ks,
    }


def network_facts(
    run_id: str, *, agent_user: str, entry_cidrs: Sequence[str]
) -> tuple[ObservedFact, ...]:
    """Per-run boundary CONFIG as observed facts, from the fence's RunNetwork primitives.

    Records the enforced entry CIDRs (the boundary configuration) + the join event + the agent
    user — NEVER the pre-auth keys (secrets). Takes primitives, not the headscale RunNetwork, so
    the runs module does not import a part-island (the delivery caller extracts them). Per-flow /
    boundary-VIOLATION facts are deferred (future work).
    """
    return (
        ObservedFact(
            run_id=run_id, kind="acl-config", name="entry-cidrs", value=",".join(entry_cidrs)
        ),
        ObservedFact(run_id=run_id, kind="network-lifecycle", name="agent-user", value=agent_user),
        ObservedFact(run_id=run_id, kind="network-lifecycle", name="join", value="created"),
    )


class ObservedFactsStore(ABC):
    """Run-scoped, append-only store for XORCISE-owned observed facts (network/ACL).

    Immutable once a run is sealed: there is no update/delete path, and `record` refuses a fact
    for a sealed run."""

    @abstractmethod
    def record(self, fact: ObservedFact) -> bool:
        """Record *fact*. False, and nothing written, if its run is already sealed."""

    @abstractmethod
    def list_for_run(self, run_id: str) -> tuple[ObservedFact, ...]: ...


class InMemoryObservedFactsStore(ObservedFactsStore):
    def __init__(self) -> None:
        self._rows: list[ObservedFact] = []

    def record(self, fact: ObservedFact) -> bool:
        self._rows.append(fact)  # no seal here: nothing in memory is ever sealed
        return True

    def list_for_run(self, run_id: str) -> tuple[ObservedFact, ...]:
        return tuple(f for f in self._rows if f.run_id == run_id)


class SqliteObservedFactsStore(ObservedFactsStore):
    def record(self, fact: ObservedFact) -> bool:
        """Refused once the run is sealed (#194). Observed facts are inside the evidence digest, so
        one written after the seal turned an untouched run's verification into `mismatch`. A
        finished run's terrain view wrote one, and so could the join reconciler, which polls for
        two minutes after its last kick whether or not the run has ended since.

        Under the run's admission lock, the one that orders telemetry against sealing (#184), so a
        fact either commits before the seal, and is hashed, or sees the seal and is refused. Lazy
        import keeps the OTel store off this module's import path (plane isolation).

        A run that took such a fact before this fix keeps reading `mismatch`, on purpose. Nothing
        repairs it: that would mean deleting rows from sealed evidence, the very act the seal exists
        to expose. A `telemetry-lifecycle` or `network-lifecycle`/`join` fact whose `created_at` is
        after the run's `sealed_at` is this bug, not tampering."""
        from xorcise.core.otel.store.admission import is_sealed_in, under_admission

        def add(s: Session) -> bool:
            if is_sealed_in(s, fact.run_id):
                log.info("run %s is sealed; not recording %s/%s", fact.run_id, fact.kind, fact.name)
                return False
            s.add(
                RunObservedFactRow(
                    run_id=fact.run_id, kind=fact.kind, name=fact.name, value=fact.value
                )
            )
            return True

        return under_admission(fact.run_id, add)

    def list_for_run(self, run_id: str) -> tuple[ObservedFact, ...]:
        with session_scope() as s:
            rows = s.scalars(
                select(RunObservedFactRow)
                .where(RunObservedFactRow.run_id == run_id)
                .order_by(RunObservedFactRow.seq)
            ).all()
            return tuple(
                ObservedFact(
                    run_id=r.run_id,
                    kind=r.kind,
                    name=r.name,
                    value=r.value,
                    created_at=_utc(r.created_at),
                )
                for r in rows
            )
