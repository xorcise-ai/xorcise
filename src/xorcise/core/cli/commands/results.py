"""`xorcise leaderboard` — per-agent results roll-up (cli, thin REST client).

CLI half of the Results page's agent leaderboard: fold GET /runs (terminal runs) plus each run's
GET /runs/{id}/result into one row per agent, entirely client-side — no new backend surface.

Scoring mirrors the GUI aggregation (frontend summarize-runs.ts): a PARTIAL run (timeout
/ budget / operator kill) did not end on the agent's own terms, so it never counts toward the
score aggregates — but it still counts in the run totals and the partial rate.
"""

from __future__ import annotations

from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json
from xorcise.core.cli._ux import humanize_when, print_table, ux_table
from xorcise.core.cli.rest_client import RestClient

# How a terminal run ended, per the run-control vocabulary (mirrors the GUI's run-state map).
_PARTIAL_TRIGGERS = frozenset({"timeout", "budget"})
_COMPLETED_TRIGGERS = frozenset({"done", "completed"})


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
        partial = trigger in _PARTIAL_TRIGGERS
    return {
        "agent_id": run.get("agent_id"),
        "overall": grade.get("overall"),
        "partial": bool(partial),
        "completed": trigger in _COMPLETED_TRIGGERS,
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
        summaries.append(
            {
                "agent_id": agent_id,
                "agent_name": names.get(agent_id, agent_id[:8]),
                "runs": total,
                "scored": len(scored),
                "avg_overall": sum(scored) / len(scored) if scored else None,
                "best_overall": max(scored) if scored else None,
                "completion_rate": (
                    sum(1 for r in agent_rows if r["completed"]) / total if total else None
                ),
                "partial_rate": (
                    sum(1 for r in agent_rows if r["partial"]) / total if total else None
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
Partial runs (timeout / budget / kill) are excluded from the score \
aggregates but still counted in the totals.
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
        "Last run",
        title="Leaderboard",
    )
    for s in summaries:
        table.add_row(
            s["agent_name"],
            str(s["runs"]),
            str(s["scored"]),
            _score(s["avg_overall"]),
            _score(s["best_overall"]),
            _rate(s["completion_rate"]),
            _rate(s["partial_rate"]),
            humanize_when(s["last_run"]),
        )
    print_table(table)
