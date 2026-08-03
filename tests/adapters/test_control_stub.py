from __future__ import annotations

import pytest

from tests.adapters._contracts import VALID_KEY, ControlPortContract
from xorcise.core.orchestration.clients.control import InProcessControlStub
from xorcise.core.orchestration.ports import ControlPort


class TestInProcessControlStub(ControlPortContract):
    @pytest.fixture
    def port(self) -> ControlPort:
        return InProcessControlStub(api_key=VALID_KEY)
