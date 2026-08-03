"""Real grading-assembly readers + EvalJudge builder.

Verifies each reader against a REAL seeded store (submissions / sealed trace / observed facts /
installed manifest) — not mocks of the readers — plus the end-to-end builder wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core import runs
from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    Check,
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    RubricCriterion,
)
from xorcise.core.contracts.telemetry import ObservedFact, TraceRecord

pytestmark = pytest.mark.adapters


def _otlp_span(name: str, **attrs: str) -> str:
    """A minimal real OTLP/JSON traces payload for one span (distill_transcript flattens it)."""
    span = {
        "traceId": "t",
        "spanId": "s",
        "name": name,
        "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()],
    }
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": []},
                    "scopeSpans": [{"scope": {"name": "sc"}, "spans": [span]}],
                }
            ]
        }
    )


def _install_mission(
    home: Path,
    slug: str,
    *,
    checks: tuple[Check, ...] = (),
    rubric: tuple[RubricCriterion, ...] = (),
) -> None:
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    root = home / "missions" / slug
    root.mkdir(parents=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
        checks=checks,
        rubric=rubric,
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def _seed_run_with_evidence(home: Path) -> str:
    """A registered-agent run + submissions + a sealed two-record trace + an observed fact."""
    from xorcise.core.otel.store import SqliteSealStore, SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    flag_check = Check(
        id="flag",
        source="artifacts",
        ref="flag",
        op="equals",
        args={"expected": "XORCISE{x}"},
        weight=1.0,
    )
    _install_mission(
        home,
        "c1",
        checks=(flag_check,),
        rubric=(RubricCriterion(id="r1", text="did it", weight=1.0),),
    )
    run = runs.create_run(agent_id="a1", mission="c1", budget_seconds=600)
    rid = run.run_id

    subs = SqliteSubmissionStore()
    subs.record(rid, "flag", "flag", "XORCISE{x}")
    subs.record(rid, "artifact", "writeup", "the answer")
    subs.record(rid, "intel", "i1", "ignored-by-artifacts")

    trace = SqliteTraceStore()
    trace.append(
        TraceRecord(run_id=rid, seq=1, payload=_otlp_span("span-a", **{"gen_ai.prompt": "did-a"}))
    )
    trace.append(
        TraceRecord(run_id=rid, seq=2, payload=_otlp_span("span-b", **{"gen_ai.prompt": "did-b"}))
    )
    SqliteSealStore().seal(rid)

    SqliteObservedFactsStore().record(
        ObservedFact(run_id=rid, kind="run-control", name="flag-submitted", value="True")
    )
    return rid


def test_grade_request_for_builds_thin_request(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import grade_request_for

    rid = _seed_run_with_evidence(migrated_home)
    req = grade_request_for(rid)
    assert req.run_id == rid
    assert req.trace_ref == rid  # run_id-keyed pointer


def test_sealed_context_for_reads_real_stores(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import (
        GENERIC_GAP_LINE,
        grade_request_for,
        sealed_context_for,
    )

    rid = _seed_run_with_evidence(migrated_home)
    ctx = sealed_context_for(grade_request_for(rid))

    assert ctx.run_id == rid
    assert ctx.trace_ref == rid
    assert ctx.trace_present is True
    # artifacts = flag + artifact submissions, keyed by name (intel excluded)
    assert ctx.artifacts == {"flag": "XORCISE{x}", "writeup": "the answer"}
    # otel_stats = the existing pure projection over the two sealed records
    assert ctx.otel_stats == {"turn-count": 2, "span-payload-count": 2}
    # observed facts keyed by name
    assert ctx.observed_facts["flag-submitted"] == "True"
    # Transcript distillation: compact per-span lines (name + attrs), seq order
    assert len(ctx.transcript) == 2
    assert "span-a" in ctx.transcript[0] and "did-a" in ctx.transcript[0]
    assert "span-b" in ctx.transcript[1] and "did-b" in ctx.transcript[1]
    # distillation drops the raw OTLP envelope — the transcript is not the raw payload
    assert "resourceSpans" not in ctx.transcript[0]
    # harness telemetry disclosure (capability-matrix feature): _seed_run_with_evidence's run
    # defaults source_agent to "generic" (create_run's default) -- no registered real harness --
    # so source_agent is threaded through verbatim and the honest single-line gap disclosure fires.
    assert ctx.source_agent == "generic"
    assert ctx.telemetry_gaps == (GENERIC_GAP_LINE,)


def test_sealed_context_for_threads_a_real_harness_profile(migrated_home) -> None:
    """A run whose source_agent resolves to a REGISTERED harness (not the generic fallback)
    carries that source_agent + its adapter's declared partial/unsupported-with-note gaps through
    to the SealedContext -- proving the threading in sealed_context_for, not just the pure
    telemetry_gaps_for helper (which is covered in isolation by
    test_grade_assembly_telemetry_gaps.py)."""
    from xorcise.core.rest.grade_assembly import grade_request_for, sealed_context_for

    _install_mission(migrated_home, "c2")
    run = runs.create_run(agent_id="a1", mission="c2", budget_seconds=600, source_agent="codex")
    ctx = sealed_context_for(grade_request_for(run.run_id))

    assert ctx.source_agent == "codex"
    assert any("agent-authored chat messages" in g for g in ctx.telemetry_gaps)
    assert any(g.startswith("thinking:") for g in ctx.telemetry_gaps)


def test_sealed_context_transcript_is_seq_ordered_distilled_lines(migrated_home) -> None:
    from xorcise.core.otel.store import SqliteSealStore, SqliteTraceStore
    from xorcise.core.rest.grade_assembly import grade_request_for, sealed_context_for

    _install_mission(migrated_home, "c1")
    run = runs.create_run(agent_id="a1", mission="c1", budget_seconds=600)
    rid = run.run_id
    trace = SqliteTraceStore()
    # append OUT of seq order to prove sealed_context_for sorts by seq, not insertion. Each span
    # carries a content-bearing attr (full_command) so the actions-only distiller keeps it.
    trace.append(
        TraceRecord(run_id=rid, seq=2, payload=_otlp_span("t", **{"full_command": "second"}))
    )
    trace.append(
        TraceRecord(run_id=rid, seq=1, payload=_otlp_span("t", **{"full_command": "first"}))
    )
    SqliteSealStore().seal(rid)

    ctx = sealed_context_for(grade_request_for(rid))
    assert len(ctx.transcript) == 2
    joined = "\n".join(ctx.transcript)
    assert joined.index("first") < joined.index("second")


def test_sealed_context_absent_trace_marks_not_present(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import grade_request_for, sealed_context_for

    _install_mission(migrated_home, "c1")
    run = runs.create_run(agent_id="a1", mission="c1", budget_seconds=600)
    ctx = sealed_context_for(grade_request_for(run.run_id))
    assert ctx.trace_present is False
    assert ctx.otel_stats == {"turn-count": 0, "span-payload-count": 0}
    assert ctx.artifacts == {}


def test_checks_and_rubric_for_read_installed_manifest(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import checks_for, grade_request_for, rubric_for

    rid = _seed_run_with_evidence(migrated_home)
    req = grade_request_for(rid)
    checks = checks_for(req)
    rubric = rubric_for(req)
    assert tuple(c.id for c in checks) == ("flag",)
    assert checks[0].source == "artifacts"
    assert tuple(r.id for r in rubric) == ("r1",)


def test_readers_degrade_when_mission_not_installed(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import checks_for, grade_request_for, rubric_for

    run = runs.create_run(agent_id="a1", mission="absent", budget_seconds=600)
    req = grade_request_for(run.run_id)
    assert checks_for(req) == ()
    assert rubric_for(req) == ()


def test_build_eval_judge_grades_real_run_without_model(migrated_home) -> None:
    from xorcise.core.rest.grade_assembly import build_eval_judge, grade_request_for

    rid = _seed_run_with_evidence(migrated_home)
    result = build_eval_judge(model=None).grade(grade_request_for(rid))

    assert result.run_id == rid
    assert 0.0 <= result.overall <= 1.0
    assert result.judge_status in {"ok", "model-not-configured", "unavailable"}
    # the seeded flag check passes against the real artifacts -> deterministic half is full
    assert result.breakdown.deterministic == pytest.approx(1.0)
    # no BYOM key in the throwaway home -> judge half degrades cleanly
    assert result.judge_status == "model-not-configured"
