# tests/unit/test_otel_drops.py
"""Dropped-batch accounting for the OTLP receiver (#121): counters, the bounded spool, and the
settings → recorder factory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from xorcise.core.otel.ingest.drops import (
    DEFAULT_SPOOL_MAX_BYTES,
    DropCounters,
    DropRecorder,
    DropSpool,
    drop_recorder_from_settings,
)

pytestmark = pytest.mark.unit

_BATCH = json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "a"}]}]}]})


def test_counters_tally_by_signal_and_reason() -> None:
    c = DropCounters()
    c.add("traces", "unroutable", 3)
    c.add("traces", "unroutable", 2)
    c.add("logs", "sealed", 4)
    assert c.unroutable_spans == 5 and c.unroutable_batches == 2
    assert c.sealed_log_records == 4 and c.sealed_batches == 1
    assert c.sealed_spans == 0 and c.unroutable_log_records == 0
    assert set(c.as_dict()) == {
        "unroutable_spans",
        "unroutable_log_records",
        "sealed_spans",
        "sealed_log_records",
        "unroutable_batches",
        "sealed_batches",
        "spooled_batches",
    }


def test_spool_writes_an_envelope_and_keeps_only_the_newest_cap_files(tmp_path: Path) -> None:
    spool = DropSpool(tmp_path / "dropped", cap=2)
    paths = [
        spool.write(signal="traces", reason="unroutable", run_id="", payload=_BATCH)
        for _ in range(3)
    ]
    assert all(p is not None for p in paths)
    remaining = sorted(p.name for p in (tmp_path / "dropped").glob("*.json"))
    assert len(remaining) == 2  # the oldest was evicted
    assert paths[0] is not None and paths[0].name not in remaining
    envelope = json.loads((tmp_path / "dropped" / remaining[-1]).read_text())
    assert envelope["signal"] == "traces" and envelope["reason"] == "unroutable"
    assert envelope["run_id"] is None  # blank run id → null, not ""
    assert envelope["payload"] == json.loads(_BATCH)  # the batch itself, intact
    assert "received_at" in envelope


def test_spool_evicts_oldest_until_the_byte_budget_holds_even_under_the_file_cap(
    tmp_path: Path,
) -> None:
    """The file cap alone left disk use at cap × max_payload. With a generous cap and a budget
    sized for two envelopes, the third write must evict the oldest on BYTES."""
    probe = DropSpool(tmp_path / "probe").write(
        signal="traces", reason="unroutable", run_id="", payload=_BATCH
    )
    assert probe is not None
    one = probe.stat().st_size
    spool = DropSpool(tmp_path / "dropped", cap=100, max_bytes=one * 2 + one // 2)
    paths = [
        spool.write(signal="traces", reason="unroutable", run_id="", payload=_BATCH)
        for _ in range(3)
    ]
    assert all(p is not None for p in paths)
    remaining = sorted((tmp_path / "dropped").glob("*.json"))
    assert len(remaining) == 2  # far under cap=100; the byte budget did the evicting
    assert paths[0] is not None and paths[0] not in remaining  # oldest went first
    assert paths[2] is not None and paths[2] in remaining  # newest kept
    assert sum(p.stat().st_size for p in remaining) <= spool.max_bytes


def test_spool_refuses_a_single_batch_larger_than_the_whole_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One oversized batch must not be written and then immediately evict everything else —
    it is refused up front, logged, and reported as not spooled (the counters still see it)."""
    spool = DropSpool(tmp_path / "dropped", cap=10, max_bytes=32)
    with caplog.at_level(logging.WARNING):
        assert spool.write(signal="logs", reason="sealed", run_id="r", payload=_BATCH) is None
    assert "exceeds the 32-byte budget" in caplog.text
    assert not list((tmp_path / "dropped").glob("*.json"))  # nothing landed
    rec = DropRecorder(spool=spool)
    rec.record(signal="logs", reason="sealed", n=1, run_id="r", payload=_BATCH)
    assert rec.counters.sealed_log_records == 1 and rec.counters.spooled_batches == 0


def test_spool_never_raises_on_a_bad_payload(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    spool = DropSpool(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert spool.write(signal="logs", reason="sealed", run_id="r", payload="{nope") is None
    assert "spool write failed" in caplog.text


def test_recorder_counts_logs_and_spools(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    rec = DropRecorder(spool=DropSpool(tmp_path))
    with caplog.at_level(logging.WARNING):
        rec.record(signal="traces", reason="sealed", n=2, run_id="run-1", payload=_BATCH)
    assert rec.counters.sealed_spans == 2 and rec.counters.spooled_batches == 1
    assert "otlp drop: signal=traces reason=sealed spans=2 run_id=run-1 spooled=" in caplog.text


def test_recorder_without_spool_still_counts_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    rec = DropRecorder()
    with caplog.at_level(logging.WARNING):
        rec.record(signal="logs", reason="unroutable", n=3, run_id="", payload=_BATCH)
    assert rec.counters.unroutable_log_records == 3 and rec.counters.spooled_batches == 0
    assert "signal=logs reason=unroutable log_records=3 run_id=- spooled=no" in caplog.text


def test_factory_spools_only_when_the_operator_opted_in(tmp_path: Path) -> None:
    off = drop_recorder_from_settings(SimpleNamespace(otel_drop_spool_enabled=False))
    assert off.spool is None
    on = drop_recorder_from_settings(
        SimpleNamespace(
            otel_drop_spool_enabled=True, otel_drop_spool_dir=str(tmp_path), otel_drop_spool_cap=7
        )
    )
    assert on.spool is not None and on.spool.root == tmp_path and on.spool.cap == 7
    assert on.spool.max_bytes == DEFAULT_SPOOL_MAX_BYTES  # absent on an older Settings → default
    sized = drop_recorder_from_settings(
        SimpleNamespace(
            otel_drop_spool_enabled=True,
            otel_drop_spool_dir=str(tmp_path),
            otel_drop_spool_cap=7,
            otel_drop_spool_max_bytes=4096,
        )
    )
    assert sized.spool is not None and sized.spool.max_bytes == 4096
