"""Telemetry wire DTOs (LEAF). The run_id-keyed records the trace store persists.

Imports nothing internal (stdlib + pydantic only).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TraceRecord(BaseModel):
    """A single run_id-keyed telemetry record appended to / read from the trace store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    seq: int
    payload: str  # opaque serialized span line; structured decode lives in core.otel
    # Server-side ingest time. Callers appending RAW telemetry leave this unset; durable stores
    # populate it from their row's created_at on read. This gives every derived display event a
    # clock comparable with XORCISE-owned infra facts without replacing the producer timestamp.
    received_at: datetime | None = None


class ObservedFact(BaseModel):
    """A single XORCISE-owned observed fact (NOT agent self-report). Run-scoped, immutable.

    `kind` groups the source ("run-control" | "network-lifecycle" | "acl-config"); `name` is the
    ref key the eval resolver looks up; `value` is a serialized scalar/JSON. The grader reads these
    as the anti-forgery anchor; the exact v1 fact vocabulary is deliberately small.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    kind: str
    name: str
    value: str
    # Server-side record time (when XORCISE observed the fact). Optional: in-memory/constructed
    # facts leave it None; the sqlite store populates it from the row. Used as the fact's anchor
    # on the unified receipt-time terrain timeline (same clock as trace-ingest receipt times).
    created_at: datetime | None = None
