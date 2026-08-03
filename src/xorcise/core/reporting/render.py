"""Run-report rendering — pure Markdown/HTML from plain data (domain module, reporting).

A run report is the shareable, offline artifact of everything the Results page shows: metadata,
the 50/50 scores, the deterministic check table, the judge rubric, evidence/deductions/hard-fails,
submitted artifacts, telemetry, the resolved terrain and disclosed conditions.

The HTML path is the OFFLINE TWIN of the Results page and the only XORCISE artifact that leaves
the building, so it carries the brand itself: the amber crosshair mark + `XORCISE.AI` wordmark
(letters warm `#f2ead6`, dot ALWAYS amber) in the masthead and the favicon, the same score-first
hierarchy the app uses (KPI strip → scorecard with an overall dial + the two half-score meters →
detail), the same 6px radius and surface ladder, and a print register so the file survives being
printed into a board pack. Colour follows the brand rule that a SECOND HUE is never decoration:
amber is identity/chrome (mark, wordmark dot, section ticks, links) and never a measured value;
measured values read on the functional ladder (data `#6ee7a8` / warning `#f0d890` / failure
`#ff5f57`); everything else is brightness within one neutral ramp.

Deliberately DEPENDENCY-FREE and CROSS-MODULE-FREE:
  * no markdown/jinja library — the templates are hand-rolled f-strings, so a report renders in
    any install (XORCISE ships one distribution; a renderer must not add a wheel);
  * no external font, script, stylesheet or image — the mark, the favicon, the score dial, the
    meters and the terrain map are all INLINE SVG/CSS, so the file looks right opened from a
    download folder or forwarded as an attachment, offline;
  * no runs/agents/runcontrol imports — `reporting` is a sibling module of those (the import-linter
    layer rule), so this module takes an already-assembled `RunReportContext` of contract DTOs.
    The cross-module join lives one layer up, in `rest/report_assembly.py`.

Every user-originated string (mission/agent names, judge reasons, check values, terrain labels,
artifact payloads) is `html.escape`d on the HTML path — a run report is shared, and an artifact
payload is attacker-controlled by construction (the agent wrote it). The Markdown path escapes
table-breaking characters only; Markdown is plain text, not a document that executes.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from xorcise.core.contracts.grading import GradeResult
from xorcise.core.contracts.reporting import ResultConditions, RunStats
from xorcise.core.contracts.run import RunEntry
from xorcise.core.contracts.terrain import ResolvedTerrainV2, TerrainNodeV2

# How much of an artifact payload the report embeds before truncating. Reports are meant to be
# opened and read; a multi-megabyte paste would make the HTML unusable.
_PAYLOAD_LIMIT = 20_000
# Same idea for a single deterministic check's resolved value inside a table cell.
_VALUE_LIMIT = 240

_DASH = "—"


@dataclass(frozen=True)
class ReportArtifact:
    """One agent-submitted artifact as it appears in the report (mirrors RunArtifactView)."""

    name: str
    kind: str
    seq: int
    payload: str


@dataclass(frozen=True)
class RunReportContext:
    """Everything a report needs, already joined. Pure data — no stores, no I/O."""

    run: RunEntry
    agent_name: str
    grade: GradeResult
    conditions: ResultConditions
    partial: bool = False
    partial_trigger: str | None = None
    stats: RunStats | None = None
    artifacts: tuple[ReportArtifact, ...] = ()
    # The run's resolved terrain, ALREADY FOLDED to its settled end-state by the assembler (a
    # report is a snapshot, not a time machine — `updates` is not replayed here). None when the
    # mission declared no terrain or the map could not be resolved; the section is then omitted
    # rather than drawn empty.
    terrain: ResolvedTerrainV2 | None = None
    # Injected so a report is reproducible in tests; defaults to render time.
    generated_at: datetime | None = None
    version: str = field(default="")


# ── formatting helpers (shared by both renderers) ────────────────────────────────────────────


def _xorcise_version() -> str:
    """The installed distribution version, resolved lazily (stdlib only — no cross-layer import)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("xorcise")
    except PackageNotFoundError:  # pragma: no cover — source checkout without an install
        return "0.0.0+unknown"


def _ts(value: datetime | None) -> str:
    if value is None:
        return _DASH
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _score(value: float) -> str:
    return f"{value:.2f} ({value * 100:.0f}%)"


def _score_range(lower: float, upper: float | None) -> str:
    if upper is not None and upper > lower + 1e-9:
        return f"{_score(lower)}–{_score(upper)}"
    return _score(lower)


def _elapsed_seconds(ctx: RunReportContext) -> float | None:
    """Wall-clock run duration: the recorded telemetry timing wins, else created→completed."""
    if ctx.stats is not None and ctx.stats.timing.elapsed_seconds is not None:
        return ctx.stats.timing.elapsed_seconds
    if ctx.run.completed_at is None:
        return None
    return (ctx.run.completed_at - ctx.run.created_at).total_seconds()


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return _DASH
    total = int(seconds)
    if total < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rest}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {rest}s"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n… [truncated — {len(text) - limit} more characters]"


def _value(raw: object) -> str:
    """A deterministic check's resolved value, flattened to one readable line."""
    if raw is None:
        return _DASH
    text = raw if isinstance(raw, str) else repr(raw)
    if len(text) > _VALUE_LIMIT:
        text = f"{text[:_VALUE_LIMIT]}…"
    return text


def _verdict_label(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _verdict_class(passed: bool) -> str:
    return "pass" if passed else "fail"


def _status_line(ctx: RunReportContext) -> str:
    trigger = ctx.run.terminal_trigger
    return f"{ctx.run.state} ({trigger})" if trigger else ctx.run.state


def _metadata_rows(ctx: RunReportContext) -> list[tuple[str, str]]:
    """The identity metadata table, shared verbatim by both renderers and matched field-for-field
    by the results page and the live run header: Mission and Agent carry their pinned version in
    the name, then the Harness, when it Started and how long it ran (Duration)."""
    agent = ctx.agent_name or ctx.run.agent_id
    return [
        ("Name", ctx.run.name or ctx.run.mission),
        ("Mission", f"{ctx.run.mission} v{ctx.conditions.mission_version}"),
        ("Agent", f"{agent} v{ctx.conditions.agent_version}"),
        ("Harness", ctx.run.source_agent or "generic"),
        ("Started", _ts(ctx.run.created_at)),
        ("Duration", _duration(_elapsed_seconds(ctx))),
        ("Run ID", ctx.run.run_id),
        ("Status", _status_line(ctx)),
        ("Budget", f"{ctx.run.budget_seconds}s" if ctx.run.budget_seconds else _DASH),
    ]


def _condition_rows(ctx: RunReportContext) -> list[tuple[str, str]]:
    # Agent/mission versions are folded into the Overview names above, so they are not repeated
    # here — Conditions carries only what the run was *evaluated under*.
    c = ctx.conditions
    return [
        ("Agent model (disclosed)", c.model or "not disclosed"),
        ("Judge model", c.judge_model or "not configured"),
        ("Budget", f"{c.budget_seconds}s"),
        ("Sandbox image", c.sandbox_ref or _DASH),
    ]


def _telemetry_rows(ctx: RunReportContext) -> list[tuple[str, str]]:
    s = ctx.stats
    if s is None:
        return []
    return [
        ("Input tokens", f"{s.tokens.input:,}"),
        ("Output tokens", f"{s.tokens.output:,}"),
        ("Cache read tokens", f"{s.tokens.cache_read:,}"),
        ("Cache creation tokens", f"{s.tokens.cache_creation:,}"),
        ("Reasoning tokens", f"{s.tokens.reasoning:,}"),
        ("Total tokens", f"{s.tokens.total:,}"),
        ("Model calls", f"{s.counts.model_calls:,}"),
        ("Tool calls", f"{s.counts.tool_calls:,}"),
        ("Findings", f"{s.counts.findings:,}"),
        ("Errors", f"{s.counts.errors:,}"),
        ("Events", f"{s.counts.events_total:,}"),
        ("First event", _ts(s.timing.first_event_ts)),
        ("Last event", _ts(s.timing.last_event_ts)),
        (
            "Longest tool call",
            f"{s.timing.longest_tool_ms} ms" if s.timing.longest_tool_ms is not None else _DASH,
        ),
    ]


_STATE_RANK = {"defined": 0, "discovered": 1, "completed": 2}


def _max_state(a: str, b: str) -> str:
    return b if _STATE_RANK.get(b, 0) > _STATE_RANK.get(a, 0) else a


def _drawn_nodes(t: ResolvedTerrainV2) -> list[tuple[TerrainNodeV2, str]]:
    """The nodes a terrain actually shows, each with its EFFECTIVE state.

    `endpoint` nodes (`hs:join`, `rc:artifacts`, …) are not drawn — they collapse onto the parent
    service named by their id prefix, exactly as the app's `terrain-layout.ts` does, lifting their
    most-advanced state onto it. Both the drawn map and the counted facts read this one function,
    so the picture and the table can never disagree."""
    known = {g.id for g in t.groups}
    members = [n for n in t.nodes if n.group in known]
    drawn = [n for n in members if n.type != "endpoint"]
    drawn_ids = {n.id for n in drawn}
    lifted: dict[str, str] = {}
    for node in members:
        if node.type == "endpoint":
            parent = node.id.split(":", 1)[0]
            if parent in drawn_ids:
                lifted[parent] = _max_state(lifted.get(parent, "defined"), node.state)
    return [(n, _max_state(n.state, lifted.get(n.id, "defined"))) for n in drawn]


def _terrain_rows(t: ResolvedTerrainV2) -> list[tuple[str, str]]:
    """The terrain reduced to countable facts — the Markdown twin of the drawn map."""
    drawn = _drawn_nodes(t)
    reached = [n for n, state in drawn if state in ("discovered", "completed")]
    enumerated = [n for n, state in drawn if state == "completed"]
    objective = next(((n, s) for n, s in drawn if n.objective), None)
    rows = [
        ("Segments", str(len(t.groups))),
        ("Nodes", str(len(drawn))),
        ("Reached", f"{len(reached)} of {len(drawn)}"),
        ("Enumerated", f"{len(enumerated)} of {len(drawn)}"),
        ("Links active", f"{sum(1 for e in t.edges if e.active)} of {len(t.edges)}"),
    ]
    if objective is not None:
        node, state = objective
        settled = "reached" if state == "completed" else "not reached"
        rows.append(("Objective", f"{node.label} ({settled})"))
    return rows


def _partial_note(ctx: RunReportContext) -> str:
    trigger = ctx.partial_trigger or "partial"
    stopped = "terminated by the operator" if trigger == "operator" else "did not finish"
    return (
        f"PARTIAL RESULT — this run {stopped} (trigger: {trigger}). "
        "The scores below reflect only what was completed."
    )


def report_filename(ctx: RunReportContext, fmt: str) -> str:
    """A stable, filesystem-safe attachment name: xorcise-run-<id8>-<mission>.<ext>."""
    slug = re.sub(r"[^a-z0-9]+", "-", ctx.run.mission.lower()).strip("-") or "run"
    return f"xorcise-run-{ctx.run.run_id[:8]}-{slug}.{'html' if fmt == 'html' else 'md'}"


# ── Markdown ─────────────────────────────────────────────────────────────────────────────────


def _md_cell(text: str) -> str:
    """A table cell can hold neither a pipe nor a newline — flatten both."""
    return text.replace("|", r"\|").replace("\n", " ").replace("\r", " ")


def _md_kv_table(rows: Iterable[tuple[str, str]]) -> list[str]:
    out = ["| Field | Value |", "| --- | --- |"]
    out += [f"| {_md_cell(k)} | {_md_cell(v)} |" for k, v in rows]
    return out


def _md_bullets(title: str, items: Iterable[str]) -> list[str]:
    values = list(items)
    if not values:
        return []
    return [f"## {title}", "", *[f"- {v}" for v in values], ""]


def render_markdown(ctx: RunReportContext) -> str:
    """Render the run report as GitHub-flavoured Markdown."""
    grade, run = ctx.grade, ctx.run
    lines: list[str] = [f"# XORCISE Run Report — {run.mission}", ""]
    if ctx.partial:
        # No emoji anywhere in a XORCISE artifact — the wording already carries the warning.
        lines += [f"> **{_partial_note(ctx)}**", ""]
    lines += ["## Overview", "", *_md_kv_table(_metadata_rows(ctx)), ""]

    lines += [
        "## Scores",
        "",
        "| Score | Value |",
        "| --- | --- |",
        f"| **Overall** | **{_score_range(grade.overall, grade.overall_upper)}** |",
        f"| Deterministic | {_score(grade.breakdown.deterministic)} |",
        f"| Judge | {_score_range(grade.breakdown.judge, grade.judge_upper)} |",
        "",
    ]
    if grade.judge_status == "partial":
        coverage = grade.judge_coverage or 0.0
        lines += [
            f"> Partial judge result: {coverage:.0%} of rubric weight was scored. "
            "Ranges include unscored criteria.",
            "",
        ]
    elif grade.judge_status != "ok":
        detail = f" — {grade.judge_detail}" if grade.judge_detail else ""
        lines += [f"> Judge half degraded: `{grade.judge_status}`{detail}", ""]

    lines += ["## Deterministic checks", ""]
    if grade.check_breakdown:
        lines += [
            "| Result | Check | Weight | Op | Value | Blocked by | Error |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for check in grade.check_breakdown:
            lines.append(
                f"| {_verdict_label(check.passed)} | {_md_cell(check.id)} | {check.weight} "
                f"| {_md_cell(check.op)} | {_md_cell(_value(check.value))} "
                f"| {_md_cell(', '.join(check.blocked_by) or _DASH)} "
                f"| {_md_cell(check.error or _DASH)} |"
            )
        lines.append("")
    else:
        lines += ["_No deterministic checks were declared for this mission._", ""]

    lines += ["## Judge rubric", ""]
    if grade.judge_breakdown:
        for crit in grade.judge_breakdown:
            result = _score(crit.score) if crit.status == "ok" else crit.status
            lines += [
                f"### {crit.criterion_id} — {result} (weight {crit.weight})",
                "",
                crit.text,
                "",
            ]
            if crit.reason:
                lines += [f"> {crit.reason}", ""]
    else:
        lines += ["_No rubric criteria were scored._", ""]

    lines += _md_bullets("Evidence", grade.key_evidence)
    lines += _md_bullets("Deductions", grade.major_deductions)
    lines += _md_bullets("Hard fails", grade.hard_fails)

    lines += ["## Artifacts", ""]
    if ctx.artifacts:
        for a in ctx.artifacts:
            lines += [
                f"### {a.name} ({a.kind})",
                "",
                "```",
                _truncate(a.payload, _PAYLOAD_LIMIT),
                "```",
                "",
            ]
    else:
        lines += ["_The agent submitted no artifacts._", ""]

    if ctx.terrain is not None:
        lines += ["## Terrain", ""]
        if ctx.terrain.summary:
            lines += [ctx.terrain.summary, ""]
        lines += [*_md_kv_table(_terrain_rows(ctx.terrain)), ""]

    lines += ["## Telemetry", ""]
    telemetry = _telemetry_rows(ctx)
    if telemetry:
        lines += [*_md_kv_table(telemetry), ""]
    else:
        lines += ["_No telemetry snapshot was recorded for this run._", ""]

    lines += ["## Conditions", "", *_md_kv_table(_condition_rows(ctx)), ""]
    lines += [
        "---",
        "",
        f"Generated by XORCISE {ctx.version or _xorcise_version()} at "
        f"{_ts(ctx.generated_at or datetime.now(UTC))} · trace ref: "
        f"`{grade.trace_ref or _DASH}`",
        "",
    ]
    return "\n".join(lines)


# ── HTML ─────────────────────────────────────────────────────────────────────────────────────

# The brand palette, inlined. Names mirror the app's design tokens so the report and the Results
# page stay one system: a warm surface ladder, ONE amber ladder for identity/chrome, and the
# functional ladder (data/warning/failure/info) reserved for measured values and status.
_CSS = """
:root{color-scheme:dark;
--bg:#111111;--panel:#1a1a1a;--raised:#1e1e1e;--active:#232323;--chrome:#141414;
--fg:#d4d4d4;--heading:#f0f0f0;--muted:#999999;--dim:#999999;--faint:#666666;
--primary:#e8b84b;--primary-bright:#ffcf6a;--primary-soft:rgba(232,184,75,.12);
--mark:#e8b84b;--wordmark:#f2ead6;
--data:#6ee7a8;--ok:#6ee7a8;--warn:#f0d890;--err:#ff5f57;--info:#8ab4f8;
--border:rgba(255,255,255,.09);--radius:6px}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;padding:0;background:var(--bg);color:var(--fg);
font:13px/1.6 "JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
font-variant-numeric:tabular-nums}
main{max-width:66rem;margin:0 auto;padding:1.5rem 1.25rem 3rem}
h1{font-size:1.5rem;color:var(--heading);margin:.15rem 0 .25rem;letter-spacing:.01em}
h2{font-size:.68rem;letter-spacing:1.5px;text-transform:uppercase;color:var(--dim);
margin:2rem 0 .6rem;font-weight:600;display:flex;align-items:center;gap:.55rem}
h2::before{content:"";width:10px;height:1px;background:var(--primary);flex:none}
h3{font-size:.9rem;color:var(--heading);margin:1.1rem 0 .35rem}
p{margin:.4rem 0}
a{color:var(--primary)}
.masthead{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap;
background:var(--chrome);border-bottom:1px solid var(--border);padding:.7rem 1.25rem}
.masthead-in{max-width:66rem;margin:0 auto;width:100%;display:flex;align-items:center;
justify-content:space-between;gap:1rem;flex-wrap:wrap}
.lockup{display:flex;align-items:center;gap:14px}
.wordmark{color:var(--wordmark);font-weight:700;font-size:.95rem;letter-spacing:.1em}
.wordmark .dot{color:var(--primary)}
.stamp{margin:0;font-size:.62rem;letter-spacing:2px;text-transform:uppercase;color:var(--dim)}
.eyebrow{margin:0;font-size:.62rem;letter-spacing:2px;text-transform:uppercase;color:var(--dim)}
.runid{margin:0;font-size:.72rem;color:var(--muted);word-break:break-all}
.chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.55rem 0 0}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
padding:1rem 1.15rem}
.scorecard{background:var(--raised);padding:1.15rem 1.25rem;display:flex;gap:1.5rem;
align-items:center;flex-wrap:wrap}
.dial{flex:none}
.dial-cap{display:block;text-align:center;font-size:.6rem;letter-spacing:1.2px;
text-transform:uppercase;color:var(--dim);margin-top:.35rem}
.meters{flex:1 1 20rem;min-width:16rem}
.meter+.meter{margin-top:.9rem}
.meter-head{display:flex;align-items:baseline;justify-content:space-between;gap:.5rem}
.cap{font-size:.6rem;letter-spacing:1.2px;text-transform:uppercase;color:var(--dim)}
.meter-val{font-size:.78rem;font-weight:700}
.track{height:4px;border-radius:2px;background:var(--active);margin:.3rem 0 .25rem;
overflow:hidden}
.track span{display:block;height:100%;border-radius:2px}
.help-text{margin:0;font-size:.66rem;line-height:1.5;color:var(--muted)}
.exact{margin:.9rem 0 0;padding-top:.8rem;border-top:1px solid var(--border);width:100%;
font-size:.72rem;color:var(--muted)}
.exact strong{color:var(--heading);font-weight:700}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));gap:.9rem 1.25rem}
.kpi .v{display:block;font-size:1.15rem;font-weight:700;color:var(--heading);margin-top:.15rem}
.banner{border:1px solid var(--border);border-left:3px solid var(--warn);
background:var(--primary-soft);border-radius:var(--radius);padding:.7rem .95rem;margin:1.1rem 0;
font-size:.8rem}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.78rem;min-width:30rem}
th,td{text-align:left;padding:.45rem .7rem;border-bottom:1px solid var(--border);
vertical-align:top;font-variant-numeric:tabular-nums}
th{color:var(--dim);font-weight:600;font-size:.66rem;letter-spacing:1px;text-transform:uppercase}
td.k{color:var(--muted);white-space:nowrap;width:14rem}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;border-radius:4px;padding:.05rem .45rem;font-size:.66rem;
letter-spacing:.6px;font-weight:600;border:1px solid transparent}
.pass{background:rgba(110,231,168,.12);color:var(--data);border-color:rgba(110,231,168,.25)}
.fail{background:rgba(255,95,87,.12);color:var(--err);border-color:rgba(255,95,87,.25)}
.kind,.chip{background:transparent;color:var(--muted);border-color:var(--border);
text-transform:uppercase}
ul{margin:.3rem 0;padding-left:1.15rem}
li{margin:.2rem 0}
li.err{color:var(--err)}
/* pre/code must name the mono stack EXPLICITLY. The UA stylesheet sets
   `pre,code{font-family:monospace}`, and a UA declaration ON THE ELEMENT beats the value
   inherited from <body> — so these blocks never actually rendered in JetBrains Mono, they
   rendered in whatever generic monospace the reader's browser defaults to. An artifact
   payload is agent-submitted terminal output, the single most mono-critical text in the
   report, and it was silently off-brand. Found by reading computed styles, not the sheet. */
pre,code,kbd,samp{font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--chrome);border:1px solid var(--border);border-radius:var(--radius);
padding:.75rem;overflow-x:auto;font-size:.72rem;color:var(--fg);white-space:pre-wrap;
word-break:break-word;max-height:32rem;overflow-y:auto}
blockquote{margin:.35rem 0;padding-left:.75rem;border-left:2px solid var(--border);
color:var(--muted);font-size:.78rem}
.empty{color:var(--muted);font-style:italic;font-size:.78rem}
.map{width:100%;height:auto;display:block;max-width:760px;margin:0 auto}
.legend{display:flex;flex-wrap:wrap;gap:.35rem 1rem;margin:.85rem 0 0;padding:0;list-style:none;
font-size:.62rem;letter-spacing:.5px;text-transform:uppercase;color:var(--muted)}
.legend li{display:flex;align-items:center;gap:.4rem;margin:0}
.swatch{width:8px;height:8px;border-radius:50%;flex:none}
footer{margin-top:2.5rem;padding-top:1rem;border-top:1px solid var(--border);color:var(--muted);
font-size:.7rem;display:flex;flex-wrap:wrap;gap:.5rem 1.25rem;align-items:center}
@media print{
:root{color-scheme:light;--bg:#FAF8F2;--panel:#FAF8F2;--raised:#FAF8F2;--active:#e6e0d2;
--chrome:#FAF8F2;--fg:#14120E;--heading:#14120E;--muted:#4a463d;--dim:#4a463d;--faint:#8a8578;
--primary:#C49A2A;--mark:#C49A2A;--wordmark:#14120E;--border:rgba(20,18,14,.14);
--data:#1f7a4d;--ok:#1f7a4d;--warn:#8a6a12;--err:#b3261e;--info:#245b9c}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#FAF8F2;color:#14120E}
main{max-width:none;padding:0 0 1rem}
.masthead{padding:0 0 .6rem}
.card,.scorecard{border-color:rgba(20,18,14,.14)}
pre{max-height:none}
h2{break-after:avoid}
.card,.scorecard,tr{break-inside:avoid}
}
"""

# The crosshair mark, inline (no image request, no font). Ring r=15 with the cross contained
# inside it, stroke 2.4 — the brand's only permitted construction. `--mark` (not a literal) so the
# print register can swap it for the deep print amber without touching the markup.
_MARK = (
    "<svg class='mark' width='26' height='26' viewBox='0 0 40 40' aria-hidden='true'>"
    "<circle cx='20' cy='20' r='15' fill='none' stroke='var(--mark)' stroke-width='2.4'/>"
    "<path d='M20 8V32 M8 20H32' fill='none' stroke='var(--mark)' stroke-width='2.4' "
    "stroke-linecap='round'/></svg>"
)
# Letters warm (`--wordmark` #f2ead6, never the console's cooler neutral), dot ALWAYS amber.
_WORDMARK = "<span class='wordmark'>XORCISE<span class='dot'>.</span>AI</span>"
_LOCKUP = f"<div class='lockup'>{_MARK}{_WORDMARK}</div>"
# Same mark as a data: URI favicon — the tab of a downloaded report is still XORCISE. No request
# leaves the document (a data: URI is the file's own bytes); the xmlns is an XML namespace name.
_FAVICON = (
    '<link rel="icon" href="data:image/svg+xml,'
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'>"
    "<circle cx='20' cy='20' r='15' fill='none' stroke='%23e8b84b' stroke-width='2.4'/>"
    "<path d='M20 8V32 M8 20H32' fill='none' stroke='%23e8b84b' stroke-width='2.4'/>"
    '</svg>">'
)

_RING_R = 52.0
_RING_C = 2 * math.pi * _RING_R


def _e(text: object) -> str:
    """html.escape for any value — the single choke point for untrusted text on the HTML path."""
    return html.escape(str(text), quote=True)


def _tone(value: float) -> str:
    """A measured 0..1 on the FUNCTIONAL ladder — never amber (amber is identity, not a value).

    Mirrors the Results page's pass/marginal/fail read, with the brand's own stops: >=80% data
    green, >=50% warning, else failure."""
    if value >= 0.8:
        return "var(--data)"
    if value >= 0.5:
        return "var(--warn)"
    return "var(--err)"


def _pct(value: float) -> str:
    return f"{max(0.0, min(1.0, value)) * 100:.0f}%"


def _dial(value: float, label: str) -> str:
    """The headline score as an inline SVG donut — the report's twin of the app's score ring.

    Zero dependencies: one track circle, one arc circle whose `stroke-dasharray` is the score's
    share of the circumference, rotated so the arc starts at 12 o'clock."""
    v = max(0.0, min(1.0, value))
    arc = _RING_C * v
    tone = _tone(v)
    return (
        "<div class='dial'>"
        f"<svg width='124' height='124' viewBox='0 0 124 124' role='img' "
        f"aria-label='{_e(label)} score {_pct(v)}'>"
        f"<circle cx='62' cy='62' r='{_RING_R:g}' fill='none' stroke='var(--active)' "
        "stroke-width='10'/>"
        f"<circle cx='62' cy='62' r='{_RING_R:g}' fill='none' stroke='{tone}' stroke-width='10' "
        f"stroke-dasharray='{arc:.2f} {_RING_C:.2f}' transform='rotate(-90 62 62)'/>"
        f"<text x='62' y='62' text-anchor='middle' dominant-baseline='central' fill='{tone}' "
        "font-family='JetBrains Mono,ui-monospace,monospace' font-size='26' font-weight='700'>"
        f"{_pct(v)}</text></svg>"
        f"<span class='dial-cap'>{_e(label)}</span></div>"
    )


def _meter(label: str, value: float, help_text: str, *, muted: bool = False) -> str:
    """One half-score as a labelled bar — the app's BreakdownBar, offline."""
    v = max(0.0, min(1.0, value))
    tone = "var(--muted)" if muted else _tone(v)
    return (
        "<div class='meter'><div class='meter-head'>"
        f"<span class='cap'>{_e(label)}</span>"
        f"<span class='meter-val' style='color:{tone}'>{_pct(v)}</span></div>"
        f"<div class='track'><span style='width:{v * 100:.1f}%;background:{tone}'></span></div>"
        f"<p class='help-text'>{_e(help_text)}</p></div>"
    )


def _chip(text: str) -> str:
    return f"<span class='pill chip'>{_e(text)}</span>"


def _kpi_strip(ctx: RunReportContext) -> str:
    """The at-a-glance row the Results page opens with: scores first, then telemetry."""
    grade, stats = ctx.grade, ctx.stats
    tiles: list[tuple[str, str, str]] = [
        (
            "Overall",
            (
                f"{_pct(grade.overall)}–{_pct(grade.overall_upper)}"
                if grade.overall_upper is not None and grade.overall_upper > grade.overall + 1e-9
                else _pct(grade.overall)
            ),
            _tone(grade.overall),
        ),
        ("Deterministic", _pct(grade.breakdown.deterministic), "var(--heading)"),
        (
            "Judge",
            (
                f"{_pct(grade.breakdown.judge)}–{_pct(grade.judge_upper)}"
                if grade.judge_upper is not None
                and grade.judge_upper > grade.breakdown.judge + 1e-9
                else _pct(grade.breakdown.judge)
            ),
            "var(--heading)",
        ),
        ("Elapsed", _duration(_elapsed_seconds(ctx)), "var(--heading)"),
        ("Tokens", f"{stats.tokens.total:,}" if stats else _DASH, "var(--heading)"),
        ("Tool calls", f"{stats.counts.tool_calls:,}" if stats else _DASH, "var(--heading)"),
        ("Model calls", f"{stats.counts.model_calls:,}" if stats else _DASH, "var(--heading)"),
    ]
    cells = "".join(
        f"<div><span class='cap'>{_e(label)}</span>"
        f"<span class='v' style='color:{tone}'>{_e(value)}</span></div>"
        for label, value, tone in tiles
    )
    return f"<div class='card kpi'>{cells}</div>"


def _scorecard(ctx: RunReportContext) -> str:
    """The big card: overall dial + the two half-score meters + the checks-passed line.

    The card's left rule carries the verdict tone, so the whole block reads pass/marginal/fail
    before a single number is parsed — the same hierarchy as the Results page."""
    grade = ctx.grade
    passed = sum(1 for c in grade.check_breakdown if c.passed)
    total = len(grade.check_breakdown)
    tone = _tone(grade.overall)
    summary = ""
    if total:
        plural = "s" if total != 1 else ""
        summary = (
            f"<strong>{passed}</strong> of <strong>{total}</strong> deterministic check{plural} "
            "passed"
        )
        if grade.judge_breakdown:
            n = len(grade.judge_breakdown)
            summary += f" &middot; <strong>{n}</strong> judge criteri{'a' if n != 1 else 'on'}"
        summary += " &middot; "
    exact = (
        f"{summary}overall <strong>{_e(_score_range(grade.overall, grade.overall_upper))}</strong> "
        f"&middot; deterministic "
        f"{_e(_score(grade.breakdown.deterministic))} &middot; judge "
        f"{_e(_score_range(grade.breakdown.judge, grade.judge_upper))}"
    )
    return (
        f"<div class='card scorecard' style='border-left:3px solid {tone}'>"
        + _dial(grade.overall, "Overall")
        + "<div class='meters'>"
        + _meter(
            "Deterministic",
            grade.breakdown.deterministic,
            "Hard checks scored by the control plane — flag match, artefact presence, "
            "deterministic asserts.",
        )
        + _meter(
            "Judge",
            grade.breakdown.judge,
            (
                f"Conservative lower bound; possible range "
                f"{_pct(grade.breakdown.judge)}–{_pct(grade.judge_upper)} at "
                f"{_pct(grade.judge_coverage or 0.0)} coverage."
                if grade.judge_upper is not None
                and grade.judge_upper > grade.breakdown.judge + 1e-9
                else "LLM-judge rubric score (50% of overall) — 0 when no judge ran."
            ),
            muted=grade.judge_status not in ("ok", "partial"),
        )
        + "</div>"
        + f"<p class='exact'>{exact}</p>"
        + "</div>"
    )


def _html_kv_table(rows: Iterable[tuple[str, str]]) -> str:
    body = "".join(f"<tr><td class='k'>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in rows)
    return f"<div class='wrap'><table><tbody>{body}</tbody></table></div>"


def _html_list(title: str, items: Iterable[str], *, tone: str = "") -> str:
    values = list(items)
    if not values:
        return ""
    cls = f" class='{tone}'" if tone else ""
    body = "".join(f"<li{cls}>{_e(v)}</li>" for v in values)
    return f"<h2>{_e(title)}</h2><ul>{body}</ul>"


# ── terrain (static SVG twin of the app's terrain map) ────────────────────────────────────────
#
# A faithful port of the frontend's deterministic layout (`terrain-layout.ts`): authored GROUPS
# stack as full-width horizontal bands ordered by `order`; each band's member nodes sit evenly
# across it; `endpoint` nodes (`hs:join`, `rc:artifacts`, …) collapse onto their parent service
# so the map stays readable, lifting their state onto that parent. Same constants, so the report
# and the app draw the same picture.

_T_WIDTH = 760
_T_PAD = 20
_T_GAP = 18
_T_HEADER = 26
_T_ROW = 22
_T_R = 16
_T_BOTTOM = 30
_T_ZONE = _T_HEADER + _T_ROW + _T_R + _T_BOTTOM
_T_LABEL_CHAR_W = 6.3
_T_LABEL_GUTTER = 10


def _clamp_label(label: str, slot_w: float) -> str:
    """Fit a label to its horizontal slot so it can never spill under its neighbour's."""
    budget = max(4, int((slot_w - _T_LABEL_GUTTER) / _T_LABEL_CHAR_W))
    return label if len(label) <= budget else f"{label[: budget - 1]}…"


def _node_colour(node: TerrainNodeV2, state: str, group_kind: str) -> str:
    """The app's terrain palette: the agent node is the one amber object; the objective is red
    until it is reached; otherwise grey (unknown) → blue (discovered) → green (enumerated)."""
    if node.objective:
        return "var(--data)" if state == "completed" else "var(--err)"
    if group_kind == "agent":
        return "var(--primary)"
    if state == "completed":
        return "var(--data)"
    if state == "discovered":
        return "var(--info)"
    return "var(--faint)"


_TERRAIN_LEGEND = (
    ("unknown", "var(--faint)"),
    ("agent", "var(--primary)"),
    ("discovered", "var(--info)"),
    ("enumerated", "var(--data)"),
    ("objective", "var(--err)"),
)


def _terrain_svg(t: ResolvedTerrainV2) -> str:
    """Draw the folded terrain. Returns "" when there is nothing to draw (never an empty frame)."""
    groups = sorted(t.groups, key=lambda g: g.order)
    if not groups:
        return ""
    drawn = _drawn_nodes(t)
    if not drawn:
        return ""
    drawn_ids = {n.id for n, _ in drawn}
    # An edge may target a collapsed endpoint — re-anchor it onto the parent service it folded on.
    parent_of = {
        n.id: n.id.split(":", 1)[0]
        for n in t.nodes
        if n.type == "endpoint" and n.id.split(":", 1)[0] in drawn_ids
    }

    kind_of = {g.id: g.kind for g in groups}
    band_w = _T_WIDTH - 2 * _T_PAD
    boxes: list[str] = []
    dots: list[str] = []
    at: dict[str, tuple[float, float]] = {}
    band_anchor: dict[str, tuple[float, float]] = {}
    y = float(_T_PAD)
    for group in groups:
        solid = group.discovered
        boxes.append(
            f"<rect x='{_T_PAD}' y='{y:g}' width='{band_w}' height='{_T_ZONE}' rx='4' fill='none' "
            f"stroke='var(--{'info' if solid else 'faint'})' stroke-width='1'"
            + ("" if solid else " stroke-dasharray='4 4' stroke-opacity='.7'")
            + "/>"
            f"<text x='{_T_PAD + 8}' y='{y + 15:g}' fill='var(--muted)' font-size='9' "
            f"letter-spacing='.8'>{_e(group.label.upper())}</text>"
        )
        band_anchor[group.id] = (_T_PAD + band_w / 2, y)
        in_band = [(n, s) for n, s in drawn if n.group == group.id]
        slot_w = band_w / (len(in_band) or 1)
        for i, (node, state) in enumerate(in_band):
            cx = _T_PAD + slot_w * (i + 0.5)
            cy = y + _T_HEADER + _T_ROW
            at[node.id] = (cx, cy)
            colour = _node_colour(node, state, kind_of.get(node.group, "segment"))
            dash = " stroke-dasharray='3 3'" if state == "defined" else ""
            glyph = (
                f"<path d='M{cx - 4.5:.1f} {cy - 4.5:.1f}l9 9M{cx + 4.5:.1f} {cy - 4.5:.1f}"
                f"l-9 9' stroke='{colour}' stroke-width='1.5' stroke-linecap='round'/>"
                if node.objective
                else f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='4' fill='{colour}'/>"
            )
            label = _clamp_label(node.label, slot_w)
            dots.append(
                f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{_T_R}' fill='var(--panel)' "
                f"stroke='{colour}' stroke-width='1.5'{dash}/>{glyph}"
                f"<text x='{cx:.1f}' y='{cy + _T_R + 13:.1f}' text-anchor='middle' "
                f"fill='var(--fg)' font-size='10'>{_e(label)}</text>"
            )
        y += _T_ZONE + _T_GAP
    height = y - _T_GAP + _T_PAD

    def anchor(element_id: str) -> tuple[float, float] | None:
        if element_id in at:
            return at[element_id]
        parent = parent_of.get(element_id)
        if parent is not None and parent in at:
            return at[parent]
        return band_anchor.get(element_id)

    lines: list[str] = []
    for edge in t.edges:
        a, b = anchor(edge.src), anchor(edge.dst)
        if a is None or b is None or a == b:
            continue
        stroke = "var(--info)" if edge.active else "var(--border)"
        lines.append(
            f"<line x1='{a[0]:.1f}' y1='{a[1]:.1f}' x2='{b[0]:.1f}' y2='{b[1]:.1f}' "
            f"stroke='{stroke}' stroke-width='{1.25 if edge.active else 1}'"
            + ("" if edge.active else " stroke-opacity='.8'")
            + "/>"
        )

    body = "".join(boxes) + "".join(lines) + "".join(dots)
    return (
        f"<svg class='map' viewBox='0 0 {_T_WIDTH} {height:g}' width='{_T_WIDTH}' "
        f"height='{height:g}' role='img' aria-label='Resolved terrain map' "
        "font-family='JetBrains Mono,ui-monospace,monospace'>"
        f"{body}</svg>"
    )


def _terrain_section(t: ResolvedTerrainV2) -> str:
    """The TERRAIN block: the authored summary, the drawn map, a legend and the counted facts."""
    svg = _terrain_svg(t)
    if not svg:
        return ""
    legend = "".join(
        f"<li><span class='swatch' style='background:{colour}'></span>{_e(label)}</li>"
        for label, colour in _TERRAIN_LEGEND
    )
    summary = f"<p class='help-text'>{_e(t.summary)}</p>" if t.summary else ""
    return (
        "<h2>Terrain</h2><div class='card'>"
        f"{summary}{svg}<ul class='legend'>{legend}</ul></div>" + _html_kv_table(_terrain_rows(t))
    )


def render_html(ctx: RunReportContext) -> str:
    """Render the run report as a standalone, dark-themed, BRANDED HTML document.

    Self-contained by design: inline CSS, inline SVG, no external font/script/image — the file has
    to look right (and stay XORCISE) opened from a download folder or forwarded as an attachment.
    """
    grade, run = ctx.grade, ctx.run
    generated = _ts(ctx.generated_at or datetime.now(UTC))
    version = ctx.version or _xorcise_version()
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>XORCISE Run Report — {_e(run.mission)}</title>",
        _FAVICON,
        f"<style>{_CSS}</style></head><body>",
        f"<header class='masthead'><div class='masthead-in'>{_LOCKUP}"
        "<p class='stamp'>Run report</p></div></header><main>",
        "<p class='eyebrow'>Run result</p>",
        f"<h1>{_e(run.mission)}</h1>",
        f"<p class='runid'>{_e(run.run_id)}</p>",
        "<div class='chips'>"
        + _chip(_status_line(ctx))
        + _chip(f"agent {ctx.agent_name or ctx.run.agent_id} v{ctx.conditions.agent_version}")
        + _chip(f"harness {run.source_agent or 'generic'}")
        + "</div>",
    ]
    if ctx.partial:
        parts.append(f"<div class='banner'>{_e(_partial_note(ctx))}</div>")

    parts += [_kpi_strip(ctx), "<h2>Scorecard</h2>", _scorecard(ctx)]
    if grade.judge_status == "partial":
        coverage = grade.judge_coverage or 0.0
        parts.append(
            f"<div class='banner'>Partial judge result: <strong>{coverage:.0%}</strong> of rubric "
            "weight was scored. Displayed ranges include unscored criteria.</div>"
        )
    elif grade.judge_status != "ok":
        detail = f" — {grade.judge_detail}" if grade.judge_detail else ""
        parts.append(
            f"<div class='banner'>Judge half degraded: "
            f"<strong>{_e(grade.judge_status)}</strong>{_e(detail)}</div>"
        )

    parts.append("<h2>Deterministic checks</h2>")
    if grade.check_breakdown:
        rows = "".join(
            "<tr>"
            f"<td><span class='pill {_verdict_class(check.passed)}'>"
            f"{_verdict_label(check.passed)}</span></td>"
            f"<td>{_e(check.id)}</td><td>{_e(check.weight)}</td><td>{_e(check.op)}</td>"
            f"<td>{_e(_value(check.value))}</td>"
            f"<td>{_e(', '.join(check.blocked_by) or _DASH)}</td>"
            f"<td>{_e(check.error or _DASH)}</td>"
            "</tr>"
            for check in grade.check_breakdown
        )
        parts.append(
            "<div class='wrap'><table><thead><tr><th>Result</th><th>Check</th><th>Weight</th>"
            f"<th>Op</th><th>Value</th><th>Blocked by</th><th>Error</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            "</table></div>"
        )
    else:
        parts.append("<p class='empty'>No deterministic checks were declared for this mission.</p>")

    parts.append("<h2>Judge rubric</h2>")
    if grade.judge_breakdown:
        for crit in grade.judge_breakdown:
            result = _score(crit.score) if crit.status == "ok" else crit.status
            parts.append(
                f"<h3>{_e(crit.criterion_id)} — {_e(result)} "
                f"(weight {_e(crit.weight)})</h3><p>{_e(crit.text)}</p>"
            )
            if crit.reason:
                parts.append(f"<blockquote>{_e(crit.reason)}</blockquote>")
    else:
        parts.append("<p class='empty'>No rubric criteria were scored.</p>")

    parts.append(_html_list("Evidence", grade.key_evidence))
    parts.append(_html_list("Deductions", grade.major_deductions))
    parts.append(_html_list("Hard fails", grade.hard_fails, tone="err"))

    if ctx.terrain is not None:
        parts.append(_terrain_section(ctx.terrain))

    parts.append("<h2>Artifacts</h2>")
    if ctx.artifacts:
        for a in ctx.artifacts:
            parts.append(
                f"<h3>{_e(a.name)} <span class='pill kind'>{_e(a.kind)}</span></h3>"
                f"<pre>{_e(_truncate(a.payload, _PAYLOAD_LIMIT))}</pre>"
            )
    else:
        parts.append("<p class='empty'>The agent submitted no artifacts.</p>")

    parts.append("<h2>Activity</h2>")
    telemetry = _telemetry_rows(ctx)
    parts.append(
        _html_kv_table(telemetry)
        if telemetry
        else "<p class='empty'>No telemetry snapshot was recorded for this run.</p>"
    )

    parts += ["<h2>Overview</h2>", _html_kv_table(_metadata_rows(ctx))]
    parts += ["<h2>Conditions</h2>", _html_kv_table(_condition_rows(ctx))]
    parts.append(
        f"<footer>{_LOCKUP}<span>Generated by XORCISE {_e(version)} at {_e(generated)}</span>"
        f"<span>trace ref: {_e(grade.trace_ref or _DASH)}</span></footer>"
    )
    parts.append("</main></body></html>")
    return "".join(parts)
