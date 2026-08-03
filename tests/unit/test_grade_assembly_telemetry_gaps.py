"""telemetry_gaps_for: profile notes verbatim; generic/unknown → the unverified line."""

from __future__ import annotations

from xorcise.core.rest.grade_assembly import GENERIC_GAP_LINE, telemetry_gaps_for


def test_known_harness_gaps_carry_the_profile_notes_verbatim() -> None:
    gaps = telemetry_gaps_for("codex")
    assert any("agent-authored chat messages" in g for g in gaps)  # the partial note
    assert any(g.startswith("thinking:") for g in gaps)


def test_supported_kinds_produce_no_gap_lines() -> None:
    gaps = telemetry_gaps_for("codex")
    assert not any(g.startswith("terminal_command:") for g in gaps)


def test_unnoted_unsupported_kinds_produce_no_gap_lines() -> None:
    """The brief's rule: only NOTED unsupported kinds + ALL partial kinds become gap lines --
    un-noted unsupported kinds (browser_*, finding, ...) are structural non-features of CLI
    harnesses and would drown the disclosure if surfaced here (the UI still shows them struck)."""
    gaps = telemetry_gaps_for("codex")
    assert not any(g.startswith("browser_action:") for g in gaps)
    assert not any(g.startswith("browser_observation:") for g in gaps)
    assert not any(g.startswith("finding:") for g in gaps)


def test_unknown_or_generic_harness_yields_the_single_unverified_line() -> None:
    assert telemetry_gaps_for("some-custom-cli") == (GENERIC_GAP_LINE,)
    assert telemetry_gaps_for("generic") == (GENERIC_GAP_LINE,)
    assert telemetry_gaps_for(None) == (GENERIC_GAP_LINE,)
