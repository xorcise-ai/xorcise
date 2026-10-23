"""Run performance: how each finished run counts, and the summaries the GUI's run strip, grader
agreement plot and run ledger, the dashboard and `xorcise leaderboard` draw from (domain module,
pure).

The rules live here and nowhere else (contracts.reporting.ScoreBucket): the median is over runs
graded with a judge, a partly-judged run counting at its lower bound. A run that timed out is in
it, at the score it earned: not finishing within the budget is the agent's failure. A run whose
judge never ran, one an operator stopped, one whose grading crashed, one cut short by an
environment failure and one still grading are each counted and disclosed, never averaged in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import median
from typing import Any, Literal, cast

from xorcise.core.contracts.grading import GradeResult, JudgeStatus
from xorcise.core.contracts.reporting import RunScore, ScoreBucket, ScoreSummary
from xorcise.core.contracts.run import RunEntry

GroupBy = Literal["all", "agent", "mission", "mission_agent"]

# The judge never graded: it contributed 0.0 to `overall` without scoring anything.
_NO_JUDGE = frozenset({"unavailable", "model-not-configured"})

# How a result recorded before `GradeResult.grading_failed` existed says its grading crashed
# (rest.run_terminate's zero fallback). Read only for those older rows.
LEGACY_GRADING_FAILED_PREFIX = "grading failed:"


@dataclass(frozen=True)
class GradeFacts:
    """The parts of a recorded grade the performance views need. Built from the result row's
    columns and a plain read of its stored grade, never a full GradeResult: that carries the
    judge's whole evidence, and the views read every run at once."""

    overall: float
    deterministic: float
    judge: float
    judge_status: JudgeStatus
    judge_upper: float | None = None
    overall_upper: float | None = None
    grading_failed: bool = False
    judge_model: str | None = None

    @classmethod
    def from_detail(
        cls,
        detail: Mapping[str, Any],
        *,
        overall: float,
        deterministic: float,
        judge: float,
        judge_model: str | None,
    ) -> GradeFacts:
        """From a stored grade's JSON object and the row's own score columns."""
        return cls(
            overall=overall,
            deterministic=deterministic,
            judge=judge,
            judge_status=cast("JudgeStatus", detail.get("judge_status") or "ok"),
            judge_upper=detail.get("judge_upper"),
            overall_upper=detail.get("overall_upper"),
            grading_failed=bool(detail.get("grading_failed"))
            or str(detail.get("judge_detail") or "").startswith(LEGACY_GRADING_FAILED_PREFIX),
            judge_model=judge_model,
        )

    @classmethod
    def of(cls, grade: GradeResult, judge_model: str | None = None) -> GradeFacts:
        """From a whole grade in hand."""
        return cls.from_detail(
            grade.model_dump(mode="json"),
            overall=grade.overall,
            deterministic=grade.breakdown.deterministic,
            judge=grade.breakdown.judge,
            judge_model=judge_model,
        )


def version_label(mission_version: str | None, install_revision: int) -> str:
    """The version a run (or an install) is on, as the GUI shows it (`v…`): the creator SemVer,
    else the local install revision. Decided here only: the GUI reads the label the server
    sends rather than rebuilding it."""
    return mission_version or str(install_revision)


def run_version(run: RunEntry) -> str:
    return version_label(run.mission_version, run.install_revision)


def current_versions(runs: Iterable[RunEntry], installed: Mapping[str, str]) -> dict[str, str]:
    """Each mission's current version label: the installed one, else (a mission no longer
    installed) the version of its most recent run. Decide it over EVERY run, never a filtered
    set of them, so a version reads as older, or not, the same on every view."""
    current: dict[str, str] = {}
    for run in sorted(runs, key=lambda r: (r.created_at, r.run_id)):
        current[run.mission] = run_version(run)  # oldest first: the newest run's label sticks
    current.update(installed)
    return current


def bucket(run: RunEntry, grade: GradeFacts | None) -> ScoreBucket:
    """How one finished run counts (contracts.reporting.ScoreBucket)."""
    if run.outcome == "infra_failed":
        return "infra_failed"
    if grade is None:
        return "grading"
    if grade.grading_failed:
        return "grading_failed"
    if run.terminal_trigger == "operator":
        return "stopped"
    if grade.judge_status in _NO_JUDGE:
        return "no_judge"
    return "scored"


def score_rows(
    runs: Iterable[RunEntry],
    grades: Mapping[str, GradeFacts],
    current: Mapping[str, str],
    intel: Mapping[str, int] | None = None,
) -> list[RunScore]:
    """One RunScore per finished run, oldest first.

    `grades` maps a run id to its recorded grade. `current` maps each mission to its current
    version label (`current_versions`); a run on any other label is on an older version, and a
    mission missing from it counts as current. `intel` maps a run id to how many intel it was
    given; absent means none."""
    finished = sorted(
        (r for r in runs if r.outcome is not None), key=lambda r: (r.created_at, r.run_id)
    )
    rows: list[RunScore] = []
    for run in finished:
        grade = grades.get(run.run_id)
        assert run.outcome is not None
        label = run_version(run)
        rows.append(
            RunScore(
                run_id=run.run_id,
                agent_id=run.agent_id,
                mission=run.mission,
                mission_version=label,
                current_version=current.get(run.mission, label) == label,
                created_at=run.created_at,
                completed_at=run.completed_at,
                outcome=run.outcome,
                terminal_trigger=run.terminal_trigger,
                bucket=bucket(run, grade),
                timed_out=run.terminal_trigger == "timeout",
                deterministic=grade.deterministic if grade else None,
                judge=grade.judge if grade else None,
                judge_upper=grade.judge_upper if grade else None,
                judge_status=grade.judge_status if grade else None,
                overall=grade.overall if grade else None,
                overall_upper=grade.overall_upper if grade else None,
                judge_model=grade.judge_model if grade else None,
                intel_disclosed=(intel or {}).get(run.run_id, 0),
            )
        )
    return rows


def _quartiles(values: list[float]) -> tuple[float | None, float | None]:
    """The middle half's edges, by linear interpolation; both None for no values."""
    if not values:
        return (None, None)
    s = sorted(values)

    def at(q: float) -> float:
        pos = (len(s) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    return (at(0.25), at(0.75))


def summarize(
    rows: Iterable[RunScore],
    by: GroupBy,
    agent_names: Mapping[str, str] | None = None,
) -> list[ScoreSummary]:
    """Summarise rows as one group (`all`), per agent, per mission version, or per agent on each
    mission version.

    Groups with an agent come back RANKED, so every client shows one order: best median first, an
    agent with no scored run last, then more scored runs first, then by name (`agent_names`; a
    deleted agent goes by its short id). Mission groups come back in first-seen order, oldest run
    first: how missions are laid out is the view's choice. Per agent on each mission version, the
    versions keep that order and the agents rank within each."""
    groups: dict[tuple[str | None, ...], list[RunScore]] = {}
    for row in rows:
        key: tuple[str | None, ...]
        if by == "all":
            key = (None, None, None)
        elif by == "agent":
            key = (row.agent_id, None, None)
        elif by == "mission":
            key = (None, row.mission, row.mission_version)
        else:
            key = (row.agent_id, row.mission, row.mission_version)
        groups.setdefault(key, []).append(row)

    names = agent_names or {}
    summaries: list[ScoreSummary] = []
    for (agent_id, mission, version), group in groups.items():
        scored = [r for r in group if r.bucket == "scored"]
        overalls = [r.overall for r in scored if r.overall is not None]
        q1, q3 = _quartiles(overalls)
        fully = [r for r in scored if r.judge_status == "ok"]
        gaps = [
            r.judge - r.deterministic
            for r in fully
            if r.judge is not None and r.deterministic is not None
        ]
        dets = [r.deterministic for r in scored if r.deterministic is not None]
        judges = [r.judge for r in fully if r.judge is not None]
        # Only the judges behind the runs in the median: a run left out cannot make the scores
        # the median is made of come from different judges.
        models = sorted({r.judge_model for r in scored if r.judge_model})
        summaries.append(
            ScoreSummary(
                agent_id=agent_id,
                agent_name=(names.get(agent_id) or agent_id[:8]) if agent_id else None,
                mission=mission,
                mission_version=version,
                current_version=group[-1].current_version if mission is not None else None,
                runs=len(group),
                completed=sum(1 for r in group if r.outcome == "completed"),
                scored=len(scored),
                timed_out=sum(1 for r in scored if r.timed_out),
                partial_judge=sum(1 for r in scored if r.judge_status == "partial"),
                assisted=sum(1 for r in scored if r.intel_disclosed > 0),
                no_judge=sum(1 for r in group if r.bucket == "no_judge"),
                stopped=sum(1 for r in group if r.bucket == "stopped"),
                grading_failed=sum(1 for r in group if r.bucket == "grading_failed"),
                infra_failed=sum(1 for r in group if r.bucket == "infra_failed"),
                grading=sum(1 for r in group if r.bucket == "grading"),
                median=median(overalls) if overalls else None,
                mean=sum(overalls) / len(overalls) if overalls else None,
                q1=q1,
                q3=q3,
                best=max(overalls) if overalls else None,
                mean_deterministic=sum(dets) / len(dets) if dets else None,
                mean_judge=sum(judges) / len(judges) if judges else None,
                judge_gap=sum(gaps) / len(gaps) if gaps else None,
                judge_models=tuple(models),
                last_run=max(r.completed_at or r.created_at for r in group),
            )
        )
    if by in ("agent", "mission_agent"):
        # Per agent on each mission version: the versions keep their order, agents rank within.
        order: dict[tuple[str | None, str | None], int] = {}
        for s in summaries:
            order.setdefault((s.mission, s.mission_version), len(order))
        summaries.sort(
            key=lambda s: (
                order[(s.mission, s.mission_version)],
                s.median is None,
                -(s.median or 0.0),
                -s.scored,
                (s.agent_name or "").casefold(),
                s.agent_id or "",
            )
        )
    return summaries
