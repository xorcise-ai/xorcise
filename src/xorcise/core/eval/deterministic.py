"""The deterministic half — the reproducible anchor.

grade_deterministic is a PURE function of (checks, ctx): same sealed evidence => identical
sub-score. Corroboration needs no special path — a check sourced from observed-facts only credits
what the XORCISE-owned anti-forgery anchor supports, so a fabricated trace cannot inflate the score.
Check dependencies gate conditional criteria such as turn efficiency on a successful solution.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import CheckVerdict
from xorcise.core.contracts.mission import Check
from xorcise.core.eval.checks import effective_weights, run_check


class DeterministicOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sub_score: float
    verdicts: tuple[CheckVerdict, ...]


def grade_deterministic(checks: Sequence[Check], ctx: SealedContext) -> DeterministicOutcome:
    weights = effective_weights(checks)
    raw_verdicts = [run_check(c, ctx) for c in checks]
    checks_by_id = {check.id: check for check in checks}
    raw_by_id = {check.id: verdict for check, verdict in zip(checks, raw_verdicts, strict=True)}
    effective: dict[str, bool] = {}
    visiting: set[str] = set()

    def effectively_passed(check_id: str) -> bool:
        """Resolve prerequisite passes recursively; malformed legacy graphs fail closed."""
        if check_id in effective:
            return effective[check_id]
        if check_id in visiting or check_id not in checks_by_id:
            return False
        visiting.add(check_id)
        check = checks_by_id[check_id]
        passed = raw_by_id[check_id].passed and all(
            effectively_passed(required_id) for required_id in check.requires
        )
        visiting.remove(check_id)
        effective[check_id] = passed
        return passed

    verdicts = []
    for check, verdict in zip(checks, raw_verdicts, strict=True):
        blocked_by = tuple(
            required_id for required_id in check.requires if not effectively_passed(required_id)
        )
        verdicts.append(
            verdict.model_copy(
                update={
                    "passed": effectively_passed(check.id),
                    "blocked_by": blocked_by,
                }
            )
        )

    sub = sum(w for w, verdict in zip(weights, verdicts, strict=True) if verdict.passed)
    # carry the EFFECTIVE weight onto each verdict (fills the equal-split None case)
    verdicts = [v.model_copy(update={"weight": w}) for v, w in zip(verdicts, weights, strict=True)]
    return DeterministicOutcome(sub_score=sub, verdicts=tuple(verdicts))
