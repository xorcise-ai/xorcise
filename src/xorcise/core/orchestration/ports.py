"""In-process ABCs (ports) the server conducts peers through. LAYER: APPLICATION (domain module).

Ports live beside their application-layer owner; concrete adapters live in orchestration/clients/
and wrap each part's plain public entrypoint, so the part-islands (runner, eval)
never import these ABCs. Transport-agnostic: Shape A (HTTP) and Shape B (queue) are
adapters that satisfy the same ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from xorcise.core.contracts.control import (
    ApiKey,
    CollectTargetsResult,
    DeployRequest,
    RunId,
    RunnerEndpoints,
    StatusResult,
    TeardownResult,
)
from xorcise.core.contracts.grading import GradeRequest, GradeResult
from xorcise.core.headscale import RunNetwork


class ControlPort(ABC):
    """Server→runner control contract: idempotent, run_id-keyed, API-key authed.

    Every verb authenticates via the per-call credential; verbs are idempotent so
    Shape A→B is additive, never a rewrite.
    """

    @abstractmethod
    def deploy(self, request: DeployRequest, *, credential: ApiKey) -> RunnerEndpoints: ...

    @abstractmethod
    def status(self, run_id: RunId, *, credential: ApiKey) -> StatusResult: ...

    @abstractmethod
    def collect_targets(self, run_id: RunId, *, credential: ApiKey) -> CollectTargetsResult: ...

    @abstractmethod
    def teardown(self, run_id: RunId, *, credential: ApiKey) -> TeardownResult: ...

    def reap_orphan_environments(self, keep: Sequence[RunId], *, credential: ApiKey) -> list[RunId]:
        """Release run environments whose network outlived their run, keeping every id in `keep`.

        A leaked network keeps its subnet allocated, so the run pool bleeds a /24 per imperfect
        teardown. Non-abstract (returns nothing reaped) so existing ControlPort implementations
        keep satisfying the ABC."""
        return []


class JudgePort(ABC):
    """Server→evaluator grading contract — synchronous in-process (D12: queue deferred)."""

    @abstractmethod
    def grade(self, request: GradeRequest) -> GradeResult: ...


class NetworkFencePort(ABC):
    """Server→headscale fence contract: mint a per-run key+ACL at create, revoke at teardown.

    The concrete adapter (orchestration/clients/headscale_client.py) wraps the
    headscale NetworkController; the part-island never imports this ABC.
    """

    @abstractmethod
    def create_run_network(
        self, run_id: str, agent_user: str, entry_cidrs: Sequence[str]
    ) -> RunNetwork: ...

    @abstractmethod
    def teardown_run_network(self, run_id: str) -> None: ...

    @abstractmethod
    def reconcile_acl(self) -> None:
        """Re-render the ACL from the authoritative (persisted) run set — called post-persist so a
        concurrently-clobbered rule is restored."""
        ...

    def router_online(self, run_id: str) -> bool:
        """True iff this run's subnet ROUTER has joined the tailnet and is online.

        The readiness gate's tailnet half: the mission stack can be up while the router that
        advertises its CIDR never joined, leaving the agent with an IP but no route to any target.
        Non-abstract with a permissive default so a fence that cannot report (stubs, in-process test
        doubles) degrades to "assume ready" instead of wedging every run at PENDING."""
        return True
