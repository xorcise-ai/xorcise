"""fold_run_stats — the per-run telemetry fold. The parametrized fixture cases are the
regression guard against the "flat sum returns zero for Codex" trap: each real capture uses a
distinct token-key schema, and all three must yield non-zero input/output."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind, RawTraceRef
from xorcise.core.otel.adapters import normalize_run
from xorcise.core.otel.adapters.base import AdapterContext
from xorcise.core.otel.run_stats import fold_run_stats, projection_key

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "otlp"


def _metric(data: dict[str, str], ts: datetime = _T0) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=f"m{ts.timestamp()}{data}",
        ts=ts,
        source_agent="x",
        kind=AgentEventKind.metric,
        title="usage",
        data=data,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id=""),
    )


def _kind(
    kind: AgentEventKind,
    *,
    duration_ms: int | None = None,
    status: Literal["ok", "error"] | None = None,
) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=f"e{kind}{duration_ms}{status}",
        ts=_T0,
        source_agent="x",
        kind=kind,
        title="t",
        duration_ms=duration_ms,
        status=status,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id=""),
    )


def test_openhands_and_claude_keys_sum() -> None:
    events = [
        _metric({"input_tokens": "100", "output_tokens": "20", "cache_read_input_tokens": "5"})
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 100
    assert s.tokens.output == 20
    assert s.tokens.cache_read == 5
    assert s.tokens.total == 120  # computed, not read


def test_codex_count_keys_sum() -> None:
    events = [
        _metric(
            {
                "input_token_count": "300",
                "output_token_count": "40",
                "cached_token_count": "7",
                "reasoning_token_count": "9",
            }
        )
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 300
    assert s.tokens.output == 40
    assert s.tokens.cache_read == 7
    assert s.tokens.reasoning == 9
    assert s.tokens.total == 340


def test_model_calls_counts_token_metrics_not_ttft() -> None:
    events = [
        _metric({"input_token_count": "1", "output_token_count": "1"}),
        _metric({"duration_ms": "50"}),  # a ttft-style metric with no tokens — not a model call
    ]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.counts.model_calls == 1


def test_counts_and_timing() -> None:
    events = [
        _kind(AgentEventKind.tool_call, duration_ms=10),
        _kind(AgentEventKind.terminal_command, duration_ms=90),
        _kind(AgentEventKind.finding),
        _kind(AgentEventKind.error, status="error"),
    ]
    s = fold_run_stats(
        events, created_at=_T0, completed_at=datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
    )
    assert s.counts.tool_calls == 2
    assert s.counts.findings == 1
    assert s.counts.errors == 1
    assert s.counts.events_total == 4
    assert s.counts.by_kind["tool_call"] == 1
    assert s.timing.longest_tool_ms == 90
    assert s.timing.elapsed_seconds == 30.0


def test_empty_and_malformed_never_raise() -> None:
    events = [_metric({"input_tokens": "not-a-number"})]
    s = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert s.tokens.input == 0
    assert s.tokens.total == 0
    assert fold_run_stats([], created_at=_T0, completed_at=None).counts.events_total == 0


@pytest.mark.parametrize("name", ["openhands", "claude_code", "codex"])
def test_each_real_harness_yields_nonzero_tokens(name: str) -> None:
    # The harness adapters self-register on import but are pulled lazily (plane isolation), so the
    # projection falls back to `generic` (no token metrics) unless we load them first — exactly what
    # events_view does before it projects.
    from xorcise.core.harness_adapters import load_adapters

    load_adapters()
    doc = json.loads((_FIXTURES / f"{name}_real_run.json").read_text())
    ctx = AdapterContext(
        run_id=doc.get("run_id", "r1"),
        source_agent=name if name != "claude_code" else "claude-code",
        mission_id="fixture",
        created_at=_T0,
    )
    view = normalize_run(doc["records"], ctx, log_records=doc.get("log_records"))
    s = fold_run_stats(view.events, created_at=_T0, completed_at=None)
    assert s.tokens.input > 0, f"{name}: input tokens folded to zero"
    assert s.tokens.output > 0, f"{name}: output tokens folded to zero"
    assert s.tokens.total == s.tokens.input + s.tokens.output
    assert s.counts.model_calls > 0


# ── which model actually ran (#113) ──────────────────────────────────────────────────────────
#
# `runs.model` is the model the OPERATOR declared at `agent register --model`. Almost nobody passes
# it, so every run reported "model not disclosed" and a result could not be attributed to a model
# after the fact — fatal for a leaderboard.
#
# The telemetry knew all along. Every harness adapter already lands the model on its usage metric
# as `data["model"]` (claude-code and openhands via gen_ai.request/response.model, codex via its
# own `model` attribute); the fold just never collected it. Observed-from-telemetry is also the
# better provenance than a declaration: it is what the harness reported actually running, not what
# somebody typed at registration.


def test_the_observed_model_is_folded_from_usage_metrics() -> None:
    s = fold_run_stats(
        [_metric({"model": "gpt-5.5", "input_tokens": "10", "output_tokens": "2"})],
        created_at=_T0,
        completed_at=None,
    )
    assert s.models == ("gpt-5.5",)


def test_repeated_and_multiple_models_are_deduped_in_first_seen_order() -> None:
    """A run makes many calls; a router or a fallback can change model mid-run.

    Order is first-seen rather than sorted so the primary model — the one that did the early work —
    reads first wherever this is rendered.
    """
    s = fold_run_stats(
        [
            _metric({"model": "gpt-5.5", "input_tokens": "1"}, ts=_T0),
            _metric(
                {"model": "gpt-5.5", "input_tokens": "1"}, ts=datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
            ),
            _metric(
                {"model": "claude-fable-5", "input_tokens": "1"},
                ts=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
            ),
        ],
        created_at=_T0,
        completed_at=None,
    )
    assert s.models == ("gpt-5.5", "claude-fable-5")


def test_a_run_with_no_model_telemetry_reports_none_rather_than_guessing() -> None:
    """Empty, not a placeholder: "unknown" is a real answer and must not be mistaken for a name."""
    s = fold_run_stats([_metric({"input_tokens": "5"})], created_at=_T0, completed_at=None)
    assert s.models == ()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("openhands", "bedrock/au.anthropic.claude-sonnet-4-5-20250929-v1:0"),
        ("claude_code", "claude-fable-5"),
        ("codex", "gpt-5.5"),
    ],
)
def test_each_real_harness_capture_reveals_the_model_that_ran(name: str, expected: str) -> None:
    """Against the real captures, because this is a provenance claim and it has to be true of the
    actual harnesses rather than of a synthetic event this test built for itself.

    Codex is the case that proves the fold must not special-case `gen_ai.*`: it carries the model
    on its own `model` attribute, and its adapter already normalises that onto the metric.
    """
    from xorcise.core.harness_adapters import load_adapters

    load_adapters()
    doc = json.loads((_FIXTURES / f"{name}_real_run.json").read_text())
    ctx = AdapterContext(
        run_id=doc.get("run_id", "r1"),
        source_agent=name if name != "claude_code" else "claude-code",
        mission_id="fixture",
        created_at=_T0,
    )
    view = normalize_run(doc["records"], ctx, log_records=doc.get("log_records"))

    s = fold_run_stats(view.events, created_at=_T0, completed_at=None)

    assert s.models == (expected,), f"{name}: folded {s.models}, expected ({expected!r},)"


def test_unclassified_spans_are_counted_but_are_not_tool_calls() -> None:
    """The #120 shape: 113 marker-only spans must not read as 113 tool calls in the report. They
    are still events (events_total / by_kind), so the report can show them on their own row."""
    events = [_kind(AgentEventKind.unclassified) for _ in range(3)] + [
        _kind(AgentEventKind.tool_call)
    ]
    stats = fold_run_stats(events, created_at=_T0, completed_at=None)
    assert stats.counts.tool_calls == 1
    assert stats.counts.events_total == 4
    assert stats.counts.by_kind["unclassified"] == 3


def test_fold_stamps_the_projection_it_was_folded_under() -> None:
    """`projection` is what lets a reader tell a snapshot that predates the current renderer apart
    from a fresh one. Absent unless the caller supplies it, so older call sites keep working.

    It carries the fold's OWN version as well as the renderer's. Originally it was just
    `adapter@version`, which missed the case where the fold starts emitting a NEW field: neither
    component changes then, so every existing snapshot kept looking current and was served stale
    (#128 review).
    """
    assert projection_key("generic", "2+normalizer.3") == "generic@2+normalizer.3+stats.3"
    stamped = fold_run_stats(
        [], created_at=_T0, completed_at=None, projection="generic@2+normalizer.3"
    )
    assert stamped.projection == "generic@2+normalizer.3"
    assert fold_run_stats([], created_at=_T0, completed_at=None).projection is None


# ── a snapshot folded before `models` existed must not read as current (#128 review) ─────────
#
# The stamp was `<adapter>@<version>` only. Adding a FIELD to the fold changes neither component,
# so a snapshot folded before `models` existed still matched the current stamp and was served
# as-is — an already-graded run kept saying "model not disclosed" even though its retained
# telemetry named the model. The stamp has to version the fold's own output shape too.


def test_the_stamp_changes_when_the_fold_output_changes() -> None:
    from xorcise.core.otel.run_stats import STATS_FOLD_VERSION, projection_key

    key = projection_key("generic", "2+normalizer.3")

    assert key.startswith("generic@2+normalizer.3")
    assert STATS_FOLD_VERSION in key, (
        "the stamp must carry the fold version, or adding a field to RunStats leaves every "
        "existing snapshot looking current"
    )


def test_a_snapshot_stamped_before_the_models_fold_is_stale() -> None:
    """The exact pre-change stamp — adapter and version current, fold version absent."""
    from xorcise.core.otel.run_stats import projection_key

    assert projection_key("generic", "2+normalizer.3") != "generic@2+normalizer.3"


# ── the telemetry window must include the last event's duration (#134 review) ────────────────
#
# The fold kept only min/max of event START timestamps, so a run whose telemetry is ONE 60-second
# span reported a zero-length window — the report rendered "0.0s" for a minute of work. Span-backed
# events carry the producer's start time; the duration is a separate field, and dropping it also
# loses the final span's extent in a multi-span run.


def test_the_window_covers_a_single_long_span() -> None:
    s = fold_run_stats(
        [_kind(AgentEventKind.tool_call, duration_ms=60_000)], created_at=_T0, completed_at=None
    )
    assert s.timing.last_event_end_ts is not None and s.timing.first_event_ts is not None
    assert (s.timing.last_event_end_ts - s.timing.first_event_ts).total_seconds() == 60.0


def test_the_window_includes_the_final_span_duration_not_just_its_start() -> None:
    later = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
    s = fold_run_stats(
        [
            _kind(AgentEventKind.tool_call, duration_ms=1_000),
            AgentEvent(
                run_id="r1",
                id="last",
                ts=later,
                source_agent="x",
                kind=AgentEventKind.tool_call,
                title="t",
                duration_ms=10_000,
                raw_ref=RawTraceRef(run_id="r1", raw_seq=0, span_id=""),
            ),
        ],
        created_at=_T0,
        completed_at=None,
    )
    # 30s to the last span's start, plus its own 10s — not 30.
    assert s.timing.last_event_end_ts is not None and s.timing.first_event_ts is not None
    assert (s.timing.last_event_end_ts - s.timing.first_event_ts).total_seconds() == 40.0


def test_an_event_with_no_duration_contributes_only_its_timestamp() -> None:
    """Unknown duration is not zero-length work; it is simply unknown, so the window ends at the
    last timestamp we actually have."""
    s = fold_run_stats([_kind(AgentEventKind.message)], created_at=_T0, completed_at=None)
    assert s.timing.last_event_end_ts == s.timing.last_event_ts
