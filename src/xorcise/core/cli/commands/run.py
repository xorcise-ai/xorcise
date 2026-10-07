"""`xorcise run` group — thin REST client (cli)."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape

from xorcise.core.cli._pull import pull_to_terminal
from xorcise.core.cli._resolve import (
    agent_names_by_id,
    mission_names_by_id,
    resolve_agent_name,
    resolve_mission,
    resolve_run_id,
)
from xorcise.core.cli._shared import EXIT_CODES_EPILOG, app, console, emit_json, err_console
from xorcise.core.cli._ux import (
    COMPLETED_TRIGGERS,
    DASH,
    confirm_or_abort,
    fail,
    fmt_score,
    humanize_when,
    kind_label,
    next_step,
    print_table,
    run_state_markup,
    short_id,
    ux_table,
)
from xorcise.core.cli.rest_client import DocumentUnavailable, RestClient
from xorcise.core.reporting.render import _SEAL_MISMATCH, agent_model_line

run_app = typer.Typer(
    help="Create and manage evaluation runs.",
    no_args_is_help=True,
    epilog=EXIT_CODES_EPILOG,
)
app.add_typer(run_app, name="run", rich_help_panel="Evaluate")

# `xorcise run events …` — per-run AgentEvent projection tools (thin REST clients, like the
# rest of this module).
run_events_app = typer.Typer(
    help="Per-run AgentEvent export (normalized projection, fetched from the server).",
    no_args_is_help=True,
)
run_app.add_typer(run_events_app, name="events")

# Poll cadence + cap for waiting out the async grading window. The cap comfortably
# exceeds the judge timeout (model_timeout_seconds, default 120s) plus slack.
_GRADE_POLL_SECONDS = 2.0
_GRADE_POLL_CAP_SECONDS = 240.0

_RUN_ID_HELP = "Run id or unique prefix (see: xorcise run list)."


class ReportFormat(StrEnum):
    md = "md"
    html = "html"


# Module-level singleton (B008): a call in an argument default would re-run per import.
_FORMAT_OPTION = typer.Option(ReportFormat.md, "--format", help="Report format.")


def _resolve_id(client: RestClient, given: str) -> str:
    """Full 32-char ids pass through untouched (no extra call); shorter inputs are
    resolved as unique prefixes against the live run list. An empty id is a usage
    error, never a prefix that silently matches everything."""
    if given.strip() and len(given) >= 32:
        return given
    return resolve_run_id(client, given)


def _agent_model_line(cond: dict[str, Any], observed: Sequence[str], dropped: int = 0) -> str:
    """The CLI's view of `reporting.render.agent_model_line` — see there for the rule.

    Shared rather than mirrored: this used to be a second copy and the two drifted, so `run status`
    printed a bare declared name where report.md printed "… (disclosed)" (#128 review).
    """
    return agent_model_line(str(cond.get("model") or ""), observed, dropped)


def _render_telemetry(telemetry: dict[str, Any] | None) -> None:
    """The run's telemetry honesty block: which renderer, how much content, and every warning.
    Silent when the server has no telemetry summary (older server, or no trace at all)."""
    if not telemetry:
        return
    counts = telemetry.get("counts") or {}
    renderer = str(telemetry.get("adapter_name") or "generic")
    if telemetry.get("fallback"):
        renderer += " (generic renderer — no harness-specific adapter)"
    console.print(
        f"telemetry: renderer {escape(renderer)} · "
        f"spans {counts.get('spans', 0)} ({counts.get('content_spans', 0)} with content) · "
        f"logs {counts.get('logs', 0)} ({counts.get('content_logs', 0)} with content)"
    )
    for w in telemetry.get("warnings") or []:
        console.print(f"[warn]warning[/]: {escape(str(w.get('message', '')))}")


def _render_evidence_seal(r: dict[str, Any]) -> None:
    """The run's evidence seal, printed where the score is read.

    Branches on `evidence_status` throughout — every one of its seven values has an answer here,
    and none falls through to a verdict field. An earlier version read `evidence_verified` for
    everything past the first three, so a healthy sealed run fetched WITHOUT `?verify=1` came back
    `recorded` with a null verdict and printed "could not verify it" — on every normal run.

    /result has carried `evidence_digest` and `evidence_verified` since #116 and this view
    printed neither, so the digest reached an operator only as prose inside report.md — a grade
    tied to its evidence for a reader of the report and for nobody looking at `run status`.

    Silent on a run whose /result carries no digest (sealed before the feature, or never
    sealed): a line reading "unknown" on every old run trains people to ignore it, which is the
    opposite of the point. The report's Conditions table drops its row for that reason too — but
    it is NOT quite the report's rule. The report gates on having a digest OR a recorded failure
    to produce one; this gates on `evidence_status`, which /result now carries, so the two agree
    on every case the status can name — including a run whose sealing failed to hash, which used
    to be explicit in the report and silent here.

    Short-form digest for the same reason the report uses one: enough to compare two views of a
    run by eye, with the full value in the seal store for an actual verification.

    `verified` is a TRISTATE, and null is never an accusation: a build that cannot re-derive the
    scheme has not found a mismatch.
    """
    status = str(r.get("evidence_status") or "none")
    if status == "none":
        return  # unsealed, or sealed before digests existed — a line here would say nothing
    if status == "unavailable":
        # Sealed, but hashing it failed at seal time. The report has always said this; without
        # `evidence_status` on /result this surface could only fall silent, which read as a run
        # that simply predates the feature.
        console.print(
            "[warn]evidence seal: sealed, but its evidence could not be hashed[/] — "
            "this run cannot be checked against its evidence"
        )
        return
    if status == "unreadable":
        console.print(
            "[warn]evidence seal: the seal could not be read just now[/] — "
            "treat as unknown, not altered"
        )
        return
    digest = str(r.get("evidence_digest") or "")
    if not digest:
        return
    short = f"{digest[:16]}…"
    if status == "recorded":
        # NOT "could not verify": nobody asked. /result re-hashes only on ?verify=1, because the
        # hash is proportional to the run's telemetry and the sweeping callers fetch this once per
        # run. `run status` does ask, so this branch is what a reader sees from a surface that
        # deliberately did not — `run export`'s bundled result.json, today.
        console.print(
            f"evidence seal: {short} recorded — not checked here; "
            "`xorcise run status <id>` re-checks it against the evidence"
        )
        return
    verified = r.get("evidence_verified")
    if verified is True:
        console.print(
            f"evidence seal: {short} verified — the graded evidence is unchanged since sealing"
        )
    elif verified is False:
        # The report's own sentence, imported rather than re-worded for the same reason
        # agent_model_line is: two spellings of "this run's evidence changed" is how two
        # surfaces start disagreeing about the same run (#128 review).
        console.print(f"[err]evidence seal: {short} MISMATCH[/] — {escape(_SEAL_MISMATCH)}")
    else:
        console.print(
            f"evidence seal: {short} recorded, but this build could not verify it — "
            "treat as unknown, not altered"
        )


def _render_result(
    r: dict[str, Any], *, verbose: bool = False, telemetry: dict[str, Any] | None = None
) -> None:
    """Render a graded result envelope; shared by run status + run terminate."""
    grade, cond = r["grade"], r["conditions"]
    judge_lower = float(grade["breakdown"]["judge"])
    judge_upper_raw = grade.get("judge_upper")
    judge_upper = float(judge_lower if judge_upper_raw is None else judge_upper_raw)
    overall_lower = float(grade["overall"])
    overall_upper_raw = grade.get("overall_upper")
    overall_upper = float(overall_lower if overall_upper_raw is None else overall_upper_raw)

    def score_range(lower: float, upper: float) -> str:
        if upper > lower + 1e-9:
            return f"{fmt_score(lower)}–{fmt_score(upper)}"
        return fmt_score(lower)

    console.print(
        f"overall={score_range(overall_lower, overall_upper)}"
        f"  deterministic={fmt_score(grade['breakdown']['deterministic'])}"
        f"  judge={score_range(judge_lower, judge_upper)}"
    )
    if grade.get("judge_status") == "partial":
        coverage = float(grade.get("judge_coverage") or 0.0)
        console.print(
            f"[yellow]PARTIAL JUDGE[/] — {coverage:.0%} of rubric weight scored; "
            "shown ranges include unscored criteria"
        )
    elif judge_degraded(grade):
        # The judge never ran, so its half contributed 0.0 — by design, but a full deterministic
        # solve then reads as a flat 0.50 and looks like "half solved" rather than "checks passed,
        # judge unavailable". report.md always said so; this is the same sentence, here.
        detail = str(grade.get("judge_detail") or "").strip()
        console.print(
            f"[yellow]JUDGE DEGRADED[/] — {escape(str(grade['judge_status']))}: the judge half "
            "did not run and contributed 0.00 to overall"
            + (f" ({escape(detail)})" if detail else "")
        )
    # Interpolated evidence/deduction text is server/LLM-authored — escape it so a
    # stray `[...]` never gets interpreted as Rich markup.
    if grade.get("hard_fails"):
        console.print(f"[err]HARD-FAIL[/]: {escape(', '.join(grade['hard_fails']))}")
    if grade.get("key_evidence"):
        console.print("evidence: " + escape("; ".join(grade["key_evidence"])))
    if grade.get("major_deductions"):
        console.print("deductions: " + escape("; ".join(grade["major_deductions"])))
    if grade.get("artifacts"):
        console.print("artifacts: " + escape(", ".join(grade["artifacts"])))
    if grade.get("trace_ref"):
        console.print(f"trace: {escape(str(grade['trace_ref']))}")
    # Disclosed conditions travel with the result.
    console.print(
        "model: "
        + escape(
            _agent_model_line(
                cond,
                r.get("models_reported") or [],
                int(r.get("models_reported_truncated") or 0),
            )
        )
    )
    console.print(
        f"judge model: {escape(str(cond.get('judge_model') or 'judge model not configured'))}"
    )
    console.print(f"budget: {cond.get('budget_seconds', 0)}s")
    console.print(f"sandbox: {escape(str(cond.get('sandbox_ref') or '—'))}")
    _render_evidence_seal(r)
    _render_telemetry(telemetry)
    # Partial banner — only shown when the result was graded on incomplete data.
    if r.get("partial"):
        trig = r.get("partial_trigger") or "partial"
        console.print(
            f"[bold yellow]⚠ PARTIAL[/] — result graded on incomplete data (trigger: {trig})"
        )
    if verbose:
        # The per-check (deterministic) + per-criterion (judge) detail. Both arrays
        # are already in the REST response; the default view stays compact.
        checks = grade.get("check_breakdown") or []
        if checks:
            console.print("\n[bold]deterministic checks[/]")
            for c in checks:
                mark = "[ok]PASS[/]" if c.get("passed") else "[err]FAIL[/]"
                console.print(
                    f"  {mark} {c['id']} (w={c.get('weight')}): {c.get('op')} {c.get('value')}"
                )
        criteria = grade.get("judge_breakdown") or []
        if criteria:
            console.print("\n[bold]judge rubric[/]")
            for c in criteria:
                status = c.get("status", "ok")
                score = c.get("score") if status == "ok" else status
                head = f"  [{score}] {c['criterion_id']} (w={c.get('weight')})"
                console.print(escape(f"{head}: {c.get('text')}"))
                if c.get("reason"):
                    console.print(escape(f"      ↳ {c['reason']}"))


def _poll_for_grade(run_id: str) -> dict[str, Any] | None:
    """Poll /result past the async grading window. Returns the graded envelope, or None
    if it did not land within the cap (the grade will still record server-side)."""
    deadline = time.monotonic() + _GRADE_POLL_CAP_SECONDS
    while True:
        r: dict[str, Any] = RestClient().get(f"/runs/{run_id}/result")
        if r.get("status") != "grading":
            return r
        if time.monotonic() >= deadline:
            return None
        time.sleep(_GRADE_POLL_SECONDS)


def judge_degraded(grade: dict[str, Any]) -> bool:
    """Did the judge half fail to run at all?

    `unavailable` / `model-not-configured` mean the judge contributed 0.0 to `overall` without
    ever grading — by design, and documented, but invisible outside report.md. `partial` is NOT
    this: it means the judge ran and scored some of the rubric, and it carries its own coverage
    disclosure. An absent status is an ungraded run, not a degraded one.
    """
    status = grade.get("judge_status")
    return bool(status) and status not in ("ok", "partial")


def _run_score(client: RestClient, run: dict[str, Any]) -> str:
    """A terminal run's overall score for the list view; DASH when absent/ungraded.

    A judge-degraded score is marked. `0.50` from a full deterministic solve with no judge is
    indistinguishable from a genuine half-score otherwise, and the list is exactly where someone
    compares runs at a glance. The whole result is fetched here already, so the status is in hand.
    """
    if run.get("state") != "terminal":
        return DASH
    result = client.get(f"/runs/{run['run_id']}/result")
    grade = (result or {}).get("grade") or {}
    overall = grade.get("overall")
    if not isinstance(overall, int | float):
        return DASH
    return f"{overall:.2f}[warn]*[/warn]" if judge_degraded(grade) else f"{overall:.2f}"


@run_app.command("list")
def list_runs(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show full run ids and raw internal states."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw run list as JSON (for scripting)."
    ),
) -> None:
    """List runs — newest first; the short run ids feed run status / report / traces."""
    client = RestClient()
    runs = client.get("/runs")
    if as_json is True:
        emit_json(runs)
        return
    if not runs:
        console.print(
            "no runs yet — create one: xorcise run create --agent <name> --mission <id>",
            markup=False,
        )
        return
    agent_names = agent_names_by_id(client)
    mission_names = mission_names_by_id(client)
    id_col = "Run id" if verbose is True else "Run"
    table = ux_table(
        id_col, "Result", "Agent", "Harness", "Mission", "Score", "Started", title="Runs"
    )
    any_degraded = False
    for r in runs:
        rid = str(r.get("run_id") or DASH)
        agent = agent_names.get(str(r.get("agent_id")), str(r.get("agent_id") or DASH)[:8])
        # The harness the run was RENDERED as (its source_agent at create time). "Custom" =
        # no harness-specific adapter: the replay is the generic renderer (#119).
        harness = kind_label(r.get("source_agent") or None)
        mission_id = str(r.get("mission") or r.get("mission_id") or DASH)
        mission = mission_names.get(mission_id, mission_id)
        state = run_state_markup(r.get("state"), r.get("terminal_trigger"))
        if verbose is True:
            state += f" [dim]({r.get('state')}/{r.get('terminal_trigger') or '—'})[/dim]"
        score = _run_score(client, r)
        any_degraded = any_degraded or "*" in score
        table.add_row(
            rid if verbose is True else short_id(rid),
            state,
            agent,
            harness,
            mission,
            score,
            humanize_when(r.get("created_at")),
        )
    print_table(table)
    if any_degraded:
        # A bare marker is worse than none — say what it means, once, only when one is on screen.
        console.print(
            "[dim]* the judge half did not run for this score — see xorcise run status <id>[/dim]"
        )


def _auto_pull(client: RestClient, entry: dict[str, Any], mission: str) -> None:
    """Install a not-yet-installed mission before creating its run (docker-run-style,
    parity with POST /runs which auto-pulls on demand). The CLI runs the pull itself so
    the wait shows the honest progress bar instead of a silently long create request.

    Everything here renders on stderr: with --json, stdout must carry ONLY the created
    run. Returns on 'installed'; every other terminal state means no run gets created —
    'error' and 'cancelled' exit 1 (unlike `mission pull`, where an external cancel is a
    clean converged stop, exit 0 — here the command's artifact, the run, never appeared),
    and a poll-cap expiry exits 3 (in progress, retry once installed)."""
    name = entry.get("name") or mission
    err_console.print(f"mission '{name}' is not installed — pulling it first", markup=False)
    view = pull_to_terminal(client, mission)
    status = view.get("status")
    if status == "installed":
        err_console.print(f"installed '{name}' ({mission})", markup=False)
        return
    if status == "error":
        fail(
            f"pull failed — {view.get('detail') or 'unknown error'}",
            see=("xorcise doctor",),
        )
    if status == "cancelled":
        fail(f"pull cancelled — '{mission}' was not installed, so no run was created")
    # Cap expired with the job still running server-side: in progress, not failure.
    err_console.print(
        f"still pulling — the download continues in the background (job {view.get('job_id')}); "
        f"once installed, re-run: xorcise run create --agent <agent> --mission {mission}",
        markup=False,
    )
    raise typer.Exit(3)


@run_app.command("create")
def create_run(
    agent: str = typer.Option(
        ..., "--agent", help="Registered agent name (see: xorcise agent list)."
    ),
    mission: str = typer.Option(
        ...,
        "--mission",
        help="Mission id or name (see: xorcise mission list); pulled first if not installed.",
    ),
    budget: int | None = typer.Option(None, "--budget", help="Run budget in seconds."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw created run as JSON (for scripting)."
    ),
) -> None:
    """Create an evaluation run (one agent vs one mission; pulls the mission on demand)."""
    client = RestClient()
    # Validate both inputs up front: the answer to a typo is the matching name — not a 404.
    agent = resolve_agent_name(client, agent)
    entry = resolve_mission(client, mission)
    mission = str(entry["mission_id"])
    if not entry.get("installed"):
        _auto_pull(client, entry, mission)
    body: dict[str, object] = {"agent": agent, "mission": mission}
    if budget is not None:
        body["budget_seconds"] = budget
    # Run-create runs the host nesting probe synchronously on a cold cache (a throwaway DinD boot,
    # up to ~3 min on a fresh host), far past the 5 s default. Without a longer timeout the CLI
    # gives up while the server keeps going and still creates the run — and the retry the timeout
    # message invites mints a SECOND run. Wait instead.
    created = client.post("/runs", json=body, timeout=300.0)
    if as_json is True:
        emit_json(created)
        return
    run_id = created.get("run_id") if isinstance(created, dict) else None
    if run_id:
        console.print(f"run {run_id} created ({agent} vs {mission})", markup=False)
        next_step(f"xorcise run launch-cmd {short_id(run_id)}", label="connect your agent")
        next_step(f"xorcise run status {short_id(run_id)}", label="check the result")
    else:  # unexpected but non-fatal: show what the server returned
        console.print(created)


@run_app.command("status")
def run_status(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also render the per-check and per-criterion breakdown."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the full result as JSON (for scripting)."
    ),
) -> None:
    """Show a run's result: scores, breakdown, evidence, deductions, and conditions."""
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    # get_run_result: an active run's server 409 becomes {"status": "active"}, so a
    # status check right after `run create` (the golden-path hint) reads as progress,
    # not a red 409 that looks like a crash.
    # verify=True: this is one run shown to a person, so the digest is worth re-hashing.
    # The sweeping callers (`run list`, the leaderboard, `run export`) deliberately do not.
    r = client.get_run_result(run_id, verify=True)
    # The telemetry summary (renderer, content counts, warnings) rides along with a graded
    # result. Tolerant read: an older server without the endpoint simply yields nothing.
    telemetry = client.get_or_none(f"/runs/{run_id}/telemetry") if "grade" in r else None
    if as_json is True:
        # JSON first, ALWAYS parseable — the envelope itself carries status:"grading"
        # / "active", so a polling script never receives prose on the JSON path. The telemetry
        # block is additive so existing consumers keep working.
        emit_json({**r, "telemetry": telemetry} if telemetry is not None else r)
        return
    if r.get("status") == "grading":
        # Terminal but not graded yet — grading is async after /complete.
        console.print(
            f"[warn]grading in progress[/] — result for run {short_id(run_id)} is not ready "
            f"yet; re-run [value]xorcise run status {short_id(run_id)}[/value] shortly"
        )
        raise typer.Exit(3)
    if r.get("status") == "active" or (r.get("state") in {"created", "active"}):
        # Still running — in progress, not a failure (exit 3, like grading), so a
        # `while xorcise run status …` poll keeps waiting instead of erroring out.
        console.print(
            f"run {short_id(run_id)} is still running — no result yet; "
            f"re-run [value]xorcise run status {short_id(run_id)}[/value] when it finishes"
        )
        raise typer.Exit(3)
    _render_result(r, verbose=verbose, telemetry=telemetry)


@run_app.command("terminate")
def run_terminate(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Poll until grading finishes and print the result."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Stop a running run early (it is graded on what happened so far).

    The run is sealed immediately; grading then runs in the background. \
By default this polls run status until the grade lands and prints it; \
pass --no-wait to return on the ack.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    confirm_or_abort(f"Terminate run {short_id(run_id)}? (it will be graded as-is)", assume_yes=yes)
    entry = client.post(f"/runs/{run_id}/terminate", json={})
    trigger = entry.get("terminal_trigger") or "operator"
    console.print(f"run {run_id} → {entry.get('state')} (trigger: {trigger})")
    if not wait:
        return
    console.print("[warn]grading…[/] (waiting for the result)")
    r = _poll_for_grade(run_id)
    if r is None:
        console.print(
            "grading still in progress — check with "
            f"[value]xorcise run status {short_id(run_id)}[/value] shortly"
        )
        raise typer.Exit(3)
    _render_result(r)


@run_app.command("regrade")
def run_regrade(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    wait: bool = typer.Option(
        True, "--wait/--no-wait", help="Poll until grading finishes and print the result."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Also render the per-check and per-criterion breakdown."
    ),
) -> None:
    """Re-grade a finished run's sealed evidence against the current settings.

    The agent is NOT re-run: the deterministic checks and the LLM judge re-evaluate \
the evidence already recorded for the run. Use after fixing grading config — e.g. \
a judge token budget the transcript overflowed, or a rejected model key (see \
`xorcise config`). The recorded result is replaced. By default this polls until \
the fresh grade lands and prints it; pass --no-wait to return on the ack.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    confirm_or_abort(
        f"Re-grade run {short_id(run_id)}? (its recorded result is replaced)", assume_yes=yes
    )
    ack = client.post(f"/runs/{run_id}/regrade", json={})
    console.print(f"run {run_id} → {ack.get('status')}")
    if not wait:
        return
    console.print("[warn]grading…[/] (waiting for the result)")
    r = _poll_for_grade(run_id)
    if r is None:
        console.print(
            "grading still in progress — check with "
            f"[value]xorcise run status {short_id(run_id)}[/value] shortly"
        )
        raise typer.Exit(3)
    _render_result(r, verbose=verbose)


@run_app.command("delete")
@run_app.command("rm", hidden=True)
def run_delete(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a run's result + record. Available as both `delete` and `rm`.

    Removes the recorded result and the run row, so the run leaves the runs \
list, agent history, and results view. A still-running run cannot be \
deleted — stop it first with `xorcise run terminate`.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    confirm_or_abort(f"Delete run {short_id(run_id)} and its recorded result?", assume_yes=yes)
    client.delete(f"/runs/{run_id}")
    console.print(f"deleted run '{run_id}'")


#: Run ids are `uuid4().hex`. The bulk export joins one onto `--out` to build a directory, so a
#: row whose id is not that shape is refused rather than turned into a path: /runs is
#: server-supplied data, and a `run_id` of '../…' from a foreign or hostile service would write
#: outside the directory the operator named.
_RUN_ID_SHAPE = re.compile(r"[0-9a-f]{32}")

#: Per-call timeout for the export's document fetches. Each GET /report re-hashes the run's
#: evidence server-side (seconds on a large run) and an otlp.jsonl can be multi-megabyte, so the
#: 5 s control default turned a slow-but-healthy run into a client timeout — and because a
#: service-wide failure is re-raised rather than skipped, that ONE run aborted the whole batch.
#: `run create` widens its timeout for the same reason.
_EXPORT_FETCH_TIMEOUT_SECONDS = 300.0


def _is_grading_envelope(body: str) -> bool:
    """Is this the 202 "still grading" JSON envelope rather than a rendered report?

    Checked by shape, not by status code, because `get_text` does not surface one. Shared by
    `run report` (which must not write a one-line JSON "report" to disk) and the bulk export
    (which must not count one as an exported run) — one test, written once.
    """
    head = body.lstrip()[:200]
    return head.startswith("{") and '"grading"' in head


def select_runs_for_export(
    runs: Sequence[dict[str, Any]],
    *,
    mission: str | None = None,
    agent_id: str | None = None,
    since: str | None = None,
    genuine_only: bool = False,
) -> list[dict[str, Any]]:
    """Which runs a bulk export should write, in the order the server returned them.

    Pure, so the filtering rules are testable without a disk or a server.

    Only terminal runs qualify, always: an active run has no report, no sealed trace and no final
    event stream, so including it would write three misleading files instead of failing honestly.

    `mission` and `agent_id` are the CANONICAL values the run rows carry — the mission slug and the
    agent id. The caller resolves what the operator typed (a display name, a prefix) before getting
    here, so a typo fails with the candidates rather than as a silently empty selection.

    `genuine_only` keeps only the runs the agent finished itself (COMPLETED_TRIGGERS); every other
    trigger the server writes — an operator kill, a budget timeout, a deploy failure, a crash — is
    the platform ending the run, which is not agent performance.

    `since` compares ISO timestamps and is INCLUSIVE of its boundary — a half-open one silently
    drops the run created exactly at a timestamp pasted from a previous export, which is the run
    someone re-running a range is most likely to want. Unparseable timestamps on either side sort
    the run out rather than crashing the export. An EMPTY `since` is rejected, not read as "no
    floor": `--since "$FROM"` with an unset FROM is the same hazard `--agent ""` is, and it
    exported the entire history with exit 0.
    """
    from datetime import UTC, datetime

    def created(row: dict[str, Any]) -> datetime | None:
        try:
            at = datetime.fromisoformat(str(row.get("created_at") or ""))
        except ValueError:
            return None
        # A stored timestamp without an offset gets the same treatment, so the comparison is
        # always aware-vs-aware whichever side is missing its zone.
        return at.replace(tzinfo=UTC) if at.tzinfo is None else at

    floor = None
    # `is not None`, not truthiness: `--since ""` fell through to "no floor" and selected every
    # run ever recorded. fromisoformat refuses it below, so it fails as the usage error it is.
    if since is not None:
        try:
            floor = datetime.fromisoformat(since)
        except ValueError as exc:
            raise typer.BadParameter(
                f"--since must be an ISO timestamp (e.g. 2026-07-01T10:00:00+00:00), got {since!r}"
            ) from exc
        # `--since 2026-07-01` is the obvious thing to type, and fromisoformat returns it NAIVE —
        # comparing that with an offset-aware created_at raises TypeError. A bare date is a
        # legitimate input, so it is read as UTC (run timestamps are UTC) rather than rejected.
        if floor.tzinfo is None:
            floor = floor.replace(tzinfo=UTC)

    picked = []
    for row in runs:
        if row.get("state") != "terminal":
            continue
        if mission and str(row.get("mission") or row.get("mission_id") or "") != mission:
            continue
        if agent_id and str(row.get("agent_id") or "") != agent_id:
            continue
        if genuine_only and str(row.get("terminal_trigger") or "") not in COMPLETED_TRIGGERS:
            continue
        if floor is not None:
            at = created(row)
            if at is None or at < floor:
                continue
        picked.append(row)
    return picked


def export_directory_names(run_ids: Sequence[str]) -> dict[str, str]:
    """run id → the directory name its export is written under; short where that is unambiguous.

    `<id8>` is what every other view shows and what an operator recognises, but two ids sharing a
    prefix would share a directory: the second run's files silently overwrote the first's while
    the command still reported both as exported. Colliding ids fall back to the full 32 chars —
    ugly, never wrong. uuid4 makes this rare, and rare-and-silent is exactly the bad combination.
    """
    counts = Counter(rid[:8] for rid in run_ids)
    return {rid: (rid[:8] if counts[rid[:8]] == 1 else rid) for rid in run_ids}


def _document_name(path: str) -> str:
    """`/runs/<id>/otlp.jsonl` → `otlp.jsonl` — which document failed, without the id again."""
    return path.rsplit("/", 1)[-1].split("?", 1)[0]


def _publish(bodies: dict[str, str], target: Path) -> None:
    """Write one run's bundle through a staging directory, then move each file into place.

    Fetching every document before the first mkdir covers a failed FETCH. It does not cover a
    write that fails PART WAY — a full disk on a multi-megabyte trace is the realistic one —
    which left a directory holding a truncated file, and anything globbing `<out>/*/` reads that
    as an exported run. Staging is a sibling of the target, so each move is a rename on the same
    filesystem and a file appears whole or not at all.

    A FIRST export moves the whole staging directory in one rename, so the run appears complete
    or not at all. A RE-export moves file by file, because replacing the directory would delete
    anything else already in it — so the atomicity above is traded for not destroying a reader's
    own files, and what a failure mid-loop leaves is a mix of two exports of the same run rather
    than a half-written one. The staging name is dot-prefixed so a `<out>/*/` glob never sees it
    even mid-write.

    The price, stated: a run's documents are held twice while they are staged, and a process
    killed outright (SIGKILL, power loss) leaves one `.xorcise-export-*` directory behind that
    nothing reaps. Dot-prefixed it stays out of every `<out>/*/` reader, so it is litter rather
    than a wrong answer — and a sweep here would delete a CONCURRENT export's staging directory,
    which is worse than the litter.
    """
    import os
    import shutil
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".xorcise-export-", dir=target.parent))
    try:
        for name, body in bodies.items():
            (staging / name).write_text(body, encoding="utf-8")
        if not target.exists():
            # Nothing to preserve, so move the DIRECTORY: one rename, so the run appears whole or
            # not at all. The per-file path below cannot promise that — a failure between two
            # `os.replace` calls leaves some files new and some missing — and this is the common
            # case, since most exports write a run for the first time.
            os.replace(staging, target)
            return
        # Re-export onto an existing directory: per FILE, because replacing the directory would
        # delete anything else already in it. The window the paragraph above closes is open here,
        # and it is the narrower risk of the two: a run's own files are being overwritten with
        # fresh copies of themselves, so a failure mid-loop leaves a MIX of two exports of the
        # same run rather than a half-written one.
        for name in bodies:
            os.replace(staging / name, target / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@run_app.command("export")
def run_export(
    out: str = typer.Option(
        ...,
        "--out",
        help="Directory to write the export tree into (created if missing).",
    ),
    mission: str | None = typer.Option(
        None,
        "--mission",
        help="Only runs of this mission — id or name (see: xorcise mission list).",
    ),
    agent: str | None = typer.Option(
        None, "--agent", help="Only runs by this registered agent name (see: xorcise agent list)."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only runs created at or after this ISO timestamp (inclusive)."
    ),
    format: ReportFormat = _FORMAT_OPTION,
    genuine_only: bool = typer.Option(
        False,
        "--genuine-only",
        help=(
            "Skip runs the agent did not finish itself — operator kills, budget timeouts, "
            "deploy failures and crashes."
        ),
    ),
) -> None:
    """Export a SET of runs — report, result, raw OTLP and normalized events — into one tree.

    Analysis and hand-off operate on a group of runs, not one: a mission, an agent, a date range. \
Each selected run becomes `<out>/<run-id8>/` holding `report.md` (or .html), `result.json`, \
`traces.otlp.jsonl` and `events.jsonl`. Report, traces and events are the same bytes \
`run report`, `run traces --export` and `run events export` write for a single run. \
`result.json` is the server's `/result` envelope verbatim — the grade, the disclosed \
conditions and the evidence seal; `run status --json` renders that same envelope with a \
`telemetry` block merged in, so the two are one source read twice, not two formats. The \
bundled seal is RECORDED, not verified: checking it re-hashes a run's whole evidence, which a \
batch would pay once per run, so `evidence_verified` is null throughout. Use \
`xorcise run status <id>` or the report to get a verdict on one run.

    Only finished runs are exported; an active one has no sealed record yet. \
A run whose OWN document answers an error — a 404 or a 500 on that run's report, result, \
trace or events — is reported and skipped rather than aborting the batch, so one bad run does \
not cost you the ninety-nine after it. Anything that is not one document's answer stops the \
export instead: an unreachable service, an auth / rate-limit / gateway status, or no response \
inside the per-document timeout. That last one may well be a single slow run, but nothing here \
can tell it from a slow service, and retrying it would spend the whole timeout again on every \
run left. Whatever was written stays on disk, and the summary says how much.

    Exit codes say what the tree holds: 0 nothing failed — any run still grading is named on \
stderr · 1 a run was skipped, or the export stopped early; what it did write is on disk and \
the summary says how much · 2 nothing matched the filters, or the invocation was a usage \
error · 3 nothing was exported at all because every selected run is still grading — re-run \
once grading finishes.

    Re-exporting is safe but not a clean slate: a run's own files are overwritten in place \
and anything else already in its directory is left alone. Export into a fresh directory \
when you need the tree to contain only this export.
    """
    client = RestClient()
    # An empty filter value is an unset shell variable, not "no filter" — `--agent ""` skipped
    # resolution and exported EVERY run. The two resolvers refuse it for themselves
    # (`_require_value`); `--since` has no resolver, so it is refused here, before the first
    # request, rather than inside the pure selector below where the /runs GET has already gone
    # out and an unreachable service would answer a usage error with a connection error.
    if since is not None and not since.strip():
        fail("missing --since timestamp", see=("xorcise run list",), code=2)
    agent_id = None
    # `is not None`, not truthiness, for the same reason — the resolvers then refuse the empty
    # value as the usage error it is.
    if agent is not None:
        # Resolve exactly as `run create` does — exact, case-insensitive, unique prefix, then a
        # did-you-mean, failing loud. The old exact case-sensitive match fell through to "treat it
        # as an id", so `--agent alpha` against a registered `Alpha` selected nothing and reported
        # it as "no runs matched" — a typo dressed up as an empty result.
        canonical = resolve_agent_name(client, agent)
        # resolve_agent_name returns a name it read out of /agents, so the id is in this map
        # unless that name stopped being one between the two reads — the agent deregistered, or
        # renamed (the id stays, the name moves). The old fallback used the canonical NAME as an
        # id, which can only ever match no run and then reports the race as "no finished runs
        # matched the filters".
        agent_id = next(
            (aid for aid, known in agent_names_by_id(client).items() if known == canonical), None
        )
        if agent_id is None:
            fail(
                f"no agent is registered as '{canonical}' any more — it was deregistered or "
                "renamed while this command was resolving it",
                see=("xorcise agent list",),
            )
    if mission is not None:
        # Run rows carry the slug, but `run list` shows the display name and `run create --mission`
        # accepts it — so the name is what people have. resolve_mission takes either.
        mission = str(resolve_mission(client, mission)["mission_id"])

    root = Path(out)
    if root.exists() and not root.is_dir():
        # Every write below joins onto this path, so a file here fails identically for every
        # selected run: one upfront usage error beats N copies of "[Errno 20] Not a directory".
        fail(f"--out must be a directory, but {root} is a file", code=2)

    runs: list[dict[str, Any]] = client.get("/runs")
    selected = select_runs_for_export(
        runs, mission=mission, agent_id=agent_id, since=since, genuine_only=genuine_only
    )
    if not selected:
        # NOT exit 3: that code means "still in progress", so a script that retries on 3 would
        # loop forever against a filter combination that can never match. Nothing matched is
        # something the operator must change, which is what exit 2 says.
        fail(
            "no finished runs matched the filters — nothing to export",
            see=("xorcise run list",),
            code=2,
        )

    fmt = format.value if isinstance(format, ReportFormat) else str(format)
    dir_names = export_directory_names([str(row["run_id"]) for row in selected])
    # A run that collided onto a shared prefix is written under its full id — so a directory
    # already sitting at `<id8>/` is one nothing in this export will touch, while anything
    # globbing `<out>/*/` still reads it as an exported run. Name it, saying only what is
    # checked: the name, and that this export writes elsewhere. Not who wrote it — the guard is
    # `is_dir()`, so it may not be an export at all. Never delete it either: this command
    # removes nothing it did not write.
    for prefix in sorted({rid[:8] for rid, name in dir_names.items() if name != rid[:8]}):
        if (root / prefix).is_dir():
            err_console.print(
                f"[warn]stale[/] {escape(str(root / prefix))}: run ids share this prefix, so "
                "this export writes them under their full ids and leaves this directory as it is"
            )
    written = 0
    skipped: list[tuple[str, str]] = []
    pending: list[str] = []  # terminal but not yet graded — a retry, not a failure
    stopped: tuple[str, int] | None = None  # the run in hand when a service-wide failure ended it
    for row in selected:
        rid = str(row["run_id"])
        if _RUN_ID_SHAPE.fullmatch(rid) is None:
            skipped.append((short_id(rid), "not a run id — refusing to build a path from it"))
            continue
        target = root / dir_names[rid]
        try:
            report = client.get_text_or_unavailable(
                f"/runs/{rid}/report?format={fmt}", timeout=_EXPORT_FETCH_TIMEOUT_SECONDS
            )
            # Terminal does not mean graded. /report answers 202 with a JSON envelope while
            # grading is still running, and get_text hands that body back like any other — so it
            # used to land in report.md as a file that looks like an export and contains
            # {"status":"grading"}. Skip the run instead; it exports cleanly once graded.
            if _is_grading_envelope(report):
                pending.append(short_id(rid))
                continue
            # Fetch all FOUR before creating the directory. Writing the report first left a
            # directory holding report.md alone whenever a later document failed — and anything
            # globbing <out>/*/ reads that as an exported run. Dict values evaluate in order, so
            # every fetch is done before the first mkdir.
            bodies = {
                f"report.{fmt}": report,
                # The machine-readable half of the bundle: the server's /result envelope as it
                # returns it, carrying the grade AND the evidence seal (evidence_digest +
                # evidence_verified). Without it the seal reaches the tree only as sixteen
                # characters of prose inside the rendered report, which ties a grade to its
                # evidence for a reader and for nothing else. NOT byte-identical to `run status
                # --json`, which merges a `telemetry` block into this same envelope and
                # re-indents it: one envelope, two renderings, and the tree takes the server's.
                #
                # No `?verify=1`: the seal is RECORDED here, never checked. Verifying re-hashes
                # the run's whole evidence, and a batch would pay that once per run — the cost
                # that made it opt-in. So `evidence_verified` is null in every bundle and
                # `evidence_status` is "recorded", which means "not asked" rather than "could not
                # answer". `run status <id>` and `/report` are where a verdict comes from.
                "result.json": client.get_text_or_unavailable(
                    f"/runs/{rid}/result", timeout=_EXPORT_FETCH_TIMEOUT_SECONDS
                ),
                "traces.otlp.jsonl": client.get_text_or_unavailable(
                    f"/runs/{rid}/otlp.jsonl", timeout=_EXPORT_FETCH_TIMEOUT_SECONDS
                ),
                "events.jsonl": client.get_text_or_unavailable(
                    f"/runs/{rid}/events.jsonl", timeout=_EXPORT_FETCH_TIMEOUT_SECONDS
                ),
            }
            _publish(bodies, target)
        except DocumentUnavailable as exc:
            # ONE run's document answered an error status that is about that document (a 404, a
            # 500). `_send` exits the process for any error status — right for a single-run
            # command, fatal to a batch — so this used to take the ninety-nine runs after it.
            skipped.append((short_id(rid), f"{_document_name(exc.path)}: {exc}"))
            continue
        except typer.Exit as exc:
            # RestClient exits for a failure this layer cannot pin on one document: unreachable,
            # an auth / rate-limit / gateway status, or no response inside the timeout. The last
            # of those may genuinely be one slow run — a multi-megabyte trace — but a stalled
            # document and a stalled service look identical from here, and the conservative read
            # is also the cheap one: retrying a timeout per run spends that whole wait again on
            # each. So stop; but BREAK rather than re-raise, because the runs already written
            # are on disk and the summary below is the only place that is said. The client has
            # printed its own diagnostic above it.
            stopped = (short_id(rid), int(exc.exit_code))
            break
        except Exception as exc:  # noqa: BLE001 — one bad run must not end the batch
            skipped.append((short_id(rid), str(exc)))
            continue
        written += 1
        err_console.print(f"  {short_id(rid)} → {target}", markup=False)

    console.print(f"exported {written} run(s) to {root}")
    for rid in pending:
        err_console.print(f"[warn]not yet graded[/] {rid}: re-run the export once grading finishes")
    for rid, why in skipped:
        err_console.print(f"[warn]skipped[/] {rid}: {escape(why)}")
    if stopped is not None:
        rid, code = stopped
        remaining = len(selected) - written - len(skipped) - len(pending) - 1
        err_console.print(
            f"[warn]stopped[/] at {rid}: {remaining} further run(s) were not attempted — a "
            "failure this command cannot pin on one document is not retried per run"
        )
        raise typer.Exit(code)
    # A skipped run was silent and exited 0, so a script consuming <out>/*/ could not tell
    # ninety-nine runs from a hundred. It needs a person, so it outranks "not ready".
    if skipped:
        raise typer.Exit(1)
    # 3 is the in-progress code `run status` and `run report` already use, and it stays reserved
    # for the one state a caller retrying on it converges out of: NOTHING written, everything
    # still grading. On a mixed batch it would never converge — one run stuck in grading would
    # exit 3 for that filter for good — and `run export … && …` would start failing on a batch
    # that has only just finished, which is the normal case. Those runs are named on stderr.
    if pending and not written:
        raise typer.Exit(3)


@run_app.command("report")
def run_report(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    format: ReportFormat = _FORMAT_OPTION,
    out: str | None = typer.Option(
        None, "--out", help="Output path (default: ./xorcise-run-<id8>.<ext>)."
    ),
) -> None:
    """Download a run's full report as Markdown or HTML.

    The shareable counterpart to `run status`: metadata, scores, the check table, \
the judge rubric, evidence/deductions/hard-fails, artifacts, telemetry and \
disclosed conditions — one self-contained file. The report is available once \
the run has finished; while it is still running or being graded, that is what \
this reports.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    # A still-active run has no report yet — say it's still running (exit 3), instead
    # of the server's raw 409, so the golden-path hint never dead-ends in a red error.
    if client.get_run_result(run_id).get("status") == "active":
        console.print(
            f"run {short_id(run_id)} is still running — no report yet; "
            f"re-run [value]xorcise run report {short_id(run_id)}[/value] when it finishes"
        )
        raise typer.Exit(3)
    # isinstance guards direct (non-CLI) calls that pass a plain string.
    fmt = format.value if isinstance(format, ReportFormat) else str(format)
    body = client.get_text(f"/runs/{run_id}/report?format={fmt}")
    # Parity with `run status`: a terminal-but-ungraded run 202s with a JSON envelope
    # rather than a document — say so plainly instead of writing a one-line JSON "report".
    if _is_grading_envelope(body):
        console.print(
            f"[warn]grading in progress[/] — the report for run {short_id(run_id)} is not "
            f"ready yet; re-run [value]xorcise run report {short_id(run_id)}[/value] shortly"
        )
        raise typer.Exit(3)
    path = Path(out) if out else Path(f"xorcise-run-{run_id[:8]}.{fmt}")
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        err_console.print(f"[err]error[/err]: cannot write {path} — {exc}")
        raise typer.Exit(1) from exc
    console.print(f"wrote {path}")


@run_app.command("traces")
def run_traces(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    since: int = typer.Option(
        -1, "--since", help="Exclusive seq cursor for incremental reads; -1 = all records."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw trace envelope as JSON (for scripting)."
    ),
    export: bool = typer.Option(
        False,
        "--export",
        help="Download the raw OTLP stream (spans + logs) as JSONL for OTel tooling.",
    ),
    out: str | None = typer.Option(
        None,
        "--out",
        help="Export output path (implies --export; default: ./xorcise-run-<id8>-otlp.jsonl).",
    ),
) -> None:
    """Fetch the collected OTel trace records for a run (poll with --since for increments).

    With --export (or --out), download the run's whole RAW OTLP stream instead — spans \
then logs, one OTLP/JSON envelope per line, no XORCISE framing — the Collector's \
otlpjson file format, ready for external OTel viz tooling. A still-active run exports \
a partial snapshot of what has been ingested so far (and says so); once the run is \
terminal the record is sealed and the export is final. For the normalized per-event \
JSONL instead, see `xorcise run events export`.
    """
    export = export or out is not None
    if export and as_json:
        raise typer.BadParameter("--export writes a file; --json prints an envelope — pick one")
    if export and since != -1:
        raise typer.BadParameter(
            "--since applies to the polling view; --export always takes a whole-run snapshot"
        )
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    if export:
        # Conservative labeling: checked BEFORE the download, so a run that seals mid-flight
        # can only be over-labeled partial (harmless) — never under-labeled complete.
        active = client.get_run_result(run_id).get("status") == "active"
        body = client.get_text(f"/runs/{run_id}/otlp.jsonl")
        path = Path(out) if out else Path(f"xorcise-run-{run_id[:8]}-otlp.jsonl")
        try:
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            err_console.print(f"[err]error[/err]: cannot write {path} — {exc}")
            raise typer.Exit(1) from exc
        batches = sum(1 for ln in body.splitlines() if ln.strip())
        note = (
            " — run still active, partial snapshot; re-run after it finishes for the sealed record"
            if active
            else ""
        )
        console.print(f"wrote {path} ({batches} batch{'' if batches == 1 else 'es'}{note})")
        return
    data = client.get(f"/runs/{run_id}/traces?since={since}")
    if as_json is True:
        # The full envelope (run_id + records), so a polling script keeps the context.
        emit_json(data)
        return
    records = data.get("records") or []
    if not records:
        console.print(f"no trace records for run {run_id}")
        return
    for rec in records:
        payload = rec.get("payload") or {}
        # Payloads are raw OTLP spans — render a name if present, else the top-level keys.
        summary = payload.get("name") if isinstance(payload, dict) else None
        if not summary:
            summary = ", ".join(sorted(payload)) if isinstance(payload, dict) else str(payload)
        console.print(f"seq {rec.get('seq')}: {summary or '(empty)'}", markup=False)
    console.print(f"\n{len(records)} record(s)")


@run_app.command("prompt")
def run_prompt(run_id: str = typer.Argument(..., help=_RUN_ID_HELP)) -> None:
    """Print a run's connect prompt verbatim (pipe it to a file for your agent)."""
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    # Emit the RAW prompt text, not the REST envelope. console.print() on the
    # {"run_id", "prompt"} dict renders the prompt's real newlines as literal "\n"
    # escapes (and soft-wraps), which corrupts the connect recipe + OTLP endpoint when
    # the output is saved to a file and fed to an agent. typer.echo writes the body
    # verbatim — real newlines preserved, no escaping, no wrapping.
    typer.echo(client.get(f"/runs/{run_id}/prompt")["prompt"])


@run_app.command("launch-profile")
def run_launch_profile(run_id: str = typer.Argument(..., help=_RUN_ID_HELP)) -> None:
    """Print a run's harness launch profile — the pre-start OTel env as dotenv KEY=VALUE lines.

    The harness-facing artifact: the telemetry endpoint XORCISE configured for \
this run. Pipe it to a file and feed the harness: \
`xorcise run launch-profile <id> > launch.env`. \
Empty output when no collector is configured.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    env = client.get(f"/runs/{run_id}/launch-profile")["env"]
    for key, value in env.items():
        typer.echo(f"{key}={value}")


@run_app.command("launch-cmd")
def run_launch_cmd(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    launch_mode: str = typer.Argument(
        "host", help="Where the harness runs: 'host' (paste into a host terminal) or 'container'."
    ),
) -> None:
    """Print the copy-paste startup block for a host-run harness — the OTel \
`export`s plus the single-line harness command. Defaults to host mode (the \
telemetry endpoint points at this machine's loopback). See also `run \
launch-profile` for the env alone (dotenv, to pipe into a config)."""
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    profile: dict[str, Any] = client.get(f"/runs/{run_id}/launch-profile?launch_mode={launch_mode}")
    block = profile.get("shell_block") or ""
    if not block:
        console.print("[warn]no launch command for this run's harness[/]")
        return
    # Verbatim artifact (like `run prompt`): typer.echo, so nothing is wrapped or styled.
    typer.echo(block)


@run_events_app.command("export")
def run_events_export(
    run_id: str = typer.Argument(..., help=_RUN_ID_HELP),
    out: str | None = typer.Option(
        None, "--out", help="Output path (default: ./xorcise-run-<id8>-events.jsonl)."
    ),
) -> None:
    """Download a run's normalized AgentEvent stream as JSONL — a header line, then \
one event per line with clean bodies (debug/inspection). Works mid-run as a partial \
snapshot. For the raw OTLP stream instead, see `xorcise run traces --export`.
    """
    client = RestClient()
    run_id = _resolve_id(client, run_id)
    body = client.get_text(f"/runs/{run_id}/events.jsonl")
    path = Path(out) if out else Path(f"xorcise-run-{run_id[:8]}-events.jsonl")
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        err_console.print(f"[err]error[/err]: cannot write {path} — {exc}")
        raise typer.Exit(1) from exc
    try:
        # The header line carries the authoritative count.
        written = int(json.loads(body.split("\n", 1)[0] or "{}").get("event_count", 0))
    except ValueError:
        written = 0
    console.print(f"wrote {path} ({written} event{'' if written == 1 else 's'})")
