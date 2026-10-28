"""Viewer-driven Headscale join-confirm reconciler (Phase 3 Task 2).

`reconcile_join` is the sync, directly-testable core: given an injected HeadscaleCli + an
injected ObservedFactsStore, it records a REAL `network-lifecycle`/`join`=`confirmed` fact once
the run's agent node is actually online in Headscale — never a duplicate, never a raise even if
the Headscale query blows up. `run_join_confirmed` is the cheap public read of that same fact.
"""

from __future__ import annotations

from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.rest.join_reconcile import (
    reconcile_join,
    reconcile_telemetry,
    run_join_confirmed,
)
from xorcise.core.runs.observed import InMemoryObservedFactsStore


def _telemetry_facts(store: InMemoryObservedFactsStore, run_id: str) -> tuple[ObservedFact, ...]:
    return tuple(
        f
        for f in store.list_for_run(run_id)
        if f.kind == "telemetry-lifecycle" and f.name == "collector" and f.value == "connected"
    )


def test_reconcile_telemetry_records_connected_fact_on_first_span():
    store = InMemoryObservedFactsStore()
    reconcile_telemetry("r1", has_telemetry=True, store=store)
    assert len(_telemetry_facts(store, "r1")) == 1


def test_reconcile_telemetry_no_fact_without_telemetry():
    store = InMemoryObservedFactsStore()
    reconcile_telemetry("r1", has_telemetry=False, store=store)
    assert _telemetry_facts(store, "r1") == ()


def test_reconcile_telemetry_is_idempotent():
    store = InMemoryObservedFactsStore()
    reconcile_telemetry("r1", has_telemetry=True, store=store)
    reconcile_telemetry("r1", has_telemetry=True, store=store)
    assert len(_telemetry_facts(store, "r1")) == 1  # no duplicate


class _FakeHeadscaleCli:
    """Minimal stand-in exposing a settable node_online, mirroring StubHeadscaleCli."""

    def __init__(self, *, online: bool = False, raises: bool = False) -> None:
        self._online = online
        self._raises = raises
        self.calls: list[str] = []

    def node_online(self, user: str) -> bool:
        self.calls.append(user)
        if self._raises:
            raise RuntimeError("boom: headscale unreachable")
        return self._online


def _confirmed_facts(store: InMemoryObservedFactsStore, run_id: str) -> tuple[ObservedFact, ...]:
    return tuple(
        f
        for f in store.list_for_run(run_id)
        if f.kind == "network-lifecycle" and f.name == "join" and f.value == "confirmed"
    )


def test_reconcile_join_records_confirmed_fact_when_node_online():
    store = InMemoryObservedFactsStore()
    headscale = _FakeHeadscaleCli(online=True)

    reconcile_join(
        "run-1",
        headscale=headscale,  # type: ignore[arg-type]
        agent_user="run-1-agent",
        store=store,
    )

    confirmed = _confirmed_facts(store, "run-1")
    assert len(confirmed) == 1
    assert confirmed[0].run_id == "run-1"
    assert headscale.calls == ["run-1-agent"]


def test_reconcile_join_no_fact_when_node_offline():
    store = InMemoryObservedFactsStore()
    headscale = _FakeHeadscaleCli(online=False)

    reconcile_join("run-1", headscale=headscale, agent_user="run-1-agent", store=store)  # type: ignore[arg-type]

    assert _confirmed_facts(store, "run-1") == ()


def test_reconcile_join_no_duplicate_when_already_confirmed():
    store = InMemoryObservedFactsStore()
    store.record(
        ObservedFact(run_id="run-1", kind="network-lifecycle", name="join", value="confirmed")
    )
    headscale = _FakeHeadscaleCli(online=True)

    reconcile_join("run-1", headscale=headscale, agent_user="run-1-agent", store=store)  # type: ignore[arg-type]

    confirmed = _confirmed_facts(store, "run-1")
    assert len(confirmed) == 1
    # already confirmed => reconcile_join must short-circuit without even querying Headscale.
    assert headscale.calls == []


def test_reconcile_join_swallows_headscale_error():
    store = InMemoryObservedFactsStore()
    headscale = _FakeHeadscaleCli(raises=True)

    reconcile_join("run-1", headscale=headscale, agent_user="run-1-agent", store=store)  # type: ignore[arg-type]

    assert _confirmed_facts(store, "run-1") == ()


def test_run_join_confirmed_true_when_confirmed_fact_present(monkeypatch):
    store = InMemoryObservedFactsStore()
    store.record(
        ObservedFact(run_id="run-1", kind="network-lifecycle", name="join", value="confirmed")
    )
    monkeypatch.setattr("xorcise.core.rest.join_reconcile.SqliteObservedFactsStore", lambda: store)

    assert run_join_confirmed("run-1") is True


def test_run_join_confirmed_false_when_absent(monkeypatch):
    store = InMemoryObservedFactsStore()
    store.record(
        ObservedFact(run_id="run-1", kind="network-lifecycle", name="join", value="created")
    )
    monkeypatch.setattr("xorcise.core.rest.join_reconcile.SqliteObservedFactsStore", lambda: store)

    assert run_join_confirmed("run-1") is False


def test_run_join_confirmed_false_for_unknown_run(monkeypatch):
    store = InMemoryObservedFactsStore()
    monkeypatch.setattr("xorcise.core.rest.join_reconcile.SqliteObservedFactsStore", lambda: store)

    assert run_join_confirmed("no-such-run") is False


def test_the_reconciler_stops_polling_once_the_run_is_sealed(migrated_home, monkeypatch):
    """#194: a sealed run's facts are sealed evidence and the store refuses any more, so polling
    Headscale for its join, a docker exec every five seconds for two minutes, is wasted. Drives the
    thread's body directly: the suite stubs the kicker that would start the thread."""
    import xorcise.core.rest.join_reconcile as jr
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    polled: list[str] = []

    class _Headscale:
        def node_online(self, user: str) -> bool:
            polled.append(user)
            return False

    monkeypatch.setattr(jr, "_POLL_INTERVAL_SECONDS", 0.0)
    store = SqliteObservedFactsStore()

    jr._poll_connections("live", cli=_Headscale(), agent_user="agent-live", store=store)  # type: ignore[arg-type]
    assert len(polled) == jr._POLL_ATTEMPTS  # the control: an unsealed run polls the whole window

    polled.clear()
    SqliteSealStore().seal("sealed")
    jr._poll_connections("sealed", cli=_Headscale(), agent_user="agent-sealed", store=store)  # type: ignore[arg-type]
    assert polled == []


def test_has_telemetry_answers_without_reading_the_spans(migrated_home):
    from xorcise.core.contracts.telemetry import TraceRecord
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.rest.join_reconcile import has_telemetry

    assert has_telemetry("r1") is False
    SqliteTraceStore().append(TraceRecord(run_id="r1", seq=0, payload="{}"))
    assert has_telemetry("r1") is True
    assert has_telemetry("r2") is False
