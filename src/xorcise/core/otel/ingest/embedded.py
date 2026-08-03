"""Embedded OTLP/HTTP receiver (PART-ISLAND, default collector).

Decodes the agent's OTLP/HTTP export (JSON or protobuf), routes each
ResourceSpans to its run by xorcise.run_id, and persists the RAW payload
unmodified via a TraceStore (sqlite default). Dropped (unroutable) spans are
reported via the standard OTLP partialSuccess.rejectedSpans; they are never
blended into another run.

Content-type sniffing: ``application/x-protobuf`` → protobuf decode
(requires the ``collector`` extra); anything else → JSON decode.
"""

from __future__ import annotations

import gzip
import json

from fastapi import FastAPI, Request, Response

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.decode import (
    RouteResult,
    route_otlp_json,
    route_otlp_logs_json,
    route_otlp_logs_protobuf,
    route_otlp_protobuf,
)
from xorcise.core.otel.ports import SealStore, TraceStore
from xorcise.core.otel.store import InMemorySealStore, SqliteLogStore, SqliteTraceStore


def _count_spans(payload: str) -> int:
    """Count spans in a routed RAW payload ({"resourceSpans": [...]})."""
    doc = json.loads(payload)
    return sum(
        len(ss.get("spans", []))
        for rs in doc.get("resourceSpans", [])
        for ss in rs.get("scopeSpans", [])
    )


def _count_log_records(payload: str) -> int:
    """Count log records in a routed RAW payload ({"resourceLogs": [...]})."""
    doc = json.loads(payload)
    return sum(
        len(sl.get("logRecords", []))
        for rl in doc.get("resourceLogs", [])
        for sl in rl.get("scopeLogs", [])
    )


def _decode_unavailable_response(exc: RuntimeError) -> Response:
    """A protobuf export arrived but the decoder is unavailable (opentelemetry-proto absent —
    the ``collector`` extra, now bundled in the base install). Surface the decoder's actionable
    message as a clean 500 instead of letting the RuntimeError escape as an unhandled traceback.
    Status stays 500 (a server-side capability gap, not a client error), matching the prior
    unhandled behaviour — only the body is now useful. gRPC code 12 = UNIMPLEMENTED.
    """
    return Response(
        content=json.dumps({"code": 12, "message": str(exc)}),
        media_type="application/json",
        status_code=500,
    )


def create_otel_app(
    store: TraceStore | None = None,
    seal_store: SealStore | None = None,
    log_store: TraceStore | None = None,
) -> FastAPI:
    trace_store: TraceStore = store if store is not None else SqliteTraceStore()
    logs_store: TraceStore = log_store if log_store is not None else SqliteLogStore()
    seals: SealStore = seal_store if seal_store is not None else InMemorySealStore()
    app = FastAPI(title="xorcise-otel", version="1")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "otel"}

    @app.post("/v1/traces")
    async def ingest_traces(request: Request) -> Response:
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        # OTLP/HTTP clients MAY gzip the payload (OTel spec; the OpenHands/Laminar exporter does
        # so unconditionally). Decompress before decode — otherwise the raw gzip bytes fail to
        # parse and the export is rejected as malformed. gzip.decompress raises OSError
        # (BadGzipFile) / EOFError on a corrupt body; those fall through to the 400 below.
        encoding = request.headers.get("content-encoding", "").lower()
        result: RouteResult
        try:
            if encoding == "gzip":
                raw_body = gzip.decompress(raw_body)
            if content_type.startswith("application/x-protobuf"):
                result = route_otlp_protobuf(raw_body)
            else:
                result = route_otlp_json(raw_body.decode("utf-8"))
        except RuntimeError as exc:
            return _decode_unavailable_response(exc)
        except (ValueError, UnicodeDecodeError, OSError, EOFError):
            return Response(
                content=json.dumps({"code": 3, "message": "malformed OTLP body"}),
                media_type="application/json",
                status_code=400,
            )
        rejected = result.dropped_spans
        for run_id, payload in result.routed:
            if seals.is_sealed(run_id):
                # late spans after terminal are not admitted to the sealed record
                rejected += _count_spans(payload)
                continue
            seq = len(trace_store.read(run_id))
            trace_store.append(TraceRecord(run_id=run_id, seq=seq, payload=payload))
        out: dict[str, object] = {}
        if rejected:
            out = {
                "partialSuccess": {
                    "rejectedSpans": rejected,
                    "errorMessage": "missing/unknown xorcise.run_id or run is sealed",
                }
            }
        return Response(content=json.dumps(out), media_type="application/json")

    @app.post("/v1/logs")
    async def ingest_logs(request: Request) -> Response:
        # The OTLP LOGS signal: assistant responses, api bodies, the claude_code.events
        # stream. Mirrors /v1/traces — gzip + json/protobuf sniff, route by xorcise.run_id, persist
        # RAW to the logs store, seal-check, report unroutable records via partialSuccess.
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        encoding = request.headers.get("content-encoding", "").lower()
        result: RouteResult
        try:
            if encoding == "gzip":
                raw_body = gzip.decompress(raw_body)
            if content_type.startswith("application/x-protobuf"):
                result = route_otlp_logs_protobuf(raw_body)
            else:
                result = route_otlp_logs_json(raw_body.decode("utf-8"))
        except RuntimeError as exc:
            return _decode_unavailable_response(exc)
        except (ValueError, UnicodeDecodeError, OSError, EOFError):
            return Response(
                content=json.dumps({"code": 3, "message": "malformed OTLP body"}),
                media_type="application/json",
                status_code=400,
            )
        rejected = result.dropped_spans
        for run_id, payload in result.routed:
            if seals.is_sealed(run_id):
                rejected += _count_log_records(payload)
                continue
            seq = len(logs_store.read(run_id))
            logs_store.append(TraceRecord(run_id=run_id, seq=seq, payload=payload))
        out: dict[str, object] = {}
        if rejected:
            out = {
                "partialSuccess": {
                    "rejectedLogRecords": rejected,
                    "errorMessage": "missing/unknown xorcise.run_id or run is sealed",
                }
            }
        return Response(content=json.dumps(out), media_type="application/json")

    return app
