from __future__ import annotations

from typing import get_args

import pytest

from xorcise.core.contracts.mission import _CHECK_OP_ARGS, CheckOp
from xorcise.core.eval.operations import OPERATIONS


@pytest.mark.unit
@pytest.mark.parametrize(
    "op,value,args,expected",
    [
        ("equals", "XORCISE{x}", {"expected": "XORCISE{x}"}, True),
        ("equals", "nope", {"expected": "XORCISE{x}"}, False),
        ("matches_format", "XORCISE{abc}", {"pattern": r"XORCISE\{.+\}"}, True),
        ("matches_format", "plain", {"pattern": r"XORCISE\{.+\}"}, False),
        ("observed", "anything", {}, True),
        ("observed", "", {}, False),
        ("observed", None, {}, False),
        ("observed", True, {}, True),
        ("observed", False, {}, False),
        ("lesser_than", 12, {"value": 25}, True),
        ("lesser_than", 30, {"value": 25}, False),
    ],
)
def test_operations_table(op, value, args, expected):
    assert OPERATIONS[op](value, **args) is expected


@pytest.mark.unit
def test_operations_registry_matches_contract_vocabulary():
    """CheckOp (contracts Literal, the ingest gate) and OPERATIONS (the executable registry) must
    stay in sync — an op that validates at ingest must dispatch at grade time and vice versa.
    Mirrors the Check.source Literal ↔ RESOLVERS precedent."""
    assert set(OPERATIONS) == set(get_args(CheckOp))


@pytest.mark.unit
def test_arg_shape_table_matches_contract_vocabulary():
    """The contract's per-op required-arg table must cover exactly the op vocabulary."""
    assert set(_CHECK_OP_ARGS) == set(get_args(CheckOp))
