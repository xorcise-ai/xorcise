"""sqlite-backed TraceStore (PART-ISLAND adapter; the shipped RAW trace store).

ClickHouse/Postgres are possible futures behind this same TraceStore ABC — not built. The SQL here
is engine-neutral SQLAlchemy, including the admission barrier, so the name records the default
backend rather than a dependency on it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.db import session_scope
from xorcise.core.otel.ports import TraceStore
from xorcise.core.otel.store.admission import is_sealed_in, under_admission
from xorcise.core.otel.store.models import LogRow, TraceRow


def _utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; persisted timestamps are UTC by contract."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _append_next(model: type[TraceRow] | type[LogRow], run_id: str, payload: str) -> int | None:
    """Append `payload` as the run's next seq, or return None if the run is sealed.

    Under the run's admission lock, so the seal check, the seq and the insert are one step against
    both a concurrent seal and a concurrent append. The seq is MAX + 1, not a count of materialised
    rows (#147): the (run_id, seq) unique index makes it one index seek.
    """

    def admit(s: Session) -> int | None:
        if is_sealed_in(s, run_id):
            return None
        last = s.scalar(select(func.max(model.seq)).where(model.run_id == run_id))
        seq = 0 if last is None else last + 1
        s.add(model(run_id=run_id, seq=seq, payload=payload))
        return seq

    return under_admission(run_id, admit)


class SqliteTraceStore(TraceStore):
    def append_next(self, run_id: str, payload: str) -> int | None:
        return _append_next(TraceRow, run_id, payload)

    def append(self, record: TraceRecord) -> None:
        with session_scope() as s:
            s.add(TraceRow(run_id=record.run_id, seq=record.seq, payload=record.payload))

    def read(self, run_id: str) -> list[TraceRecord]:
        with session_scope() as s:
            rows = s.scalars(
                select(TraceRow).where(TraceRow.run_id == run_id).order_by(TraceRow.seq)
            ).all()
            return [
                TraceRecord(
                    run_id=r.run_id,
                    seq=r.seq,
                    payload=r.payload,
                    received_at=_utc(r.created_at),
                )
                for r in rows
            ]

    def read_since(self, run_id: str, after_seq: int) -> list[TraceRecord]:
        with session_scope() as s:
            rows = s.scalars(
                select(TraceRow)
                .where(TraceRow.run_id == run_id, TraceRow.seq > after_seq)
                .order_by(TraceRow.seq)
            ).all()
            return [
                TraceRecord(
                    run_id=r.run_id,
                    seq=r.seq,
                    payload=r.payload,
                    received_at=_utc(r.created_at),
                )
                for r in rows
            ]

    def ordered_payloads(self, run_id: str) -> Iterator[tuple[int, str]]:
        """Stream `(seq, payload)` for the run in a CANONICAL order — the evidence-digest read.

        Two columns, not `TraceRecord`s: the digest covers `seq` and `payload` only, and building
        pydantic models for a 20k-batch run cost ~95% of the hash and threw `received_at` away
        again. Streamed rather than listed so a 66 MB run is hashed incrementally instead of held
        in memory twice (here and in the caller).

        Ordered by `(seq, payload)`. Since `(run_id, seq)` became unique (#147) the payload never
        breaks a tie; it stays because it is part of what `xorcise-evidence-v1` digests were
        computed over, and it costs nothing on a unique key.
        """
        with session_scope() as s:
            yield from s.execute(
                select(TraceRow.seq, TraceRow.payload)
                .where(TraceRow.run_id == run_id)
                .order_by(TraceRow.seq, TraceRow.payload)
            ).tuples()

    def has_records(self, run_id: str) -> bool:
        """Has the run exported anything? One index probe on `(run_id, seq)`, not a read."""
        with session_scope() as s:
            return bool(s.scalar(select(exists().where(TraceRow.run_id == run_id))))

    def receipt_times(self, run_id: str) -> dict[int, datetime]:
        """Map each trace record's `seq` -> its server-side ingest time (`TraceRow.created_at`, the
        OTLP-receipt clock). The anchor for mission-plane spans on the unified terrain timeline;
        one time per export batch (all spans in a batch share it — the timeline tiebreaks
        intra-batch by the agent span time). Values are normalized back to UTC after SQLite."""
        with session_scope() as s:
            rows = s.execute(
                select(TraceRow.seq, TraceRow.created_at).where(TraceRow.run_id == run_id)
            ).all()
            return {seq: _utc(created_at) for seq, created_at in rows}

    def latest_receipts(self) -> dict[str, datetime]:
        """Map every run_id -> the newest server-side ingest time across its export batches.
        One grouped query for the whole table (the runs-list join reads this once per request,
        not once per run). Values are normalized back to UTC after SQLite."""
        with session_scope() as s:
            rows = s.execute(
                select(TraceRow.run_id, func.max(TraceRow.created_at)).group_by(TraceRow.run_id)
            ).all()
            return {run_id: _utc(created_at) for run_id, created_at in rows}

    def stream(self, run_id: str) -> Iterator[TraceRecord]:
        yield from self.read(run_id)


class SqliteLogStore(TraceStore):
    """The RAW OTLP *logs* store — the peer of SqliteTraceStore, backed by `logs`.

    Reuses the signal-agnostic TraceRecord (run_id/seq/payload) + the TraceStore ABC; only the
    backing table differs. RAW-canonical, keyed by run_id.
    """

    def append_next(self, run_id: str, payload: str) -> int | None:
        return _append_next(LogRow, run_id, payload)

    def append(self, record: TraceRecord) -> None:
        with session_scope() as s:
            s.add(LogRow(run_id=record.run_id, seq=record.seq, payload=record.payload))

    def read(self, run_id: str) -> list[TraceRecord]:
        with session_scope() as s:
            rows = s.scalars(
                select(LogRow).where(LogRow.run_id == run_id).order_by(LogRow.seq)
            ).all()
            return [
                TraceRecord(
                    run_id=r.run_id,
                    seq=r.seq,
                    payload=r.payload,
                    received_at=_utc(r.created_at),
                )
                for r in rows
            ]

    def read_since(self, run_id: str, after_seq: int) -> list[TraceRecord]:
        with session_scope() as s:
            rows = s.scalars(
                select(LogRow)
                .where(LogRow.run_id == run_id, LogRow.seq > after_seq)
                .order_by(LogRow.seq)
            ).all()
            return [
                TraceRecord(
                    run_id=r.run_id,
                    seq=r.seq,
                    payload=r.payload,
                    received_at=_utc(r.created_at),
                )
                for r in rows
            ]

    def ordered_payloads(self, run_id: str) -> Iterator[tuple[int, str]]:
        """Stream `(seq, payload)` in canonical `(seq, payload)` order — the evidence-digest read,
        matching SqliteTraceStore."""
        with session_scope() as s:
            yield from s.execute(
                select(LogRow.seq, LogRow.payload)
                .where(LogRow.run_id == run_id)
                .order_by(LogRow.seq, LogRow.payload)
            ).tuples()

    def receipt_times(self, run_id: str) -> dict[int, datetime]:
        """Map each log export seq to its server-side ingest time, matching SqliteTraceStore."""
        with session_scope() as s:
            rows = s.execute(
                select(LogRow.seq, LogRow.created_at).where(LogRow.run_id == run_id)
            ).all()
            return {seq: _utc(created_at) for seq, created_at in rows}

    def latest_receipts(self) -> dict[str, datetime]:
        """Map every run_id -> the newest log-batch ingest time, matching SqliteTraceStore."""
        with session_scope() as s:
            rows = s.execute(
                select(LogRow.run_id, func.max(LogRow.created_at)).group_by(LogRow.run_id)
            ).all()
            return {run_id: _utc(created_at) for run_id, created_at in rows}

    def stream(self, run_id: str) -> Iterator[TraceRecord]:
        yield from self.read(run_id)
