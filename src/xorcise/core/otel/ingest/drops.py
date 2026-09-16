"""Dropped-batch accounting for the OTLP receiver (PART-ISLAND helper, stdlib only).

The receiver drops a batch in exactly two cases: it cannot be routed to a run (no
`xorcise.run_id` resource attribute and no prompt sentinel in its content), or its run is
already sealed. Until now the ONLY trace of either was the `partialSuccess` count in the HTTP
response — which exporters ignore — so an operator could not tell it had happened (#121).

`DropRecorder` gives every drop three durable homes: a structured WARNING log line, in-process
counters (served on the collector's `/healthz`), and — when the operator opts in — a bounded
on-disk spool of the dropped batches themselves, so an unroutable export can be inspected and,
once its run is known, understood. The spool is global by necessity: a batch that failed
correlation has no run id to file it under.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

log = logging.getLogger(__name__)

Signal = str  # "traces" | "logs"
Reason = str  # "unroutable" | "sealed"


@dataclass
class DropCounters:
    """Per-process tallies of what the receiver refused, by signal and reason."""

    unroutable_spans: int = 0
    unroutable_log_records: int = 0
    sealed_spans: int = 0
    sealed_log_records: int = 0
    unroutable_batches: int = 0
    sealed_batches: int = 0
    spooled_batches: int = 0

    def add(self, signal: Signal, reason: Reason, n: int) -> None:
        unit = "spans" if signal == "traces" else "log_records"
        setattr(self, f"{reason}_{unit}", getattr(self, f"{reason}_{unit}") + n)
        setattr(self, f"{reason}_batches", getattr(self, f"{reason}_batches") + 1)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class DropSpool:
    """A bounded directory of dropped batches, newest kept, oldest evicted. Best-effort: a
    failure to write is logged and swallowed — the receiver must keep answering the agent."""

    def __init__(self, root: Path, cap: int = 200) -> None:
        self.root = root
        self.cap = max(1, cap)
        self._seq = count()

    def write(self, *, signal: Signal, reason: Reason, run_id: str, payload: str) -> Path | None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            path = self.root / f"{stamp}-{next(self._seq):06d}-{signal}-{reason}.json"
            envelope = {
                "received_at": datetime.now(UTC).isoformat(),
                "signal": signal,
                "reason": reason,
                "run_id": run_id or None,
                "payload": json.loads(payload),
            }
            path.write_text(json.dumps(envelope, separators=(",", ":")) + "\n", encoding="utf-8")
            self._evict()
            return path
        except (OSError, ValueError):
            log.warning("otlp drop spool write failed under %s", self.root, exc_info=True)
            return None

    def _evict(self) -> None:
        files = sorted(p for p in self.root.glob("*.json") if p.is_file())
        for stale in files[: max(0, len(files) - self.cap)]:
            # A racing unlink is not worth failing ingest for.
            with contextlib.suppress(OSError):
                stale.unlink()


@dataclass
class DropRecorder:
    """Where a dropped batch goes: counters + a WARNING log line, and the spool when configured."""

    counters: DropCounters = field(default_factory=DropCounters)
    spool: DropSpool | None = None

    def record(self, *, signal: Signal, reason: Reason, n: int, run_id: str, payload: str) -> None:
        self.counters.add(signal, reason, n)
        spooled = (
            self.spool.write(signal=signal, reason=reason, run_id=run_id, payload=payload)
            if self.spool is not None
            else None
        )
        if spooled is not None:
            self.counters.spooled_batches += 1
        log.warning(
            "otlp drop: signal=%s reason=%s %s=%d run_id=%s spooled=%s",
            signal,
            reason,
            "spans" if signal == "traces" else "log_records",
            n,
            run_id or "-",
            spooled.name if spooled is not None else "no",
        )


def drop_recorder_from_settings(settings: object) -> DropRecorder:
    """The production recorder: counters + log always; the spool only when the operator opted in
    (`otel_drop_spool_enabled`), bounded by `otel_drop_spool_cap` under `otel_drop_spool_dir`.
    Duck-typed over the Settings object so this stdlib-only helper needs no config import."""
    if not getattr(settings, "otel_drop_spool_enabled", False):
        return DropRecorder()
    root = Path(str(getattr(settings, "otel_drop_spool_dir", "") or "."))
    cap = int(getattr(settings, "otel_drop_spool_cap", 200) or 200)
    return DropRecorder(spool=DropSpool(root, cap=cap))
