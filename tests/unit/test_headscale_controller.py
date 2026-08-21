import json
import threading
import time

from xorcise.core.headscale.cli import StubHeadscaleCli
from xorcise.core.headscale.controller import NetworkController
from xorcise.core.headscale.policy import RunNetwork


def _controller(cli: StubHeadscaleCli) -> NetworkController:
    return NetworkController(cli, router_tag="tag:router", orchestrator_user="orchestrator")


def test_second_controller_does_not_clobber_first_via_provider():
    # two processes (own controllers) sharing one Headscale + one authoritative DB. The
    # ACL is rendered from the DB-authoritative provider unioned with the process's in-flight run,
    # so a second process's create must NOT wipe the first run's rule (the historical clobber bug).
    cli = StubHeadscaleCli()
    db: dict[str, tuple[str, tuple[str, ...]]] = {}  # the shared persisted non-terminal runs

    def provider() -> list[RunNetwork]:
        return [
            RunNetwork(agent_user=au, auth_key="", router_key="", entry_cidrs=ec)
            for au, ec in db.values()
        ]

    def _ctrl() -> NetworkController:
        return NetworkController(
            cli, router_tag="tag:router", orchestrator_user="orch", active_provider=provider
        )

    _ctrl().create_run_network("r1", "agent-1", ["10.200.1.0/24"])
    db["r1"] = ("agent-1", ("10.200.1.0/24",))  # persisted after create
    _ctrl().create_run_network("r2", "agent-2", ["10.200.2.0/24"])  # a second process
    db["r2"] = ("agent-2", ("10.200.2.0/24",))

    final = cli.policies_applied[-1]
    assert "agent-1@" in final and "agent-2@" in final  # both survive — no clobber


def test_reconcile_acl_restores_a_concurrently_clobbered_rule():
    # two separate controllers create at the same instant, BEFORE either run persists, so
    # each renders only its own rule and the second full replace clobbers the first. A post-persist
    # reconcile_acl re-renders from the now-complete DB provider and restores both.
    cli = StubHeadscaleCli()
    db: dict[str, tuple[str, tuple[str, ...]]] = {}

    def provider() -> list[RunNetwork]:
        return [
            RunNetwork(agent_user=au, auth_key="", router_key="", entry_cidrs=ec)
            for au, ec in db.values()
        ]

    def _ctrl() -> NetworkController:
        return NetworkController(
            cli, router_tag="tag:router", orchestrator_user="orch", active_provider=provider
        )

    ca, cb = _ctrl(), _ctrl()
    # concurrent: both create_run_network run before EITHER run persists (provider still empty)
    ca.create_run_network("r1", "agent-1", ["10.200.1.0/24"])
    cb.create_run_network("r2", "agent-2", ["10.200.2.0/24"])
    assert "agent-1@" not in cli.policies_applied[-1]  # clobbered (the bug)

    # each create then persists its row and reconciles; the last reconcile sees both
    db["r1"] = ("agent-1", ("10.200.1.0/24",))
    db["r2"] = ("agent-2", ("10.200.2.0/24",))
    ca.reconcile_acl()
    cb.reconcile_acl()
    final = cli.policies_applied[-1]
    assert "agent-1@" in final and "agent-2@" in final  # restored — no permanent clobber


def test_provider_and_active_are_deduped_by_agent_user():
    # The in-flight run appears in BOTH _active and (once persisted) the provider — render once.
    cli = StubHeadscaleCli()

    def provider() -> list[RunNetwork]:
        return [
            RunNetwork(
                agent_user="agent-1", auth_key="", router_key="", entry_cidrs=("10.200.1.0/24",)
            )
        ]

    ctrl = NetworkController(
        cli, router_tag="tag:router", orchestrator_user="orch", active_provider=provider
    )
    ctrl.create_run_network("r1", "agent-1", ["10.200.1.0/24"])
    doc = json.loads(cli.policies_applied[-1])
    assert sum(r["src"] == ["agent-1@"] for r in doc["acls"]) == 1


def test_create_mints_user_key_and_applies_safe_policy_with_rule():
    cli = StubHeadscaleCli()
    net = _controller(cli).create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    assert net.agent_user == "agent-1"
    assert net.auth_key and net.auth_key in cli.keys_minted
    assert "agent-1" in cli.users_created
    doc = json.loads(cli.policies_applied[-1])
    assert {"action": "accept", "src": ["agent-1@"], "dst": ["10.200.1.0/24:*"]} in doc["acls"]


def test_router_key_is_tagged_for_route_approval():
    # The router joins with a DISTINCT key carrying BOTH the shared base tag (auto-approves the
    # collector route) and its PER-RUN tag (the only thing the inbound ACL rule pins as source, so
    # one run's router can't match another run's inbound rule); the agent key stays untagged.
    from xorcise.core.headscale.policy import router_tag_for

    cli = StubHeadscaleCli()
    net = _controller(cli).create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    assert net.router_key and net.router_key != net.auth_key
    by_user = dict((u, tags) for u, tags in cli.preauth_calls)
    assert by_user["agent-1"] == ()  # agent key untagged
    assert by_user["orchestrator"] == ("tag:router", router_tag_for("agent-1", "tag:router"))
    assert "orchestrator" in cli.users_created


def test_two_runs_each_get_exactly_one_rule():
    cli = StubHeadscaleCli()
    ctrl = _controller(cli)
    ctrl.create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    ctrl.create_run_network("run-2", "agent-2", ["10.200.2.0/24"])
    doc = json.loads(cli.policies_applied[-1])
    srcs = [rule["src"] for rule in doc["acls"]]
    assert ["agent-1@"] in srcs and ["agent-2@"] in srcs


def test_teardown_removes_rule_and_deletes_user():
    # The agent user is the run-derived convention (run-<run_id[:8]>-agent) that create, the ACL
    # provider, and teardown all share, so create with that name here.
    cli = StubHeadscaleCli()
    ctrl = _controller(cli)
    ctrl.create_run_network("abcd1234ef00", "run-abcd1234-agent", ["10.200.1.0/24"])
    ctrl.teardown_run_network("abcd1234ef00")
    final = cli.policies_applied[-1]
    assert "run-abcd1234-agent@" not in final
    assert "run-abcd1234-agent" in cli.users_deleted


def test_teardown_reapplies_acl_and_deletes_user_on_fresh_controller():
    # teardown runs on a FRESH per-request controller (empty _active) — both the normal
    # completion path (build_run_create_deps) and the boot reconcile. It must therefore reclaim the
    # run's ACL rule AND its agent user from run-derived identity + the DB-authoritative provider,
    # not from this process's in-memory _active (which never held the run). Before the fix the
    # `net is None` guard skipped both the re-render and the user deletion, leaving them to leak.
    cli = StubHeadscaleCli()

    # the DB-authoritative non-terminal set: the torn-down run is already terminal (excluded); a
    # different run remains active and must survive the teardown's re-render.
    def provider() -> list[RunNetwork]:
        return [
            RunNetwork(
                agent_user="run-bbbbbbbb-agent",
                auth_key="",
                router_key="",
                entry_cidrs=("10.200.2.0/24",),
            )
        ]

    ctrl = NetworkController(
        cli, router_tag="tag:router", orchestrator_user="orch", active_provider=provider
    )
    applied_before = len(cli.policies_applied)
    ctrl.teardown_run_network("aaaaaaaa0000")  # never created in THIS controller

    assert len(cli.policies_applied) > applied_before  # the ACL WAS re-rendered
    final = cli.policies_applied[-1]
    assert "run-bbbbbbbb-agent@" in final  # the surviving run's rule is preserved
    assert "run-aaaaaaaa-agent@" not in final  # the torn-down run's rule is gone
    assert "run-aaaaaaaa-agent" in cli.users_deleted  # user reclaimed by run-derived name
    assert "aaaaaaaa0000-router" in cli.nodes_deleted_by_name  # router node still removed


def test_teardown_deletes_the_router_node():
    # the per-run router joins as the shared orchestrator user (tag:router), so it is NOT
    # covered by delete_nodes_for_user; teardown must delete it by its run-derived node name, else
    # it leaks online and a later run reusing its /24 collides on the tailnet.
    cli = StubHeadscaleCli()
    ctrl = _controller(cli)
    ctrl.create_run_network("abc123", "agent-1", ["10.200.1.0/24"])
    ctrl.teardown_run_network("abc123")
    assert "abc123-router" in cli.nodes_deleted_by_name


def test_teardown_deletes_router_even_when_not_in_active():
    # Server-restart robustness: _active is empty (never created in THIS controller) but the router
    # node must still be removed, since its name derives from run_id.
    cli = StubHeadscaleCli()
    ctrl = _controller(cli)
    ctrl.teardown_run_network("ghost99")
    assert "ghost99-router" in cli.nodes_deleted_by_name


def test_create_and_teardown_are_idempotent():
    cli = StubHeadscaleCli()
    ctrl = _controller(cli)
    ctrl.create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    ctrl.create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    doc = json.loads(cli.policies_applied[-1])
    assert sum(r["src"] == ["agent-1@"] for r in doc["acls"]) == 1
    ctrl.teardown_run_network("run-1")
    ctrl.teardown_run_network("run-1")


class _ConcurrencyCheckingCli(StubHeadscaleCli):
    """Records the peak number of overlapping apply_acl_policy calls.

    The `policy set` is a non-transactional read-render-write against the one shared Headscale;
    without a single-writer lock, two applies from distinct per-request controllers overlap and the
    stale one clobbers the other. A tiny sleep widens the window so an unserialized apply shows."""

    def __init__(self) -> None:
        super().__init__()
        self._in_flight = 0
        self.max_concurrent = 0
        self._probe = threading.Lock()

    def apply_acl_policy(self, text: str) -> None:
        with self._probe:
            self._in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self._in_flight)
        time.sleep(0.01)  # widen the critical section so overlap is observable
        super().apply_acl_policy(text)
        with self._probe:
            self._in_flight -= 1


def test_apply_is_serialized_across_per_request_controllers():
    # build_run_create_deps makes a NEW controller per request, all sharing one
    # Headscale. A single-writer lock (shared across controllers) must serialize the policy apply so
    # two never overlap — else a stale read-render-write clobbers a concurrent run's rule.
    cli = _ConcurrencyCheckingCli()
    lock = threading.Lock()
    db: dict[str, tuple[str, tuple[str, ...]]] = {}

    def provider() -> list[RunNetwork]:
        return [
            RunNetwork(agent_user=au, auth_key="", router_key="", entry_cidrs=ec)
            for au, ec in list(db.values())
        ]

    def _worker(i: int) -> None:
        # each request gets its own controller (as in prod) but shares the process-wide apply lock
        ctrl = NetworkController(
            cli,
            router_tag="tag:router",
            orchestrator_user="orch",
            active_provider=provider,
            apply_lock=lock,
        )
        au = f"agent-{i}"
        ctrl.create_run_network(f"r{i}", au, [f"10.200.{i}.0/24"])
        db[au] = (au, (f"10.200.{i}.0/24",))
        ctrl.reconcile_acl()

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(1, 6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cli.max_concurrent == 1  # never two applies at once — serialized


def test_collector_addr_injected_into_agent_rule():
    """collector_addr on the controller wires through to the rendered policy."""
    cli = StubHeadscaleCli()
    ctrl = NetworkController(
        cli,
        router_tag="tag:router",
        orchestrator_user="orchestrator",
        collector_addr="172.17.0.1",
        collector_port=4318,
    )
    ctrl.create_run_network("run-1", "agent-1", ["10.200.1.0/24"])
    doc = json.loads(cli.policies_applied[-1])
    agent_rule = next(r for r in doc["acls"] if r["src"] == ["agent-1@"])
    assert "172.17.0.1:4318" in agent_rule["dst"], (
        f"collector dst missing from agent rule dst: {agent_rule['dst']}"
    )
