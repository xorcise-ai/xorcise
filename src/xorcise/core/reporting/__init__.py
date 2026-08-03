"""xorcise.core.reporting — results/report assembly.

LAYER: APPLICATION (domain module). Owns the per-agent result history: results
accrue to a live agent and are cascade-deleted with it.
"""

from __future__ import annotations

from xorcise.core.reporting.render import (
    ReportArtifact,
    RunReportContext,
    render_html,
    render_markdown,
    report_filename,
)
from xorcise.core.reporting.repository import (
    agent_history,
    delete_for_agent,
    delete_result,
    get_result,
    get_stats,
    record_result,
    result_conditions,
    result_partial,
)

__all__ = [
    "ReportArtifact",
    "RunReportContext",
    "agent_history",
    "delete_for_agent",
    "delete_result",
    "get_result",
    "get_stats",
    "record_result",
    "render_html",
    "render_markdown",
    "report_filename",
    "result_conditions",
    "result_partial",
]
