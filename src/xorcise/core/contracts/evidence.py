"""SealedContext — the evaluator's sole input (LEAF wire DTO).

Grading is evidence-replay over a sealed context. The delivery (rest) layer assembles this from
the three sealed evidence stores (submissions, the otel-stats projection, observed facts) and hands
it to xorcise.core.eval, so eval stays a part-island. Imports stdlib + pydantic only.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SealedContext(_Frozen):
    run_id: str
    trace_ref: str | None = None
    trace_present: bool = True
    artifacts: Mapping[str, str] = Field(default_factory=dict)
    otel_stats: Mapping[str, object] = Field(default_factory=dict)
    observed_facts: Mapping[str, object] = Field(default_factory=dict)
    # ordered raw OTel span payload lines (seq order), the judge's transcript evidence.
    # Carried as str (not TraceRecord) so this leaf keeps its stdlib+pydantic-only rule.
    transcript: tuple[str, ...] = Field(default_factory=tuple)
    # Harness honesty disclosure (capability-matrix feature): which event classes this run's
    # harness can NEVER export (or exports only partially), rendered as plain sentences for the
    # judge. Additive; defaults preserve the wire shape.
    source_agent: str | None = None
    telemetry_gaps: tuple[str, ...] = ()
