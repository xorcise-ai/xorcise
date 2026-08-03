"""OTLP wire DTOs (LEAF). Decode/route detail lives in core.otel.

Imports nothing internal (stdlib + pydantic only).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceRef(_Frozen):
    """Pointer to a stored trace, keyed by run_id."""

    run_id: str


class SpanEnvelope(_Frozen):
    """Thin OTLP span envelope placeholder (full span mapping lives in core.otel.decode)."""

    run_id: str
    span_id: str
    name: str


class IngestAck(_Frozen):
    run_id: str
    accepted: int = 0
