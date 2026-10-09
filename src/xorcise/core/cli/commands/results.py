"""`xorcise leaderboard` — per-agent results roll-up (cli, thin REST client).

CLI half of the Results page's agent leaderboard: fold GET /runs (terminal runs) plus each run's
GET /runs/{id}/result into one row per agent, entirely client-side — no new backend surface.

Scoring mirrors the GUI aggregation (frontend summarize-runs.ts): a PARTIAL run (budget timeout
or operator kill) did not end on the agent's own terms, so it never counts toward the score
aggregates — but it still counts in the run totals and the partial rate. A run cut short by an
environment failure (deploy_failed / crashed) has no score at all and is not one of the agent's
attempts: it counts in the run totals and its own `infra_failed` count, and nowhere else.
"""

from __future__ import annotations

from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import (
    DASH,
    humanize_when,
    print_table,
    ux_table,
)
from xorcise.core.cli.commands.run import judge_degraded
from xorcise.core.cli.rest_client import RestClient
from xorcise.core.contracts.run import COMPLETED_TRIGGERS, PARTIAL_TRIGGERS, UNGRADED_TRIGGERS


def _agent_names(client: RestClient) -> dict[str, str]:
    """agent id → name, so the table reads in operator terms (runs carry only the id)."""
    return {a["id"]: a["name"] for a in client.get("/agents")}


def _flatten(run: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    """One terminal run flattened for the roll-up (the GUI's AgentRunRow)."""
    trigger = run.get("terminal_trigger")
    grade = (result or {}).get("grade") or {}
    # The result view carries the authoritative partial flag; fall back to the run's own trigger
    # (always present) so an ungraded run still classifies.
    partial = (result or {}).get("partial")
    if partial is None:
        # The server's own rule, imported rather than restated: a private copy here listed a
        # "budget" trigger nothing writes and missed "operator", so an operator kill on an
        # ungraded run counted as genuine (#159).
        partial = trigger in PARTIAL_TRIGGERS
    return {
        "agent_id": run.get("agent_id"),
        "overall": grade.get("overall"),
        "partial": bool(partial),
        # A judge that never ran still produces a real number (0.5 * deterministic) which averages
        # in as though it were graded. The roll-up keeps that number — the math is intentional —
        # but carries the condition so the ranking can disclose what it is made of.
        "judge_degraded": judge_degraded(grade),
        "completed": trigger in COMPLETED_TRIGGERS,
        # Cut short by an environment failure: never graded, and not an attempt by the agent.
        "infra_failed": trigger in UNGRADED_TRIGGERS,
        "when": run.get("completed_at") or run.get("created_at") or "",
    }


def summarize_by_agent(rows: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Group flattened runs into one summary per agent, ranked best-average-first.

    Agents with no scored run sink to the bottom; ties break on run count, then name.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["agent_id"]), []).append(row)

    summaries: list[dict[str, Any]] = []
    for agent_id, agent_rows in grouped.items():
        scored = [r["overall"] for r in agent_rows if not r["partial"] and r["overall"] is not None]
        total = len(agent_rows)
        # The rates are over the agent's ATTEMPTS. A run our infrastructure cut short is not one:
        # counting it would lower the agent's completion rate for a failure that is ours (#109).
        # It stays in `runs` and is disclosed as its own count instead.
        infra_failed = sum(1 for r in agent_rows if r.get("infra_failed"))
        attempts = total - infra_failed
        summaries.append(
            {
                "agent_id": agent_id,
                "agent_name": names.get(agent_id, agent_id[:8]),
                "runs": total,
                "scored": len(scored),
                "avg_overall": sum(scored) / len(scored) if scored else None,
                "best_overall": max(scored) if scored else None,
                # How many of the runs IN the aggregates above scored without a judge. Counted
                # over the same cohort as `scored` — not over every row — because the footer
                # tells the reader these are included in Avg/Best, and a partial run is excluded
                # from those. Counting all rows made a timed-out run show as "No judge 1" beside
                # "Avg —", which is the opposite of what the column is for.
                "judge_degraded": sum(
                    1
                    for r in agent_rows
                    if r.get("judge_degraded") and not r["partial"] and r["overall"] is not None
                ),
                "infra_failed": infra_failed,
                "completion_rate": (
                    sum(1 for r in agent_rows if r["completed"]) / attempts if attempts else None
                ),
                "partial_rate": (
                    sum(1 for r in agent_rows if r["partial"]) / attempts if attempts else None
                ),
                "last_run": max((r["when"] for r in agent_rows if r["when"]), default=None),
            }
        )
    summaries.sort(
        key=lambda s: (
            0 if s["avg_overall"] is not None else 1,
            -(s["avg_overall"] or 0.0),
            -s["runs"],
            s["agent_name"],
        )
    )
    return summaries


def _score(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def _rate(value: float | None) -> str:
    return f"{round(value * 100)}%" if value is not None else "—"


@app.command("leaderboard", rich_help_panel="Evaluate")
def leaderboard(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the aggregated rows as JSON (for scripting)."
    ),
) -> None:
    """Rank agents by their recorded results.

    Aggregates every finished run and its recorded result: runs, scored runs, \
average and best overall, completion + partial rate, and the last run. \
Partial runs (budget timeout or operator stop) are excluded from the score \
aggregates but still counted in the totals. Runs cut short by an environment \
failure are never graded: they count in Runs and are shown as Infra failed, \
but are left out of every score and rate.
    """
    client = RestClient()
    runs: list[dict[str, Any]] = client.get("/runs")
    terminal = [r for r in runs if r.get("state") == "terminal"]
    if not terminal:
        # --json is a machine contract: an empty ranking is [], never prose.
        if as_json is True:
            emit_json([])
            return
        console.print("no finished runs yet — nothing to rank")
        return
    rows = [_flatten(r, client.get(f"/runs/{r['run_id']}/result")) for r in terminal]
    summaries = summarize_by_agent(rows, _agent_names(client))
    if as_json is True:
        emit_json(summaries)
        return
    table = ux_table(
        "Agent",
        "Runs",
        "Scored",
        "Avg",
        "Best",
        "Completed",
        "Partial",
        "Infra failed",
        "No judge",
        "Last run",
        title="Leaderboard",
    )
    for s in summaries:
        degraded = int(s.get("judge_degraded") or 0)
        table.add_row(
            s["agent_name"],
            str(s["runs"]),
            str(s["scored"]),
            _score(s["avg_overall"]),
            _score(s["best_overall"]),
            _rate(s["completion_rate"]),
            _rate(s["partial_rate"]),
            str(s["infra_failed"]) if s.get("infra_failed") else DASH,
            f"[warn]{degraded}[/warn]" if degraded else DASH,
            humanize_when(s["last_run"]),
        )
    print_table(table)
    if any(s.get("infra_failed") for s in summaries):
        console.print(
            "[dim]'Infra failed' counts runs cut short by an environment failure — never graded, "
            "and left out of Completed/Partial, which are over the agent's own attempts.[/dim]"
        )
    if any(s.get("judge_degraded") for s in summaries):
        # The scores stay in the averages above, so without this line an agent ranked partly on
        # unjudged runs is indistinguishable from one graded end to end.
        console.print(
            "[dim]'No judge' counts runs scored with the judge half unavailable — those runs "
            "score at most 0.50 and ARE included in Avg/Best.[/dim]"
        )
