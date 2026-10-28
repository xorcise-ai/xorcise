"""Embedded OTLP/HTTP receiver (PART-ISLAND, default collector).

Decodes the agent's OTLP/HTTP export (JSON or protobuf), routes each
ResourceSpans to its run by xorcise.run_id, and persists the RAW payload
unmodified via a TraceStore (sqlite default). Dropped (unroutable) spans are
reported via the standard OTLP partialSuccess.rejectedSpans; they are never
blended into another run.

Content-type sniffing: ``application/x-protobuf`` → protobuf decode
(requires the ``collector`` extra); anything else → JSON decode.

Every dropped batch — unroutable, or late for a sealed run — is also RECORDED via a
``DropRecorder`` (a WARNING log line, counters on ``/healthz``, and an optional bounded spool of
the batch itself), because the ``partialSuccess`` count in the response is invisible to the
operator: exporters ignore it (#121).

OFF THE EVENT LOOP. REST and this receiver share one event loop in `serve`, so decoding and
writing a batch inline held every REST request behind it. Only reading the body stays on the loop;
the rest runs on a small worker pool of its own. That is safe because each batch is admitted by
`TraceStore.append_next`, which allocates the seq and checks the seal in one transaction under the
run's admission lock (#147, #184), so concurrent batches can no longer share a seq or slip past a
seal. The pool is small and separate on purpose: every worker holds a database connection while it
writes, and the REST plane's threads must not queue behind a burst of exports for connections.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Request, Response
from sqlalchemy.exc import IntegrityError, OperationalError

from xorcise.core.otel.decode import (
    RouteResult,
    route_otlp_json,
    route_otlp_logs_json,
    route_otlp_logs_protobuf,
    route_otlp_protobuf,
)
from xorcise.core.otel.ingest.drops import DropRecorder, Signal
from xorcise.core.otel.ports import SealStore, TraceStore
from xorcise.core.otel.store import InMemorySealStore, SqliteLogStore, SqliteTraceStore

log = logging.getLogger(__name__)

# Workers that decode and persist batches. Small, because each holds a database connection while
# it writes, and writers take turns on SQLite anyway: more threads would only queue on the lock.
INGEST_WORKERS = 4
# What an exporter is told to wait before retrying a batch the database was too busy to take.
_RETRY_AFTER_S = 1


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


def _store_unavailable_response(signal: str, exc: OperationalError | IntegrityError) -> Response:
    """The database could not take the batch: locked past the busy timeout, gone, or a write that
    kept clashing after its retries.

    503 is the status OTLP/HTTP exporters retry, with backoff, so the batch is delayed rather than
    lost, where an unhandled error answered 500, which exporters drop. gRPC code 14 = UNAVAILABLE.
    A batch for several runs can be partly stored when this fires, and its retry then stores those
    runs' part twice; that needs a database stalled past its busy timeout, and dropping the batch
    instead would lose evidence for every run in it. Its drops are not recorded until the whole
    batch is settled (`admit_routed`), so the retry does not count them twice.
    """
    log.warning("OTLP %s batch refused, database unavailable: %s", signal, str(exc).splitlines()[0])
    return Response(
        content=json.dumps({"code": 14, "message": "telemetry store unavailable, retry"}),
        media_type="application/json",
        status_code=503,
        headers={"Retry-After": str(_RETRY_AFTER_S)},
    )


def create_otel_app(
    store: TraceStore | None = None,
    seal_store: SealStore | None = None,
    log_store: TraceStore | None = None,
    drops: DropRecorder | None = None,
    on_admitted: Callable[[str], None] | None = None,
) -> FastAPI:
    """The OTLP receiver. *on_admitted* is called with a run id after a batch for it is admitted
    to its record — the role that boots this receiver passes what records the run as started
    (#209), which this part-island may not import. It is called on the ingest worker, once per
    admitted payload, and must not raise; one that does is logged and the batch still stands."""
    trace_store: TraceStore = store if store is not None else SqliteTraceStore()
    logs_store: TraceStore = log_store if log_store is not None else SqliteLogStore()
    seals: SealStore = seal_store if seal_store is not None else InMemorySealStore()
    recorder: DropRecorder = drops if drops is not None else DropRecorder()
    app = FastAPI(title="xorcise-otel", version="1")
    workers = ThreadPoolExecutor(max_workers=INGEST_WORKERS, thread_name_prefix="otlp-ingest")

    async def off_loop(work: Callable[[], Response]) -> Response:
        return await asyncio.get_running_loop().run_in_executor(workers, work)

    def admit_routed(
        signal: Signal, store: TraceStore, result: RouteResult, count: Callable[[str], int]
    ) -> int | Response:
        """Admit every routed payload; return how many items were rejected, or the 503.

        Drops are recorded only once every append has gone through. A 503 makes the exporter send
        the whole batch again, so recording first would count and spool its drops twice.
        """
        late: list[tuple[str, str]] = []
        for run_id, payload in result.routed:
            # `is_sealed` first is only a shortcut for the common late flush. `append_next` is the
            # real check: it refuses a run sealed between the two, atomically with the append.
            try:
                admitted = not seals.is_sealed(run_id) and (
                    store.append_next(run_id, payload) is not None
                )
            except (OperationalError, IntegrityError) as exc:
                return _store_unavailable_response(signal, exc)
            if not admitted:
                late.append((run_id, payload))
            elif on_admitted is not None:
                try:
                    on_admitted(run_id)
                except Exception:  # the batch is stored; a status note must not undo that
                    log.warning("on_admitted failed for run %s", run_id, exc_info=True)
        rejected = result.dropped_spans
        for payload, n in result.unrouted:
            recorder.record(signal=signal, reason="unroutable", n=n, run_id="", payload=payload)
        for run_id, payload in late:
            # late items after terminal are not admitted to the sealed record
            n = count(payload)
            rejected += n
            recorder.record(signal=signal, reason="sealed", n=n, run_id=run_id, payload=payload)
        return rejected

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        # `drops` = what this process refused since start (by signal + reason). Zero across the
        # board is the healthy reading; anything else is a correlation or late-flush problem.
        return {"status": "ok", "service": "otel", "drops": recorder.counters.as_dict()}

    @app.post("/v1/traces")
    async def ingest_traces(request: Request) -> Response:
        raw_body = await request.body()
        content_type = request.headers.get("content-type", "")
        encoding = request.headers.get("content-encoding", "").lower()
        return await off_loop(lambda: _ingest_traces(raw_body, content_type, encoding))

    def _ingest_traces(raw_body: bytes, content_type: str, encoding: str) -> Response:
        # OTLP/HTTP clients MAY gzip the payload (OTel spec; the OpenHands/Laminar exporter does
        # so unconditionally). Decompress before decode — otherwise the raw gzip bytes fail to
        # parse and the export is rejected as malformed. gzip.decompress raises OSError
        # (BadGzipFile) / EOFError on a corrupt body; those fall through to the 400 below.
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
        rejected = admit_routed("traces", trace_store, result, _count_spans)
        if isinstance(rejected, Response):
            return rejected
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
        return await off_loop(lambda: _ingest_logs(raw_body, content_type, encoding))

    def _ingest_logs(raw_body: bytes, content_type: str, encoding: str) -> Response:
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
        rejected = admit_routed("logs", logs_store, result, _count_log_records)
        if isinstance(rejected, Response):
            return rejected
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
