import pytest

from xorcise.core.headscale.cidr import allocate_cidr, cidr_for_index, overlapping_subnets


def test_allocate_skips_octet_zero_and_returns_first_free():
    assert allocate_cidr("10.200.0.0/16", 24, set()) == "10.200.1.0/24"


def test_allocate_skips_already_allocated():
    assert allocate_cidr("10.200.0.0/16", 24, {"10.200.1.0/24"}) == "10.200.2.0/24"


def test_cidr_for_index_prefers_indexed_subnet():
    assert cidr_for_index("10.200.0.0/16", 24, 17, set()) == "10.200.17.0/24"


def test_cidr_for_index_falls_back_when_taken():
    out = cidr_for_index("10.200.0.0/16", 24, 17, {"10.200.17.0/24"})
    assert out == "10.200.1.0/24"


def test_allocate_raises_when_exhausted():
    with pytest.raises(RuntimeError):
        allocate_cidr("10.0.0.0/30", 31, {"10.0.0.2/31"})


def test_overlapping_subnets_excludes_an_exact_match():
    # A leftover Docker network holding a carved /24 → that exact /24 is excluded.
    assert overlapping_subnets("10.200.0.0/16", 24, {"10.200.1.0/24"}) == {"10.200.1.0/24"}


def test_overlapping_subnets_is_overlap_aware_not_string_equality():
    # An in-use CIDR that is NOT /prefix-aligned still masks every /24 it overlaps — a /23 spanning
    # 10.200.4.0–10.200.5.255 masks both 10.200.4.0/24 and 10.200.5.0/24.
    assert overlapping_subnets("10.200.0.0/16", 24, {"10.200.4.0/23"}) == {
        "10.200.4.0/24",
        "10.200.5.0/24",
    }


def test_overlapping_subnets_ignores_cidrs_outside_base_and_junk():
    # Docker's own bridges (172.17.0.0/16, etc.) live outside the run pool and contribute nothing;
    # an unparseable token is skipped rather than raising.
    assert overlapping_subnets("10.200.0.0/16", 24, {"172.17.0.0/16", "not-a-cidr"}) == set()


def test_allocate_skips_a_subnet_a_leftover_network_still_holds():
    # The end-to-end intent: union the overlap set into `allocated` and the first free /24 skips it.
    leftover = overlapping_subnets("10.200.0.0/16", 24, {"10.200.1.0/24"})
    assert allocate_cidr("10.200.0.0/16", 24, leftover) == "10.200.2.0/24"
