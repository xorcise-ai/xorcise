"""run_check + effective_weights.

run_check resolves a value then applies a pure op — the whole deterministic check collapses to
"pull a value from a source by ref, assert an op". effective_weights resolves the equal-split vs
explicit cases (the manifest validator guarantees all-set-and-sum-1 when any is set).
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.evidence import SealedContext
from xorcise.core.contracts.grading import CheckVerdict
from xorcise.core.contracts.mission import Check
from xorcise.core.eval.operations import OPERATIONS
from xorcise.core.eval.resolvers import RESOLVERS


def run_check(c: Check, ctx: SealedContext) -> CheckVerdict:
    """NEVER raises: an unexecutable check grades as failed with `error` disclosing why.

    The contract now rejects unknown ops/arg shapes at ingest (CheckOp Literal + arg-shape
    validator), but an already-installed record can predate that gate — a bare
    `OPERATIONS[c.op]` KeyError here wedged the whole run at "grading" forever. Mirrors the
    judge half's degrade pattern (judge.py returns status instead of raising)."""
    op_fn = OPERATIONS.get(c.op)
    if op_fn is None:
        return _errored(c, f"unknown op '{c.op}' (known: {', '.join(sorted(OPERATIONS))})")
    try:
        value = RESOLVERS[c.source](ctx, c.ref)
        passed = op_fn(value, **c.args)
    except Exception as exc:  # defensive: one bad check must fail, not crash grading
        return _errored(c, f"check raised {type(exc).__name__}: {exc}")
    return CheckVerdict(
        id=c.id,
        source=c.source,
        ref=c.ref,
        op=c.op,
        value=value,
        passed=passed,
        weight=c.weight or 0.0,
    )


def _errored(c: Check, error: str) -> CheckVerdict:
    return CheckVerdict(
        id=c.id,
        source=c.source,
        ref=c.ref,
        op=c.op,
        value=None,
        passed=False,
        weight=c.weight or 0.0,
        error=error,
    )


def effective_weights(checks: Sequence[Check]) -> list[float]:
    if not checks:
        return []
    if all(c.weight is None for c in checks):  # equal-split branch
        return [1.0 / len(checks)] * len(checks)
    return [c.weight or 0.0 for c in checks]  # validator guarantees all set, sum 1
