"""xorcise.core.reporting — results/report assembly.

LAYER: APPLICATION (domain module). Owns the per-agent result history: results
accrue to a live agent and are cascade-deleted with it.
"""

from __future__ import annotations

from xorcise.core.reporting.render import (
    ReportArtifact,
    RunReportContext,
    agent_model_cell,
    render_html,
    render_markdown,
    report_filename,
    telemetry_window_cell,
)
from xorcise.core.reporting.repository import (
    agent_history,
    delete_for_runs,
    delete_result,
    get_result,
    get_stats,
    held_run_ids,
    put_stats,
    record_result,
    recorded_grades,
    result_conditions,
    result_partial,
)
from xorcise.core.reporting.scores import (
    GradeFacts,
    current_versions,
    score_rows,
    summarize,
    version_label,
)

__all__ = [
    "GradeFacts",
    "ReportArtifact",
    "RunReportContext",
    "agent_history",
    "agent_model_cell",
    "current_versions",
    "delete_for_runs",
    "delete_result",
    "get_result",
    "get_stats",
    "held_run_ids",
    "put_stats",
    "record_result",
    "recorded_grades",
    "render_html",
    "render_markdown",
    "report_filename",
    "result_conditions",
    "result_partial",
    "score_rows",
    "summarize",
    "telemetry_window_cell",
    "version_label",
]
