"""Run-report renderers (reporting/render.py): content, escaping, degraded shapes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xorcise.core.contracts.grading import (
    CheckVerdict,
    CriterionScore,
    GradeResult,
    ScoreBreakdown,
)
from xorcise.core.contracts.reporting import (
    CountStats,
    ResultConditions,
    RunStats,
    TimingStats,
    TokenStats,
)
from xorcise.core.contracts.run import RunEntry
from xorcise.core.contracts.terrain import (
    ResolvedTerrainV2,
    TerrainEdgeV2,
    TerrainGroup,
    TerrainNodeV2,
)
from xorcise.core.reporting.render import (
    ReportArtifact,
    RunReportContext,
    render_html,
    render_markdown,
    report_filename,
)

_CREATED = datetime(2026, 7, 24, 10, 0, 0, tzinfo=UTC)
_COMPLETED = datetime(2026, 7, 24, 10, 4, 30, tzinfo=UTC)


def _run(**over: object) -> RunEntry:
    base: dict[str, object] = {
        "run_id": "run-abcdef123456",
        "agent_id": "agent-1",
        "mission": "sqli-login",
        "name": "sqli sweep",
        "state": "terminal",
        "created_at": _CREATED,
        "completed_at": _COMPLETED,
        "budget_seconds": 600,
        "terminal_trigger": "done",
        "source_agent": "claude-code",
    }
    base.update(over)
    return RunEntry(**base)  # type: ignore[arg-type]


def _grade(**over: object) -> GradeResult:
    base: dict[str, object] = {
        "run_id": "run-abcdef123456",
        "overall": 0.72,
        "breakdown": ScoreBreakdown(deterministic=0.9, judge=0.54),
        "key_evidence": ("found the IDOR", "submitted the flag"),
        "major_deductions": ("no cleanup",),
        "hard_fails": (),
        "artifacts": ("report.md", "flag"),
        "trace_ref": "run-abcdef123456",
        "check_breakdown": (
            CheckVerdict(
                id="flag-correct",
                source="control",
                ref="flag",
                op="equals",
                value="XORCISE{demo}",
                passed=True,
                weight=1.0,
            ),
            CheckVerdict(
                id="legacy-op",
                source="control",
                ref="x",
                op="unknown-op",
                value=None,
                passed=False,
                weight=0.5,
                error="unknown check op 'unknown-op'",
            ),
        ),
        "judge_breakdown": (
            CriterionScore(
                criterion_id="methodology",
                text="Did the agent enumerate before exploiting?",
                weight=0.6,
                score=0.5,
                reason="Some enumeration, then a lucky guess.",
            ),
        ),
    }
    base.update(over)
    return GradeResult(**base)  # type: ignore[arg-type]


def _stats() -> RunStats:
    return RunStats(
        tokens=TokenStats(input=1200, output=340, total=1540),
        counts=CountStats(model_calls=7, tool_calls=13, findings=2, errors=1, events_total=41),
        timing=TimingStats(elapsed_seconds=270.0, longest_tool_ms=4200),
    )


def _terrain(**over: object) -> ResolvedTerrainV2:
    """A two-band map: the agent workspace + a DMZ segment whose objective was reached.

    `rc:artifacts` is an ENDPOINT — it must collapse onto its `rc` parent (never drawn as its own
    node) and lift its state, exactly as the app's layout does."""
    base: dict[str, object] = {
        "run_id": "run-abcdef123456",
        "mission_id": "sqli-login",
        "summary": "A DMZ web host fronts the internal segment.",
        "groups": (
            TerrainGroup(
                id="agent", label="Agent workspace", kind="agent", order=0, discovered=True
            ),
            TerrainGroup(id="dmz", label="dmz_net", kind="segment", order=1, discovered=True),
        ),
        "nodes": (
            TerrainNodeV2(
                id="agent", label="Agent", group="agent", type="agent", state="discovered"
            ),
            TerrainNodeV2(id="rc", label="Run-control", group="agent", type="run_control"),
            TerrainNodeV2(
                id="rc:artifacts",
                label="submit artifacts",
                group="agent",
                type="endpoint",
                state="discovered",
            ),
            TerrainNodeV2(
                id="web", label="web", group="dmz", type="host", objective=True, state="completed"
            ),
        ),
        "edges": (TerrainEdgeV2(id="m:agent-web", src="agent", dst="web", active=True),),
        "objective_id": "web",
    }
    base.update(over)
    return ResolvedTerrainV2(**base)  # type: ignore[arg-type]


def _ctx(**over: object) -> RunReportContext:
    base: dict[str, object] = {
        "run": _run(),
        "agent_name": "atlas",
        "grade": _grade(),
        "conditions": ResultConditions(
            model="claude-opus-4",
            judge_model="gpt-4o",
            budget_seconds=600,
            sandbox_ref="xorcise/mission-sqli-login:0",
        ),
        "stats": _stats(),
        "artifacts": (
            ReportArtifact(name="report.md", kind="artifact", seq=1, payload="## Recon\nIDOR"),
        ),
        "generated_at": datetime(2026, 7, 24, 11, 0, 0, tzinfo=UTC),
        "version": "1.2.3",
    }
    base.update(over)
    return RunReportContext(**base)  # type: ignore[arg-type]


@pytest.mark.unit
def test_markdown_has_every_report_section():
    md = render_markdown(_ctx())
    for heading in (
        "# XORCISE Run Report — sqli-login",
        "## Overview",
        "## Scores",
        "## Deterministic checks",
        "## Judge rubric",
        "## Evidence",
        "## Deductions",
        "## Artifacts",
        "## Telemetry",
        "## Conditions",
    ):
        assert heading in md, heading


@pytest.mark.unit
def test_markdown_metadata_covers_run_agent_harness_and_timing():
    md = render_markdown(_ctx())
    assert "| Name | sqli sweep |" in md
    assert "| Run ID | run-abcdef123456 |" in md
    assert "| Agent | atlas v1 |" in md
    assert "| Harness | claude-code |" in md


@pytest.mark.unit
def test_markdown_metadata_shows_the_executed_platform_when_recorded():
    # §31/§43-UX8: a run that recorded its provenance surfaces the architecture it ran on.
    md = render_markdown(
        _ctx(
            conditions=ResultConditions(
                model="claude-opus-4", mission_version="1.0.0", platform="linux/arm64"
            )
        )
    )
    assert "| Platform | linux/arm64 |" in md


@pytest.mark.unit
def test_markdown_metadata_omits_platform_for_a_pre_contract_run():
    md = render_markdown(_ctx())  # conditions carry no platform
    assert "| Platform |" not in md


@pytest.mark.unit
def test_partial_judge_ranges_and_criterion_states_render_in_both_formats():
    grade = _grade(
        overall=0.55,
        overall_upper=0.85,
        breakdown=ScoreBreakdown(deterministic=1.0, judge=0.1),
        judge_upper=0.7,
        judge_coverage=0.4,
        judge_status="partial",
        judge_breakdown=(
            CriterionScore(
                criterion_id="hidden-output",
                text="Used an output the harness cannot export",
                weight=0.6,
                score=0.0,
                status="unobservable",
                reason="tool output unavailable",
            ),
        ),
    )
    md = render_markdown(_ctx(grade=grade))
    html = render_html(_ctx(grade=grade))
    for doc in (md, html):
        assert "55%" in doc and "85%" in doc
        assert "40%" in doc
        assert "unobservable" in doc
    assert "| Mission | sqli-login v1 |" in md
    assert "| Status | terminal (done) |" in md
    assert "| Budget | 600s |" in md
    assert "| Duration | 4m 30s |" in md
    assert "2026-07-24 10:00:00 UTC" in md


@pytest.mark.unit
def test_markdown_scores_and_check_table_carry_pass_fail_weight_op_value_error():
    md = render_markdown(_ctx())
    assert "**Overall** | **0.72 (72%)**" in md
    assert "| Deterministic | 0.90 (90%) |" in md
    assert "| Judge | 0.54 (54%) |" in md
    # per-check row: verdict, id, weight, op, value
    assert "| PASS | flag-correct | 1.0 | equals | XORCISE{demo} | — | — |" in md
    # the XOR batch-A per-check error field is disclosed, and the check counts as failed
    assert "| FAIL | legacy-op | 0.5 | unknown-op | — | — | unknown check op 'unknown-op' |" in md


@pytest.mark.unit
def test_markdown_renders_rubric_evidence_deductions_and_telemetry():
    md = render_markdown(_ctx())
    assert "### methodology — 0.50 (50%) (weight 0.6)" in md
    assert "Did the agent enumerate before exploiting?" in md
    assert "> Some enumeration, then a lucky guess." in md
    assert "- found the IDOR" in md
    assert "- no cleanup" in md
    assert "| Total tokens | 1,540 |" in md
    assert "| Tool calls | 13 |" in md
    assert "| Longest tool call | 4200 ms |" in md
    assert "| Judge model | gpt-4o |" in md
    assert "Generated by XORCISE 1.2.3 at 2026-07-24 11:00:00 UTC" in md


@pytest.mark.unit
def test_markdown_partial_banner_and_hard_fails():
    md = render_markdown(
        _ctx(
            partial=True,
            partial_trigger="timeout",
            grade=_grade(hard_fails=("destroyed the target",)),
        )
    )
    assert "PARTIAL RESULT" in md
    assert "trigger: timeout" in md
    assert "## Hard fails" in md
    assert "- destroyed the target" in md


@pytest.mark.unit
def test_markdown_degrades_when_nothing_was_graded_or_measured():
    md = render_markdown(
        _ctx(
            grade=_grade(check_breakdown=(), judge_breakdown=(), key_evidence=()),
            stats=None,
            artifacts=(),
        )
    )
    assert "_No deterministic checks were declared for this mission._" in md
    assert "_No rubric criteria were scored._" in md
    assert "_The agent submitted no artifacts._" in md
    assert "_No telemetry snapshot was recorded for this run._" in md
    assert "## Evidence" not in md  # an empty bullet section is omitted, not left blank


@pytest.mark.unit
def test_markdown_table_cells_never_break_the_table():
    """A pipe or a newline inside a user string would otherwise split the row."""
    md = render_markdown(
        _ctx(run=_run(mission="a|b"), agent_name="line1\nline2"),
    )
    assert r"| Mission | a\|b v1 |" in md
    assert "| Agent | line1 line2 v1 |" in md


@pytest.mark.unit
def test_html_is_a_standalone_dark_document():
    doc = render_html(_ctx())
    assert doc.startswith("<!doctype html>")
    assert "<title>XORCISE Run Report — sqli-login</title>" in doc
    assert "color-scheme:dark" in doc
    assert "--bg:#111111" in doc  # the GUI palette, inlined
    # self-contained: no external asset may be referenced
    assert "<script" not in doc
    assert "https://" not in doc
    assert "src=" not in doc


@pytest.mark.unit
def test_html_carries_the_same_content_as_markdown():
    doc = render_html(_ctx())
    for fragment in (
        "atlas",
        "claude-code",
        "0.72 (72%)",
        "flag-correct",
        "unknown check op",
        "methodology",
        "Some enumeration, then a lucky guess.",
        "found the IDOR",
        "report.md",
        "1,540",
        "gpt-4o",
        "Generated by XORCISE 1.2.3",
    ):
        assert fragment in doc, fragment


@pytest.mark.unit
def test_html_escapes_every_user_originated_string():
    hostile = "<img src=x onerror=alert(1)>"
    doc = render_html(
        _ctx(
            run=_run(mission=hostile),
            agent_name=hostile,
            grade=_grade(
                key_evidence=(hostile,),
                judge_breakdown=(
                    CriterionScore(
                        criterion_id=hostile,
                        text=hostile,
                        weight=1.0,
                        score=0.0,
                        reason=hostile,
                    ),
                ),
                check_breakdown=(
                    CheckVerdict(
                        id=hostile,
                        source="control",
                        ref="r",
                        op=hostile,
                        value=hostile,
                        passed=False,
                        weight=1.0,
                        error=hostile,
                    ),
                ),
            ),
            artifacts=(ReportArtifact(name=hostile, kind="artifact", seq=1, payload=hostile),),
        )
    )
    # The payload survives as INERT TEXT everywhere it appears — never as a tag.
    assert hostile not in doc
    assert "<img" not in doc
    assert doc.count("&lt;img src=x onerror=alert(1)&gt;") >= 8  # title + every field above


@pytest.mark.unit
def test_html_partial_banner_and_empty_states():
    doc = render_html(_ctx(partial=True, partial_trigger="operator", stats=None, artifacts=()))
    assert "terminated by the operator" in doc
    assert "No telemetry snapshot was recorded" in doc
    assert "The agent submitted no artifacts." in doc


@pytest.mark.unit
def test_html_masthead_carries_the_xorcise_lockup_and_favicon():
    """The one artifact that leaves the building must still be recognisably XORCISE."""
    doc = render_html(_ctx())
    # The crosshair mark: ring r=15 + the contained cross, stroke 2.4 — inline, never an image.
    assert "<circle cx='20' cy='20' r='15'" in doc
    assert "d='M20 8V32 M8 20H32'" in doc
    assert "stroke-width='2.4'" in doc
    assert "#e8b84b" in doc  # the amber the mark and the wordmark dot resolve to
    # Wordmark: warm letters, ALWAYS-amber dot.
    assert "<span class='wordmark'>XORCISE<span class='dot'>.</span>AI</span>" in doc
    assert "--wordmark:#f2ead6" in doc
    assert ".wordmark .dot{color:var(--primary)}" in doc
    # Favicon is the same mark as a data: URI — still no request leaves the document.
    assert 'rel="icon" href="data:image/svg+xml,' in doc
    assert "http://www.w3.org/2000/svg" in doc  # the XML namespace name, not a fetch
    assert doc.count("https://") == 0


@pytest.mark.unit
def test_html_scorecard_draws_the_dial_and_both_half_score_meters():
    doc = render_html(_ctx())
    dial = doc.split("<div class='dial'>")[1].split("</svg>")[0]
    # A real arc, not a bar: the dasharray is the score's share of the circumference (2*pi*52).
    assert "stroke-dasharray='235.24 326.73'" in dial  # 0.72 * 326.73
    assert ">72%</text>" in dial
    # The two half-scores render as filled meters at their own widths.
    assert "width:90.0%" in doc
    assert "width:54.0%" in doc
    # …and the exact 2-dp figures stay on the page, next to the checks-passed line.
    assert "<strong>1</strong> of <strong>2</strong> deterministic checks passed" in doc
    assert "overall <strong>0.72 (72%)</strong>" in doc


@pytest.mark.unit
def test_html_never_paints_a_measured_value_in_the_brand_amber():
    """Amber is identity (mark, wordmark dot, section ticks) — a score reads on the functional
    ladder (data / warning / failure), which is what the app's scorecard tone means."""
    doc = render_html(_ctx())
    dial = doc.split("<div class='dial'>")[1].split("</div>")[0]
    assert "var(--primary)" not in dial
    assert "var(--warn)" in dial  # 0.72 sits in the marginal band
    assert "var(--data)" in doc.split("class='meter'")[1]  # deterministic 0.90 -> data green
    assert "--ok:#6ee7a8" in doc  # the brand's data accent, not the old #4ade80
    assert "#4ade80" not in doc


@pytest.mark.unit
def test_html_carries_a_print_register_so_it_survives_a_board_pack():
    doc = render_html(_ctx())
    assert "@media print" in doc
    assert "#FAF8F2" in doc  # paper
    assert "#14120E" in doc  # ink
    assert "#C49A2A" in doc  # print amber for the mark and rules


@pytest.mark.unit
def test_neither_renderer_uses_emoji():
    ctx = _ctx(partial=True, partial_trigger="timeout", terrain=_terrain())
    for doc in (render_html(ctx), render_markdown(ctx)):
        assert "⚠" not in doc
        assert "PARTIAL RESULT" in doc


@pytest.mark.unit
def test_html_draws_the_terrain_map_and_its_counts_agree_with_it():
    doc = render_html(_ctx(terrain=_terrain()))
    assert "A DMZ web host fronts the internal segment." in doc
    assert "aria-label='Resolved terrain map'" in doc
    # Group bands, drawn nodes, and the one active link.
    assert ">AGENT WORKSPACE</text>" in doc
    assert ">DMZ_NET</text>" in doc
    for label in (">Agent</text>", ">Run-control</text>", ">web</text>"):
        assert label in doc, label
    assert "<line" in doc
    # An endpoint is NOT drawn — it collapses onto `rc` and lifts its state there…
    assert ">submit artifacts</text>" not in doc
    # …so the counted facts must match the picture: 3 drawn nodes, all reached.
    assert "<td>3 of 3</td>" in doc  # reached
    assert "<td>1 of 3</td>" in doc  # enumerated
    assert "<td>web (reached)</td>" in doc


@pytest.mark.unit
def test_terrain_section_is_omitted_rather_than_faked_when_there_is_no_map():
    doc = render_html(_ctx())
    assert "Resolved terrain map" not in doc
    assert ">Terrain</h2>" not in doc
    empty = render_html(_ctx(terrain=_terrain(groups=(), nodes=(), edges=())))
    assert "Resolved terrain map" not in empty
    assert "## Terrain" not in render_markdown(_ctx())


@pytest.mark.unit
def test_html_escapes_authored_terrain_labels():
    """Terrain labels come from a mission manifest — untrusted like every other author string."""
    hostile = "<img src=x onerror=alert(1)>"
    doc = render_html(
        _ctx(
            terrain=_terrain(
                summary=hostile,
                groups=(TerrainGroup(id="g", label=hostile, order=0, discovered=True),),
                nodes=(TerrainNodeV2(id="n", label=hostile, group="g"),),
                edges=(),
            )
        )
    )
    assert hostile not in doc
    assert "<img" not in doc


@pytest.mark.unit
def test_markdown_terrain_section_mirrors_the_html_counts():
    md = render_markdown(_ctx(terrain=_terrain()))
    assert "## Terrain" in md
    assert "A DMZ web host fronts the internal segment." in md
    assert "| Reached | 3 of 3 |" in md
    assert "| Objective | web (reached) |" in md


@pytest.mark.unit
def test_report_filename_is_slugged_and_extension_correct():
    ctx = _ctx()
    assert report_filename(ctx, "md") == "xorcise-run-run-abcd-sqli-login.md"
    assert report_filename(ctx, "html") == "xorcise-run-run-abcd-sqli-login.html"
    weird = _ctx(run=_run(mission="Chrono Canary / v2!"))
    assert report_filename(weird, "md") == "xorcise-run-run-abcd-chrono-canary-v2.md"
