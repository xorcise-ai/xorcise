"""Per-run agent-events.jsonl export.

Writes a run's NORMALIZED AgentEvent projection to a per-run JSONL file for offline inspection /
an LLM reviewer — one JSON object per line: a `{"type": "header", ...}` line (run + adapter
metadata) then one AgentEvent per line (clean body/kind/title, not raw OTLP). DEBUG/EXPORT ONLY —
never canonical, always rebuildable from RAW; the grader never reads it. Reuses the
events_view read seam (the same projection GET /runs/{id}/events serves), so the file matches the
normalized `agent_events` cache exactly.

Written server-side on run finalization (run_terminate.grade_and_record, best-effort) and on
demand via `xorcise run events export`. The `runs/` tree is durable (not cleared by `down`).
"""

from __future__ import annotations

import json
from pathlib import Path

from xorcise.core.home import xorcise_home


def run_dir(run_id: str) -> Path:
    """The per-run artifact directory: `<home>/runs/<run_id>`."""
    return xorcise_home() / "runs" / run_id


def default_export_path(run_id: str) -> Path:
    """`<home>/runs/<run_id>/agent-events.jsonl`."""
    return run_dir(run_id) / "agent-events.jsonl"


def export_run_events(run_id: str, out: Path | None = None) -> Path:
    """Write the run's normalized AgentEvent projection to JSONL; return the path written.

    Rebuilt from RAW via the events_view read seam (identical to GET /runs/{id}/events). A header
    line carries run + adapter metadata; each subsequent line is one `AgentEvent` (model_dump_json).
    """
    # Lazy: keep the otel display plane off this module's import path (plane-isolation invariant).
    from xorcise.core.rest import events_view

    view = events_view._full_view(run_id)
    path = out if out is not None else default_export_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "header",
        "run_id": view.run_id,
        "source_agent": view.source_agent,
        "adapter_name": view.adapter_name,
        "adapter_version": view.adapter_version,
        "fallback": view.fallback,
        "next_since": view.next_since,
        "next_cursor": view.next_cursor.model_dump(mode="json"),
        "counts": dict(view.counts),
        "event_count": len(view.events),
    }
    lines = [json.dumps(header)]
    lines.extend(event.model_dump_json() for event in view.events)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
