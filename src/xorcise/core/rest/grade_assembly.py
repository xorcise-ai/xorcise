"""Grading assembly (rest/delivery layer) — turn a run into a real graded result.

The `EvalJudge` adapter (orchestration) is pure given INJECTED cross-module readers: orchestration
and eval may not import runs/runcontrol/otel/missions, so the delivery layer assembles the
`SealedContext` from the three sealed evidence stores (submissions / sealed-trace projection /
observed facts) and resolves the mission's checks + rubric from the installed manifest, then wires
them into `EvalJudgeDeps`. `build_eval_judge` is what a later task points `terminate_run` at.

otel imports stay lazy (plane-isolation invariant — mirror `rest/run_terminate.py`): the otel plane
must not sit on this module's import path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from xorcise.core import runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import GradeRequest
from xorcise.core.contracts.mission import Check, RubricCriterion
from xorcise.core.eval.judge import JudgeModel
from xorcise.core.missions.runtime import get_installed
from xorcise.core.orchestration.clients.grading import EvalJudge, EvalJudgeDeps
from xorcise.core.orchestration.clients.judge_model import build_judge_model
from xorcise.core.orchestration.clients.token_counter import build_token_counter

if TYPE_CHECKING:
    from xorcise.core.missions.runtime import InstalledMission

GENERIC_GAP_LINE = (
    "harness telemetry profile unknown (generic adapter) — "
    "absence of any event class is not evidence."
)


def telemetry_gaps_for(source_agent: str | None) -> tuple[str, ...]:
    """Gap sentences for the judge: `kind: note` per unsupported/partial kind, notes verbatim
    from the adapter's profile (single-sourced with the UI). Unknown/generic → one honest line."""
    # Lazy: plane-isolation invariant (otel stays off the module import path).
    from xorcise.core.contracts.agent_event import KindSupport
    from xorcise.core.harness_adapters import load_adapters
    from xorcise.core.otel.adapters import registry

    load_adapters()
    adapter = registry.get(source_agent) if source_agent else None
    if adapter is None or adapter.name == "generic":
        return (GENERIC_GAP_LINE,)
    profile = adapter.capabilities
    lines = []
    for kind, level in sorted(profile.kinds.items()):
        if level is KindSupport.supported:
            continue
        note = profile.notes.get(kind)
        if level is KindSupport.partial or note:
            lines.append(f"{kind}: {note or f'not exported by {profile.adapter_name}'}")
    return tuple(lines)


def grade_request_for(run_id: str) -> GradeRequest:
    """Build the thin GradeRequest. `trace_ref` is the run_id-keyed pointer into the trace store;
    the heavy evidence is assembled lazily by `sealed_context_for`, so the request stays thin."""
    return GradeRequest(run_id=run_id, trace_ref=run_id)


def _install_root() -> Path:
    return Path(get_settings().missions_root)


def _installed_for(req: GradeRequest) -> InstalledMission | None:
    """The InstalledMission for the run's mission slug, or None if absent/uninstalled."""
    run = runs.get(req.run_id)
    if run is None:
        return None
    return get_installed(run.mission, _install_root())


def sealed_context_for(req: GradeRequest) -> SealedContext:
    """Assemble the SealedContext from the three sealed evidence stores.

    artifacts ← run submissions (flag/artifact kinds, keyed by name);
    otel_stats ← the pure projection over the sealed RAW trace;
    observed_facts ← the XORCISE-owned observed-fact stream (keyed by name).
    `trace_present` reflects whether the trace was sealed (frozen) for this run."""
    # Lazy: keep the otel plane off the module import path (plane-isolation invariant).
    from xorcise.core.otel.distill import distill_transcript
    from xorcise.core.otel.stats import project_otel_stats
    from xorcise.core.otel.store import SqliteLogStore, SqliteSealStore, SqliteTraceStore
    from xorcise.core.runcontrol.store import ARTIFACT_KINDS, SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    submissions = SqliteSubmissionStore().list_for_run(req.run_id)
    artifacts = {s.name: s.payload for s in submissions if s.kind in ARTIFACT_KINDS}

    records = SqliteTraceStore().read(req.run_id)
    otel_stats = project_otel_stats(records)
    # Distillation: the judge's transcript is compact per-item lines (name + salient attrs
    # + event content) rather than the raw OTLP JSON — flatten strips the verbose envelope so the
    # token budget is spent on what the agent did, not scaffolding. Derived from the RAW stores only
    # (never the display replay projection the grader may not read).
    #
    # BOTH raw signals are passed: which one carries the agent's actions is a per-harness choice.
    # Codex reports every tool call only on the LOGS signal and fills its spans with plumbing, so
    # reading traces alone made the judge grade a run that built and ran a proxy as showing "no
    # evidence" of one. Every local run carries logs, claude-code's included.
    log_records = SqliteLogStore().read(req.run_id)
    transcript = distill_transcript(records, log_records)

    facts = SqliteObservedFactsStore().list_for_run(req.run_id)
    # name-keyed per the SealedContext contract (eval resolvers look up by name);
    # a same-name/different-kind collision is last-writer-wins.
    observed_facts: dict[str, object] = {f.name: f.value for f in facts}

    run = runs.get(req.run_id)
    source_agent = run.source_agent if run is not None else None

    return SealedContext(
        run_id=req.run_id,
        trace_ref=req.trace_ref,
        trace_present=SqliteSealStore().is_sealed(req.run_id),
        artifacts=artifacts,
        otel_stats=otel_stats,
        observed_facts=observed_facts,
        transcript=transcript,
        source_agent=source_agent,
        telemetry_gaps=telemetry_gaps_for(source_agent),
    )


def checks_for(req: GradeRequest) -> tuple[Check, ...]:
    """The installed mission's author-declared deterministic checks; () if uninstalled."""
    installed = _installed_for(req)
    return installed.checks if installed is not None else ()


def rubric_for(req: GradeRequest) -> tuple[RubricCriterion, ...]:
    """The installed mission's author-declared rubric criteria; () if uninstalled."""
    installed = _installed_for(req)
    return installed.rubric if installed is not None else ()


def build_eval_judge(model: JudgeModel | None = None) -> EvalJudge:
    """Wire the delivery-assembled readers + the BYOM model into the real JudgePort.

    `model=None` resolves the configured BYOM model from settings (None when no key is set, so the
    judge half degrades to `model-not-configured` rather than crashing)."""
    settings = get_settings()
    if model is None:
        model = build_judge_model(settings)
    return EvalJudge(
        EvalJudgeDeps(
            sealed_context_for=sealed_context_for,
            checks_for=checks_for,
            rubric_for=rubric_for,
            model=model,
            max_transcript_tokens=settings.judge_transcript_max_tokens,
            max_span_tokens=settings.judge_span_max_tokens,
            count_tokens=build_token_counter(settings.judge_tokenizer),
        )
    )
