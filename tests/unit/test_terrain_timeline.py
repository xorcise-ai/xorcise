from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from xorcise.core.contracts.terrain import TerrainUpdate
from xorcise.core.runs.terrain_timeline import order_updates

pytestmark = pytest.mark.unit


def _u(target_id: str) -> TerrainUpdate:
    return TerrainUpdate(seq=99, target_kind="node", target_id=target_id, event_id=None)


def _dt(sec: int, *, aware: bool = True) -> datetime:
    d = datetime(2026, 1, 1) + timedelta(seconds=sec)
    return d.replace(tzinfo=UTC) if aware else d


# --- order_updates ----------------------------------------------------------------------------


def test_interleaves_by_primary_receipt_time_and_reseqs():
    a, b, c = _u("a"), _u("b"), _u("c")
    # fed out of order; primary times 30 / 10 / 20 -> expect b, c, a
    ordered = order_updates([(_dt(30), _dt(30), a), (_dt(10), _dt(10), b), (_dt(20), _dt(20), c)])
    assert [u.target_id for u in ordered] == ["b", "c", "a"]
    assert [u.seq for u in ordered] == [0, 1, 2]  # re-seq to array index


def test_secondary_key_tiebreaks_a_shared_batch_receipt_time():
    x, y = _u("x"), _u("y")
    # same primary (one export batch) -> ordered by the agent-span secondary
    ordered = order_updates([(_dt(10), _dt(9), x), (_dt(10), _dt(5), y)])
    assert [u.target_id for u in ordered] == ["y", "x"]


def test_normalizes_naive_and_aware_datetimes():
    naive, aware = _u("naive"), _u("aware")
    # a naive (SQLite) ts and an aware (OTel) ts must compare without raising
    ordered = order_updates(
        [(_dt(20, aware=False), _dt(20, aware=False), naive), (_dt(10), _dt(10), aware)]
    )
    assert [u.target_id for u in ordered] == ["aware", "naive"]


def test_stamps_each_update_ts_with_its_normalized_primary_anchor():
    a, b = _u("a"), _u("b")
    ordered = order_updates([(_dt(30), _dt(30), a), (_dt(10, aware=False), _dt(10), b)])
    by_target = {u.target_id: u for u in ordered}
    assert by_target["a"].ts == _dt(30)
    assert by_target["b"].ts == _dt(10)  # naive input normalized to UTC-aware on the wire
    assert by_target["b"].ts is not None and by_target["b"].ts.tzinfo is not None


def test_none_anchor_sorts_last_and_stamps_ts_none():
    anchored, anchorless = _u("anchored"), _u("anchorless")
    # an anchorless update (objective grade / missing timestamp) sorts LAST with a null wire ts
    ordered = order_updates([(None, None, anchorless), (_dt(10), _dt(10), anchored)])
    assert [u.target_id for u in ordered] == ["anchored", "anchorless"]
    by_target = {u.target_id: u for u in ordered}
    assert by_target["anchored"].ts == _dt(10)
    assert by_target["anchorless"].ts is None
