"""OTLP/HTTP JSON + protobuf decode + run routing (PART-ISLAND helper, pure).

Routes each ResourceSpans to its run: PRIMARY = the xorcise.run_id resource
attribute; FALLBACK = a `xorcise.run_id=<id>` sentinel grepped out of the span
content (the mission prompt the agent echoes), so the harness needs no per-run
OTel env. Supports JSON (proto3 JSON mapping) and OTLP/HTTP protobuf.
The proto path lazy-imports opentelemetry-proto (the ``collector`` extra) so
this module is importable without that dep. stdlib + a lazy 3rd-party proto import
(no internal imports).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

RUN_ID_ATTR = "xorcise.run_id"
# Fallback: the literal `xorcise.run_id=<id>` marker the mission prompt carries.
# The structured-attribute form serializes as `"key":"xorcise.run_id","value":...`
# (no `=value`), so this never matches the primary channel by accident.
_SENTINEL_RE = re.compile(r"xorcise\.run_id=([0-9A-Za-z_-]+)")


@dataclass(frozen=True)
class RouteResult:
    """Per-run RAW payloads + span tallies for one OTLP export request."""

    routed: tuple[tuple[str, str], ...]  # (run_id, RAW {"resourceSpans": [rs]} JSON)
    accepted_spans: int
    dropped_spans: int


def _run_id_of(resource_spans: dict[str, Any]) -> str:
    """Primary: the xorcise.run_id resource attribute, or '' if absent."""
    resource = resource_spans.get("resource") or {}
    for attr in resource.get("attributes") or []:
        if attr.get("key") == RUN_ID_ATTR:
            run_id = (attr.get("value") or {}).get("stringValue", "")
            return run_id if isinstance(run_id, str) else ""
    return ""


def _run_id_from_content(resource_spans_json: str) -> str:
    """Fallback: the xorcise.run_id=<id> sentinel anywhere in the span content
    (the echoed mission prompt). Best-effort; '' if absent."""
    match = _SENTINEL_RE.search(resource_spans_json)
    return match.group(1) if match else ""


def _span_count(resource_spans: dict[str, Any]) -> int:
    return sum(len(scope.get("spans") or []) for scope in resource_spans.get("scopeSpans") or [])


def _route_parsed(payload: dict[str, Any]) -> RouteResult:
    """Route each ResourceSpans in an already-parsed OTLP payload dict."""
    resource_spans = payload.get("resourceSpans") or []
    routed: list[tuple[str, str]] = []
    accepted = 0
    dropped = 0
    for rs in resource_spans:
        count = _span_count(rs)
        raw = json.dumps({"resourceSpans": [rs]})
        run_id = _run_id_of(rs) or _run_id_from_content(raw)
        if run_id:
            routed.append((run_id, raw))
            accepted += count
        else:
            dropped += count
    return RouteResult(routed=tuple(routed), accepted_spans=accepted, dropped_spans=dropped)


def route_otlp_json(body: str) -> RouteResult:
    """Route each ResourceSpans by run_id (attr → prompt sentinel); ValueError on bad JSON."""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        payload = {}
    return _route_parsed(payload)


def route_otlp_protobuf(body: bytes) -> RouteResult:
    """Decode an OTLP/HTTP protobuf body and route by run_id.

    Lazily imports opentelemetry-proto (the ``collector`` extra). Raises
    ``RuntimeError`` if the extra is absent and ``ValueError`` on a protobuf
    decode failure (handler maps it to 400).
    """
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )
    except ImportError as exc:
        raise RuntimeError(
            "opentelemetry-proto is not installed; "
            "install xorcise with the 'collector' extra: "
            "Reinstall it: pip install --force-reinstall xorcise"
        ) from exc

    try:
        req = ExportTraceServiceRequest.FromString(body)
    except Exception as exc:
        raise ValueError(f"malformed OTLP protobuf body: {exc}") from exc

    # preserving_proto_field_name=False → camelCase keys (resourceSpans, scopeSpans,
    # stringValue) — the exact shape _route_parsed expects.
    payload: dict[str, Any] = MessageToDict(req, preserving_proto_field_name=False)
    return _route_parsed(payload)


# ── OTLP LOGS signal — the peer of the traces routers above. Same run routing
#    (xorcise.run_id resource attr → prompt sentinel), applied to ResourceLogs / logRecords.


def _log_count(resource_logs: dict[str, Any]) -> int:
    return sum(len(scope.get("logRecords") or []) for scope in resource_logs.get("scopeLogs") or [])


def _route_parsed_logs(payload: dict[str, Any]) -> RouteResult:
    """Route each ResourceLogs by run_id — identical channels to spans (_run_id_of reads the shared
    `resource` block; the sentinel fallback greps the raw). accepted/dropped count log records."""
    resource_logs = payload.get("resourceLogs") or []
    routed: list[tuple[str, str]] = []
    accepted = 0
    dropped = 0
    for rl in resource_logs:
        count = _log_count(rl)
        raw = json.dumps({"resourceLogs": [rl]})
        run_id = _run_id_of(rl) or _run_id_from_content(raw)
        if run_id:
            routed.append((run_id, raw))
            accepted += count
        else:
            dropped += count
    return RouteResult(routed=tuple(routed), accepted_spans=accepted, dropped_spans=dropped)


def route_otlp_logs_json(body: str) -> RouteResult:
    """Route each ResourceLogs by run_id (attr → prompt sentinel); ValueError on bad JSON."""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        payload = {}
    return _route_parsed_logs(payload)


def route_otlp_logs_protobuf(body: bytes) -> RouteResult:
    """Decode an OTLP/HTTP logs protobuf body and route by run_id.

    Lazily imports opentelemetry-proto (the ``collector`` extra). Raises ``RuntimeError`` if the
    extra is absent and ``ValueError`` on a protobuf decode failure (handler maps it to 400).
    """
    try:
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )
    except ImportError as exc:
        raise RuntimeError(
            "opentelemetry-proto is missing — this XORCISE install is incomplete. "
            "Reinstall it: pip install --force-reinstall xorcise"
        ) from exc

    try:
        req = ExportLogsServiceRequest.FromString(body)
    except Exception as exc:
        raise ValueError(f"malformed OTLP logs protobuf body: {exc}") from exc

    payload: dict[str, Any] = MessageToDict(req, preserving_proto_field_name=False)
    return _route_parsed_logs(payload)
