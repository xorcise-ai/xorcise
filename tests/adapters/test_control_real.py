import pytest

from tests.adapters._contracts import VALID_KEY, ControlPortContract
from xorcise.core.orchestration.clients.control import RunnerControlAdapter
from xorcise.core.orchestration.ports import ControlPort
from xorcise.core.runner.docker import StubDockerDriver
from xorcise.core.runner.service import RunnerControlService


class TestRunnerControlAdapter(ControlPortContract):
    @pytest.fixture
    def port(self) -> ControlPort:
        return RunnerControlAdapter(RunnerControlService(StubDockerDriver()), api_key=VALID_KEY)
