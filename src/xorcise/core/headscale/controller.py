"""The per-run network fence capability. PART-ISLAND.

Holds the active run set for this process, and on each create/teardown re-renders
the whole ACL, asserts it safe, and applies it atomically. Constructed with an
injected HeadscaleCli (StubHeadscaleCli in tests, DockerExecHeadscaleCli in prod).
The server-side NetworkPort wrapper lives in orchestration, not here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

from .cli import HeadscaleCli
from .policy import RunNetwork, assert_policy_safe, render_policy

# the ACL apply is a non-transactional read(DB)-render-write(`policy set`) against
# the ONE shared Headscale control plane. build_run_create_deps builds a fresh NetworkController per
# request, so a per-instance lock would not serialize concurrent requests — this process-global lock
# (shared by every controller that does not inject its own) makes the whole read-render-apply a
# single writer, so a stale apply never clobbers a concurrent run's rule. Cross-process
# serialization (a file lock, for a second server worker) is the injectable seam below; deferred.
_APPLY_LOCK: threading.Lock = threading.Lock()


def _agent_user_for_run(run_id: str) -> str:
    """The tailnet user a run's agent joins as — the run-derived convention shared with
    ``run_create._agent_user_for`` and the ACL provider. Duplicated here (like the
    ``{run_id}-router`` node name below) so teardown can reclaim the run's user + rule by identity
    on a FRESH controller whose ``_active`` never held the run, without the part-island importing
    the rest layer (dependency rule). Keep in lock-step with ``run_create._agent_user_for``."""
    return f"run-{run_id[:8]}-agent"


def _router_node_name(run_id: str) -> str:
    """The per-run subnet router's Headscale node name. It joins as the shared orchestrator user
    (tag:router), so this NAME is its only per-run identity — used both to delete it at teardown and
    to probe whether it actually came online (readiness)."""
    return f"{run_id}-router"


class NetworkController:
    def __init__(
        self,
        cli: HeadscaleCli,
        *,
        router_tag: str,
        orchestrator_user: str,
        key_expiration: str = "1h",
        collector_addr: str = "",
        collector_port: int = 4318,
        active_provider: Callable[[], Sequence[RunNetwork]] | None = None,
        apply_lock: AbstractContextManager[bool] | None = None,
    ) -> None:
        self._cli = cli
        self._router_tag = router_tag
        self._orchestrator_user = orchestrator_user
        self._key_expiration = key_expiration
        self._collector_addr = collector_addr
        self._collector_port = collector_port
        self._active: dict[str, RunNetwork] = {}
        # the authoritative non-terminal run set from the shared DB (wired by the rest
        # layer, which may read the runs module; the part-island only holds a callback). When set,
        # the ACL is rendered from this UNION with the in-process _active — so every process
        # renders the same complete policy and no full `policy set` clobbers another's rules. Only
        # agent_user + entry_cidrs are used for rendering, so the provider's keys may be empty.
        self._active_provider = active_provider
        # single-writer around the read-render-apply. Defaults to the process-wide
        # lock so per-request controllers serialize; a cross-process lock can be injected later.
        self._apply_lock: AbstractContextManager[bool] = (
            apply_lock if apply_lock is not None else _APPLY_LOCK
        )

    def _reapply(self) -> None:
        with self._apply_lock:
            self._reapply_locked()

    def _reapply_locked(self) -> None:
        # Caller holds self._apply_lock: the DB read (active_provider), render, and apply are one
        # atomic critical section, so a concurrent writer cannot interleave a
        # stale snapshot between our read and our write. Render from the DB-authoritative set (if
        # provided) UNIONED with this process's in-flight runs (not yet persisted at create time),
        # deduped by agent_user.
        by_user: dict[str, RunNetwork] = {}
        if self._active_provider is not None:
            for net in self._active_provider():
                by_user[net.agent_user] = net
        for net in self._active.values():
            by_user[net.agent_user] = net
        nets = list(by_user.values())
        text = render_policy(
            nets,
            router_tag=self._router_tag,
            orchestrator_user=self._orchestrator_user,
            collector_addr=self._collector_addr,
            collector_port=self._collector_port,
        )
        assert_policy_safe(
            text,
            nets,
            router_tag=self._router_tag,
            collector_addr=self._collector_addr,
            collector_port=self._collector_port,
        )
        self._cli.apply_acl_policy(text)

    def reconcile_acl(self) -> None:
        """Re-render + re-apply the ACL from the current authoritative set.

        Called AFTER a run row persists so the DB-authoritative provider now includes it. Under
        truly-concurrent multi-process creates the pre-persist apply in create_run_network can
        render an incomplete (clobbered) policy; this post-persist reconcile restores the complete
        policy — the last create to persist re-renders every non-terminal run's rule."""
        self._reapply()

    def create_run_network(
        self,
        run_id: str,
        agent_user: str,
        entry_cidrs: Sequence[str],
        *,
        agent_ingress: bool = False,
    ) -> RunNetwork:
        self._cli.create_user(agent_user)
        self._cli.create_user(self._orchestrator_user)  # owns the router tag; must exist to mint
        agent_key = self._cli.create_preauth_key(agent_user, expiration=self._key_expiration)
        # The router joins as the orchestrator user, ACL-tagged so autoApprovers approves its
        # advertised routes; the agent joins untagged as agent_user. Two distinct keys.
        router_key = self._cli.create_preauth_key(
            self._orchestrator_user, expiration=self._key_expiration, tags=[self._router_tag]
        )
        net = RunNetwork(
            agent_user=agent_user,
            auth_key=agent_key,
            router_key=router_key,
            entry_cidrs=tuple(entry_cidrs),
            agent_ingress=agent_ingress,
        )
        # Hold the lock across the _active mutation + apply so the render sees a consistent
        # snapshot. Key-minting above stays outside — it is per-run and independent.
        with self._apply_lock:
            self._active[run_id] = net
            self._reapply_locked()
        return net

    def teardown_run_network(self, run_id: str) -> None:
        # reclaim everything by run-derived identity + the DB-authoritative provider, NOT
        # by this process's in-memory _active. teardown is reached on a FRESH per-request controller
        # (build_run_create_deps) and from the boot reconcile, so _active almost never holds the
        # run; gating the re-render + user deletion on it (the old `net is not None` guard) leaked
        # stale ACL rules and agent users. Drop the cache entry if present, then ALWAYS re-render.
        agent_user = _agent_user_for_run(run_id)
        with self._apply_lock:
            self._active.pop(run_id, None)
            self._reapply_locked()  # renders from active_provider() ∪ _active (this run now gone)
        # Node/user deletion is per-run + independent — do it outside the apply lock so it does not
        # serialize unrelated runs' policy applies. All three CLI deletes are idempotent (no-op when
        # the entity is already gone), so running them unconditionally is safe + replayable.
        self._cli.delete_nodes_for_user(agent_user)
        self._cli.delete_user(agent_user)
        # the per-run ROUTER joins as the shared orchestrator user (tag:router), so it is
        # NOT covered by delete_nodes_for_user. Delete it by its run-derived node name so a
        # completed run never leaves an online subnet router advertising its subnet.
        self._cli.delete_node_by_name(_router_node_name(run_id))

    def router_online(self, run_id: str) -> bool:
        """True iff this run's subnet router has joined the tailnet and reports online.

        The readiness gate's tailnet half: the mission stack can be up while the router that
        advertises the run's CIDR never joined — the agent then gets a tailnet IP but no route to
        any target. Probed BY NODE NAME because the router joins as the shared orchestrator user,
        so the per-user probe cannot single it out. Never raises (the CLI degrades to False)."""
        return self._cli.node_online_by_name(_router_node_name(run_id))
