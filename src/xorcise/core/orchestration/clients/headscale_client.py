"""In-process adapter binding NetworkFencePort to the headscale NetworkController."""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.headscale import NetworkController, RunNetwork
from xorcise.core.orchestration.ports import NetworkFencePort


class HeadscaleFenceClient(NetworkFencePort):
    def __init__(self, controller: NetworkController) -> None:
        self._controller = controller

    def create_run_network(
        self, run_id: str, agent_user: str, entry_cidrs: Sequence[str]
    ) -> RunNetwork:
        return self._controller.create_run_network(run_id, agent_user, entry_cidrs)

    def teardown_run_network(self, run_id: str) -> None:
        self._controller.teardown_run_network(run_id)

    def reconcile_acl(self) -> None:
        self._controller.reconcile_acl()

    def router_online(self, run_id: str) -> bool:
        return self._controller.router_online(run_id)
