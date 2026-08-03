"""Pull errors live in contracts (LEAF) so the catalog island can raise them."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_pull_errors_live_in_contracts_and_reexport() -> None:
    from xorcise.core.contracts.errors import (
        ContractError,
        MissionNotInCatalogError,
        PullError,
    )

    # re-exported from rest for back-compat, and it's the SAME class object
    from xorcise.core.rest.mission_pull import PullError as RestPullError

    assert issubclass(PullError, ContractError)
    assert issubclass(MissionNotInCatalogError, PullError)
    assert RestPullError is PullError
