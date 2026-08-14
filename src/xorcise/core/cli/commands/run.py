"""`xorcise run` group — thin REST client (cli)."""

from __future__ import annotations

import json
import time
from enum import StrEnum
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
    DASH,
    confirm_or_abort,
    fail,
    fmt_score,
    humanize_when,
    next_step,
    print_table,
    run_state_markup,
    short_id,
    ux_table,
)
from xorcise.core.cli.rest_client import RestClient

run_app = typer.Typer(
    help="Create and manage evaluation runs.",
    no_args_is_help=True,
    epilog=EXIT_CODES_EPILOG,
)
app.add_typer(run_app, name="run", rich_help_panel="Evaluate")

# `xorcise run events …` — per-run AgentEvent projection tools. Server-local (reads the local
# projection cache + writes under ~/.xorcise), unlike the thin REST-client commands below.
run_events_app = typer.Typer(
    help="Per-run AgentEvent export (reads the local cache under ~/.xorcise).",
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


def _render_result(r: dict[str, Any], *, verbose: bool = False) -> None:
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
    console.print(f"model: {escape(str(cond.get('model') or 'model not disclosed'))}")
    console.print(
        f"judge model: {escape(str(cond.get('judge_model') or 'judge model not configured'))}"
    )
    console.print(f"budget: {cond.get('budget_seconds', 0)}s")
    console.print(f"sandbox: {escape(str(cond.get('sandbox_ref') or '—'))}")
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


def _run_score(client: RestClient, run: dict[str, Any]) -> str:
    """A terminal run's overall score for the list view; DASH when absent/ungraded."""
    if run.get("state") != "terminal":
        return DASH
    result = client.get(f"/runs/{run['run_id']}/result")
    overall = ((result or {}).get("grade") or {}).get("overall")
    return f"{overall:.2f}" if isinstance(overall, int | float) else DASH


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
    table = ux_table(id_col, "Result", "Agent", "Mission", "Score", "Started", title="Runs")
    for r in runs:
        rid = str(r.get("run_id") or DASH)
        agent = agent_names.get(str(r.get("agent_id")), str(r.get("agent_id") or DASH)[:8])
        mission_id = str(r.get("mission") or r.get("mission_id") or DASH)
        mission = mission_names.get(mission_id, mission_id)
        state = run_state_markup(r.get("state"), r.get("terminal_trigger"))
        if verbose is True:
            state += f" [dim]({r.get('state')}/{r.get('terminal_trigger') or '—'})[/dim]"
        table.add_row(
            rid if verbose is True else short_id(rid),
            state,
            agent,
            mission,
            _run_score(client, r),
            humanize_when(r.get("created_at")),
        )
    print_table(table)


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
    r = client.get_run_result(run_id)
    if as_json is True:
        # JSON first, ALWAYS parseable — the envelope itself carries status:"grading"
        # / "active", so a polling script never receives prose on the JSON path.
        emit_json(r)
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
    _render_result(r, verbose=verbose)


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
    from pathlib import Path

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
    if body.lstrip().startswith("{") and '"grading"' in body:
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
) -> None:
    """Fetch the collected OTel trace records for a run (poll with --since for increments)."""
    client = RestClient()
    run_id = _resolve_id(client, run_id)
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
        None, "--out", help="Output path (default: ~/.xorcise/runs/<run_id>/agent-events.jsonl)."
    ),
) -> None:
    """Write a run's normalized AgentEvent stream to JSONL — one event per line \
with clean bodies (debug/inspection). Local: reads the projection cache and \
writes under ~/.xorcise.
    """
    from pathlib import Path

    from xorcise.core.contracts.errors import NotFoundError
    from xorcise.core.rest.events_export import export_run_events

    out_path = Path(out) if out else None
    if not run_id.strip():
        resolve_run_id(RestClient(), run_id)  # raises the missing-id usage error

    def _events_written(p: Path) -> int:
        """Events in the export (the header line carries the count)."""
        try:
            with p.open(encoding="utf-8") as fh:
                return int(json.loads(fh.readline() or "{}").get("event_count", 0))
        except (OSError, ValueError):
            return 0

    try:
        try:
            path = export_run_events(run_id, out_path)
            written = _events_written(path)
        except NotFoundError:
            path, written = None, 0
        if written == 0 and len(run_id) < 32:
            # A short id is a PREFIX. Exporting it literally wrote a header-only
            # file and exited 0 — a silent empty export. Resolve it (service
            # needed) so the user gets the real run, or a clean not-found.
            resolved = resolve_run_id(RestClient(), run_id)
            path = export_run_events(resolved, out_path)
            written = _events_written(path)
        if path is None:
            raise NotFoundError(f"no run '{run_id}'")
    except NotFoundError as exc:
        err_console.print(f"[err]error[/err]: no such run '{run_id}'")
        raise typer.Exit(1) from exc
    except OSError as exc:
        err_console.print(f"[err]error[/err]: cannot write the export — {exc}")
        raise typer.Exit(1) from exc
    console.print(f"wrote {path} ({written} event{'' if written == 1 else 's'})")
