from __future__ import annotations

import pytest

from xorcise.core.runs.observed import run_control_facts


@pytest.mark.unit
def test_run_control_facts_counts_by_kind():
    # ordered submission kinds for a run: a flag, an artifact, one intel, a mark-done
    facts = run_control_facts(["flag", "artifact", "intel", "complete"])
    assert facts == {
        "submission-count": 2,  # flag + artifact (intel/complete are not work submissions)
        "artifact-count": 1,
        "flag-submitted": True,
        "intel-count": 1,
        "completed": True,
    }


@pytest.mark.unit
def test_run_control_facts_empty_run():
    assert run_control_facts([]) == {
        "submission-count": 0,
        "artifact-count": 0,
        "flag-submitted": False,
        "intel-count": 0,
        "completed": False,
    }
