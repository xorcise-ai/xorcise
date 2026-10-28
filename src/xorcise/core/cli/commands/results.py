"""`xorcise leaderboard` — per-agent results roll-up (cli, thin REST client).

The CLI half of the Results page's agent cards: one row per agent from GET /results/summary, the
server's own summary (reporting.scores), so the CLI, the GUI and the dashboard cannot rank the
same runs differently.

The headline is the MEDIAN over runs graded with a judge, a partly-judged run counting at its
lower bound. A run that timed out is in it, at the score it earned: not finishing within the
budget is the agent's failure. A run whose judge never ran, one an operator stopped, one whose
grading crashed, one cut short by an environment failure and one still grading are each counted in
their own column, never in it. The mean beside it is over the same runs.

`--json` emits the server's summaries, ranked, plus the keys it carried before the median
(`avg_overall`, `best_overall`, `completion_rate`, `partial_rate`, `judge_degraded`), each computed
by the rule above.
"""

from __future__ import annotations

import math
from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import (
    DASH,
    humanize_when,
    print_table,
    ux_table,
)
from xorcise.core.cli.rest_client import RestClient


def with_compat_keys(summary: dict[str, Any]) -> dict[str, Any]:
    """The server's summary plus the keys `--json` carried before the median (v0.2.0), so a
    script reading them keeps working. Each is computed by the current rule: the mean and best are
    over the same runs as the median (timed-out runs included; no-judge, stopped, grading-failed
    and environment-failed runs not), and the rates are over the agent's attempts, an environment
    failure not being one (#109).

    A server older than this CLI may not send `mean` or `grading_failed`: they read as absent
    (None, 0) rather than failing the command."""
    summary = {"mean": None, "grading_failed": 0, **summary}
    attempts = summary["runs"] - summary["infra_failed"]
    partial = attempts - summary["completed"]
    return {
        **summary,
        "avg_overall": summary["mean"],
        "best_overall": summary["best"],
        "judge_degraded": summary["no_judge"],
        "completion_rate": summary["completed"] / attempts if attempts else None,
        "partial_rate": partial / attempts if attempts else None,
    }


def _score(value: float | None) -> str:
    """A score as the GUI prints it: a whole percentage, half up (JavaScript's Math.round). Two
    decimals of the fraction disagreed with the page on the same number: 0.815 is stored just
    under 0.815, so `.2f` printed 0.81 where the page showed 82%."""
    return f"{math.floor(value * 100 + 0.5)}%" if value is not None else "—"


def _count(value: int, style: str = "") -> str:
    if not value:
        return DASH
    return f"[{style}]{value}[/{style}]" if style else str(value)


@app.command("leaderboard", rich_help_panel="Evaluate")
def leaderboard(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the ranked summaries as JSON (for scripting)."
    ),
) -> None:
    """Rank agents by the median score of their finished runs.

    Runs that timed out count, at the score they earned. Runs whose judge never ran (No judge), \
runs an operator stopped (Stopped), runs whose grading crashed (Grading failed), runs cut short by \
an environment failure (Infra failed) and runs still grading are counted in their own columns and \
never in the median. Mean is over the same runs as the median.
    """
    client = RestClient()
    summaries: list[dict[str, Any]] = client.get("/results/summary?by=agent")
    if not summaries:
        # --json is a machine contract: an empty ranking is [], never prose.
        if as_json is True:
            emit_json([])
            return
        console.print("no finished runs yet — nothing to rank")
        return
    # Already ranked and named by the server, the same order the GUI shows.
    ranked = [with_compat_keys(s) for s in summaries]
    if as_json is True:
        emit_json(ranked)
        return
    table = ux_table(
        "Agent",
        "Runs",
        "Scored",
        "Median",
        "Mean",
        "Best",
        "Timed out",
        "Stopped",
        "Grading failed",
        "Infra failed",
        "No judge",
        "Last run",
        title="Leaderboard",
    )
    for s in ranked:
        table.add_row(
            s["agent_name"],
            str(s["runs"]),
            str(s["scored"]),
            _score(s["median"]),
            _score(s["mean"]),
            _score(s["best"]),
            _count(s["timed_out"]),
            _count(s["stopped"]),
            _count(s["grading_failed"], "warn"),
            _count(s["infra_failed"]),
            _count(s["no_judge"], "warn"),
            humanize_when(s["last_run"]),
        )
    print_table(table)
    notes = []
    if any(s["timed_out"] for s in ranked):
        notes.append(
            "'Timed out' runs ran out of their budget. They ARE in Median/Mean/Best, at the "
            "score they earned: not finishing in time is the agent's failure."
        )
    if any(s["no_judge"] for s in ranked):
        notes.append(
            "'No judge' counts runs graded with the judge half unavailable. Half of each score is "
            "unmeasured, so they are left out of Median/Mean/Best."
        )
    if any(s["stopped"] for s in ranked):
        notes.append("'Stopped' counts runs an operator ended. Left out of Median/Mean/Best.")
    if any(s["grading_failed"] for s in ranked):
        notes.append(
            "'Grading failed' counts runs whose grading crashed, so nothing graded them. Left out "
            "of Median/Mean/Best; `xorcise run regrade` grades one again."
        )
    if any(s["infra_failed"] for s in ranked):
        notes.append(
            "'Infra failed' counts runs cut short by an environment failure: never graded, and "
            "not the agent's attempts."
        )
    for note in notes:
        console.print(f"[dim]{note}[/dim]")
