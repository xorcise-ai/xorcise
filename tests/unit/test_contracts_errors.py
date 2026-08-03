from __future__ import annotations

import pytest

from xorcise.core.contracts.errors import (
    AuthError,
    ConflictError,
    ContractError,
    NotFoundError,
)


@pytest.mark.parametrize("exc", [AuthError, ConflictError, NotFoundError])
def test_errors_subclass_contract_error(exc: type[ContractError]) -> None:
    assert issubclass(exc, ContractError)
    with pytest.raises(ContractError):
        raise exc("boom")
