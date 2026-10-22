"""Run performance: every finished run as the performance views draw it, and their summaries.

A cross-module join the delivery layer owns: the runs module knows how each run ended, reporting
knows its grade and how it counts, the missions island knows which version is installed. The GUI's
run strip, grader agreement plot and run ledger read these two routes instead of one `/result`
per run, and none of them counts a run on its own (reporting.scores)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from xorcise.core import reporting, runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.reporting import RunScore, ScoreSummary
from xorcise.core.missions.runtime import get_installed
from xorcise.core.reporting.scores import GroupBy

router = APIRouter(prefix="/results", tags=["results"])


def _score_rows(agent: str | None, mission: str | None) -> list[RunScore]:
    entries = [
        r
        for r in runs.list_runs()
        if (agent is None or r.agent_id == agent) and (mission is None or r.mission == mission)
    ]
    root = Path(get_settings().missions_root)
    installed: dict[str, str] = {}
    for slug in {r.mission for r in entries}:
        if (ic := get_installed(slug, root)) is not None:
            installed[slug] = ic.mission_version or str(ic.install_revision)
    return reporting.score_rows(entries, reporting.recorded_grades(agent), installed)


@router.get("/runs", response_model=list[RunScore])
def run_scores(agent: str | None = None, mission: str | None = None) -> list[RunScore]:
    """Every finished run, oldest first, with its scores and how it counts (`bucket`).

    `agent` (an agent id) and `mission` (a mission slug) narrow it. A run still in progress is
    not listed; one still being graded is, as `grading`."""
    return _score_rows(agent, mission)


@router.get("/summary", response_model=list[ScoreSummary])
def score_summary(
    by: GroupBy = "agent", agent: str | None = None, mission: str | None = None
) -> list[ScoreSummary]:
    """Finished runs summarised per agent, per mission version (`mission`), or per agent on each
    mission version (`mission_agent`).

    The median is over runs that finished on the agent's own terms and were graded with a judge,
    a partly-judged run counting at its lower bound. Runs with no judge, runs that ended early,
    environment failures and runs still grading are counted separately and never in it."""
    return reporting.summarize(_score_rows(agent, mission), by)
