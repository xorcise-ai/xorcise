"""`xorcise leaderboard` — per-agent results roll-up (cli, thin REST client).

The CLI half of the Results page's agent cards: one row per agent from GET /results/summary, the
server's own summary (reporting.scores), so the CLI, the GUI and the dashboard cannot rank the
same runs differently.

The headline is the MEDIAN over runs graded with a judge, a partly-judged run counting at its
lower bound. A run that timed out is in it, at the score it earned: not finishing within the
budget is the agent's failure. A run whose judge never ran, one an operator stopped, one cut short
by an environment failure and one still grading are each counted in their own column, never in it.
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


def _agent_names(client: RestClient) -> dict[str, str]:
    """agent id → name, so the table reads in operator terms (summaries carry only the id)."""
    return {a["id"]: a["name"] for a in client.get("/agents")}


def rank(summaries: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Name each agent's summary and rank them: best median first; an agent with no scored run
    has no median and sinks; ties go to more scored runs, then the name. A deleted agent's runs
    keep its short id, as the GUI shows them."""
    named = [
        {**s, "agent_name": names.get(str(s["agent_id"]), str(s["agent_id"])[:8])}
        for s in summaries
    ]
    named.sort(
        key=lambda s: (
            0 if s["median"] is not None else 1,
            -(s["median"] or 0.0),
            -s["scored"],
            s["agent_name"],
        )
    )
    return named


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
runs an operator stopped (Stopped), runs cut short by an environment failure (Infra failed) and \
runs still grading are counted in their own columns and never in the median.
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
    ranked = rank(summaries, _agent_names(client))
    if as_json is True:
        emit_json(ranked)
        return
    table = ux_table(
        "Agent",
        "Runs",
        "Scored",
        "Median",
        "Best",
        "Timed out",
        "Stopped",
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
            _score(s["best"]),
            _count(s["timed_out"]),
            _count(s["stopped"]),
            _count(s["infra_failed"]),
            _count(s["no_judge"], "warn"),
            humanize_when(s["last_run"]),
        )
    print_table(table)
    notes = []
    if any(s["timed_out"] for s in ranked):
        notes.append(
            "'Timed out' runs ran out of their budget. They ARE in Median/Best, at the score "
            "they earned: not finishing in time is the agent's failure."
        )
    if any(s["no_judge"] for s in ranked):
        notes.append(
            "'No judge' counts runs graded with the judge half unavailable. Half of each score is "
            "unmeasured, so they are left out of Median/Best."
        )
    if any(s["stopped"] for s in ranked):
        notes.append("'Stopped' counts runs an operator ended. Left out of Median/Best.")
    if any(s["infra_failed"] for s in ranked):
        notes.append(
            "'Infra failed' counts runs cut short by an environment failure: never graded, and "
            "not the agent's attempts."
        )
    for note in notes:
        console.print(f"[dim]{note}[/dim]")
