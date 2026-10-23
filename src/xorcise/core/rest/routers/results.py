"""Run performance: every finished run as the performance views draw it, and their summaries.

A cross-module join the delivery layer owns: the runs module knows how each run ended, reporting
knows its grade and how it counts, the missions island knows which version is installed. The GUI's
run strip, grader agreement plot and run ledger read these two routes instead of one `/result`
per run, and none of them counts a run on its own (reporting.scores)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks

from xorcise.core import agents, reporting, runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.reporting import RunScore, ScoreSummary
from xorcise.core.missions.runtime import get_installed
from xorcise.core.reporting.scores import GroupBy
from xorcise.core.runcontrol.store import disclosed_intel_counts

log = logging.getLogger(__name__)

router = APIRouter(prefix="/results", tags=["results"])


def _installed_versions(missions: set[str]) -> dict[str, str]:
    """The installed version label of each mission. One whose install record cannot be read is
    left out (its newest run's version stands in), never an error for every view."""
    root = Path(get_settings().missions_root)
    installed: dict[str, str] = {}
    for slug in missions:
        try:
            ic = get_installed(slug, root)
        except (OSError, ValueError) as exc:
            log.warning("install record for mission %s cannot be read (%s); skipped", slug, exc)
            continue
        if ic is not None:
            installed[slug] = reporting.version_label(ic.mission_version, ic.install_revision)
    return installed


def _score_rows(
    agent: str | None,
    mission: str | None,
    schedule: Callable[[Callable[[], None]], None] | None = None,
) -> list[RunScore]:
    every = runs.list_runs()
    # Which version is current is decided over every run, so a version reads as older, or not,
    # the same however the list is narrowed.
    current = reporting.current_versions(every, _installed_versions({r.mission for r in every}))
    entries = [
        r
        for r in every
        if (agent is None or r.agent_id == agent) and (mission is None or r.mission == mission)
    ]
    rows = reporting.score_rows(
        entries, reporting.recorded_grades(agent), current, disclosed_intel_counts()
    )
    if schedule is not None:
        # A run still `grading` here may have lost its grade to a restart or a hung judge: re-drive
        # it, as a poll of its `/result` does (idempotent and de-duplicated), so the views that
        # poll these routes heal it rather than wait on it for good.
        from xorcise.core.rest.run_terminate import ensure_graded_async

        for row in rows:
            if row.bucket == "grading":
                ensure_graded_async(row.run_id, schedule)
    return rows


@router.get("/runs", response_model=list[RunScore])
def run_scores(
    background: BackgroundTasks, agent: str | None = None, mission: str | None = None
) -> list[RunScore]:
    """Every finished run, oldest first, with its scores and how it counts (`bucket`).

    `agent` (an agent id) and `mission` (a mission slug) narrow it. A run still in progress is
    not listed; one still being graded is, as `grading`."""
    return _score_rows(agent, mission, background.add_task)


@router.get("/summary", response_model=list[ScoreSummary])
def score_summary(
    background: BackgroundTasks,
    by: GroupBy = "agent",
    agent: str | None = None,
    mission: str | None = None,
) -> list[ScoreSummary]:
    """Finished runs summarised as one group (`all`), per agent, per mission version (`mission`),
    or per agent on each mission version (`mission_agent`).

    The median is over runs graded with a judge, a partly-judged run counting at its lower bound.
    A run that timed out is in it, at the score it earned. Runs with no judge, runs an operator
    stopped, runs whose grading crashed, environment failures and runs still grading are counted
    separately and never in it. `mean` is over the same runs as the median.

    Groups with an agent come back ranked (best median first) and named (`agent_name`), so every
    client shows one order under one set of names."""
    names = {a.id: a.name for a in agents.list_agents()}
    return reporting.summarize(_score_rows(agent, mission, background.add_task), by, names)
