from __future__ import annotations

import pytest

from tests.adapters._contracts import JudgePortContract
from xorcise.core.orchestration.clients.grading import StubJudge
from xorcise.core.orchestration.ports import JudgePort


class TestStubJudge(JudgePortContract):
    @pytest.fixture
    def judge(self) -> JudgePort:
        return StubJudge()
