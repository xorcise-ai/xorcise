"""Unit tests for the pure per-run intel disclosure policy helper (runcontrol.intel_policy)."""

from __future__ import annotations

from xorcise.core.contracts.mission import Intel
from xorcise.core.runcontrol.intel_policy import allowed_intel

_INTEL = (
    Intel(id="i1", text="check headers"),
    Intel(id="i2", text="try IDOR"),
    Intel(id="i3", text="read the source"),
)


def test_empty_policy_allows_all() -> None:
    assert allowed_intel("", _INTEL) == _INTEL


def test_all_policy_allows_all() -> None:
    assert allowed_intel("all", _INTEL) == _INTEL
    assert allowed_intel("ALL", _INTEL) == _INTEL  # case-insensitive keyword


def test_none_policy_allows_nothing() -> None:
    assert allowed_intel("none", _INTEL) == ()


def test_subset_policy_keeps_authored_order() -> None:
    # CSV order is intentionally REVERSED — the result must still be in authored order.
    assert allowed_intel("i3,i1", _INTEL) == (_INTEL[0], _INTEL[2])


def test_unknown_ids_are_ignored() -> None:
    assert allowed_intel("i2, nope , zzz", _INTEL) == (_INTEL[1],)


def test_empty_intel_is_empty_under_any_policy() -> None:
    assert allowed_intel("all", ()) == ()
    assert allowed_intel("none", ()) == ()
    assert allowed_intel("i1", ()) == ()
