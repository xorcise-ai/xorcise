"""Pure ordering for the unified receipt-time terrain timeline (runs module, PURE).

Both terrain planes anchor to the SERVER clock so they can be merged chronologically without
agent/host clock skew: a mission update anchors to its span's trace-ingest receipt time
(`SqliteTraceStore.receipt_times`), an infra update to its own driving record's `created_at`
(join/brief/attachment fact, or artifact/intel/complete submission) or the first-span receipt
(telemetry) — that per-update anchor is produced alongside the update by
`terrain_updates_infra.infra_updates`, so this module only ORDERS the already-anchored streams.
`order_updates` merges them by `(receipt_ts, secondary_ts)` — the secondary key tiebreaks mission
spans that shared one OTLP export batch (same receipt time) — and re-seqs to array index. The
frontend folds the resulting array by POSITION, so an earlier fold index yields the true cumulative
state as of that moment (infra now rewinds correctly, monotonically).

Naive (SQLite) and aware (OTel) datetimes are normalized to UTC-aware so mixed sources compare.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from xorcise.core.contracts.terrain import TerrainUpdate

_MAX = datetime.max.replace(tzinfo=UTC)  # sort-key sentinel: a None (anchorless) update sorts last.

# One (primary_ts, secondary_ts, update) triple fed to order_updates; either ts may be None.
_Item = tuple[datetime | None, datetime | None, TerrainUpdate]


def _utc(dt: datetime) -> datetime:
    """Normalize to UTC-aware (SQLite returns tz-naive; OTel-decoded spans are aware)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def order_updates(items: Sequence[_Item]) -> tuple[TerrainUpdate, ...]:
    """Stable-sort `(primary_ts, secondary_ts, update)` triples by their normalized timestamps and
    re-seq each update to its array index, STAMPING each update's `ts` with its normalized primary
    anchor (None when anchorless). A None anchor sorts LAST (an anchorless update — objective grade
    / missing timestamp — has no place in the chronological stream). Stable: equal keys keep input
    order (feed infra before mission so infra wins an exact tie — platform state before the
    action). `seq` is cosmetic (the frontend folds by array position); `ts` lets the frontend
    interleave infra rows into the Trace by the same server clock the map orders by."""

    def _key(it: _Item) -> tuple[datetime, datetime]:
        primary, secondary, _ = it
        return (_utc(primary) if primary else _MAX, _utc(secondary) if secondary else _MAX)

    ordered = sorted(items, key=_key)
    return tuple(
        u.model_copy(update={"seq": i, "ts": _utc(primary) if primary else None})
        for i, (primary, _, u) in enumerate(ordered)
    )
