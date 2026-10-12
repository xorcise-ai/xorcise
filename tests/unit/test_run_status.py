"""How a run is classified and shown — the one server-side vocabulary every surface renders (#165).

The CLI and the GUI used to keep their own trigger tables, and they drifted: a `crashed` run read
"Crashed" in `run list` and a grey "Terminal" in the GUI, an operator stop "Terminated" against
"Terminal", and both still matched `budget` / `error` triggers no server has ever written. The
server now ships `outcome` and `status` on every run; these tests pin that vocabulary, and the
golden file below is what the GUI's test fixtures are built from, so a server change that the
frontend has not caught up with fails here rather than drifting silently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest

from xorcise.core.contracts.run import (
    COMPLETED_TRIGGERS,
    PARTIAL_TRIGGERS,
    UNGRADED_TRIGGERS,
    RunEntry,
    TerminalTrigger,
    run_outcome,
    run_status,
)

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[2] / "frontend/src/test/run-status.golden.json"

# Every (state, trigger) a run can be in: the two live states, terminal by each trigger, and
# terminal with no known trigger (a legacy row — see the stored-trigger test below).
_CASES: list[tuple[str, str | None]] = [
    ("created", None),
    ("active", None),
    *(("terminal", t) for t in get_args(TerminalTrigger)),
    ("terminal", None),
]


def _golden() -> dict[str, dict[str, object]]:
    return {
        f"{state}/{trigger or ''}": {
            "outcome": run_outcome(trigger),
            "status": run_status(state, trigger).model_dump(),
        }
        for state, trigger in _CASES
    }


def _entry(state: str, trigger: str | None) -> RunEntry:
    return RunEntry(
        run_id="r1",
        agent_id="a1",
        mission="m",
        state=state,
        created_at=datetime(2026, 10, 12, tzinfo=UTC),
        terminal_trigger=trigger,  # type: ignore[arg-type]  # deliberately unvalidated input
    )


def test_the_trigger_vocabulary_is_exactly_the_three_sets() -> None:
    """Every written trigger lands in exactly one set — no trigger counts two ways, none is
    unclassified, and the sets name nothing the server never writes (`budget`, `error`,
    `completed`)."""
    triggers = set(get_args(TerminalTrigger))
    sets = (COMPLETED_TRIGGERS, PARTIAL_TRIGGERS, UNGRADED_TRIGGERS)
    assert set().union(*sets) == triggers
    assert sum(len(s) for s in sets) == len(triggers)


@pytest.mark.parametrize(("state", "trigger"), _CASES)
def test_every_run_has_a_label_and_every_unhappy_ending_says_why(
    state: str, trigger: str | None
) -> None:
    status = run_status(state, trigger)
    assert status.label
    # The agent's own completion and a live run need no explanation; every other known ending
    # does. A terminal run with no known trigger has nothing true to say beyond "Ended".
    if state == "terminal" and trigger not in (None, "done"):
        assert status.detail
    else:
        assert status.detail is None


def test_the_run_carries_its_outcome_and_status_on_the_wire() -> None:
    wire = _entry("terminal", "crashed").model_dump(mode="json")
    assert wire["outcome"] == "infra_failed"
    assert wire["status"]["label"] == "Crashed"
    assert wire["status"]["tone"] == "err"


@pytest.mark.parametrize("stored", ["budget", "error", "completed", "something-new"])
def test_a_stored_trigger_outside_the_vocabulary_reads_as_ended_not_a_failure(stored: str) -> None:
    """The column is free text. A value outside the vocabulary must not fail the whole run list
    (RunEntry validation would 500 it); it reads as an ending with no known cause."""
    entry = _entry("terminal", stored)
    assert entry.terminal_trigger is None
    assert entry.outcome is None
    assert entry.status.label == "Ended"


def test_the_gui_fixture_golden_file_matches_the_server() -> None:
    """The GUI's test fixtures take outcome and status from this file instead of restating the
    mapping. Regenerate it with:

        uv run python -c "from tests.unit.test_run_status import write_golden; write_golden()"
    """
    assert json.loads(GOLDEN.read_text()) == _golden(), "stale golden file — regenerate it"


def write_golden() -> None:
    GOLDEN.write_text(json.dumps(_golden(), indent=2, sort_keys=True) + "\n")
