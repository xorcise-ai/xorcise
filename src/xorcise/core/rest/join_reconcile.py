"""Viewer-driven Headscale join-confirm reconciler — DELIVERY layer.

Today the "agent joined Headscale" edge in the v2 infra plane lights up from a SETUP-TIME fact
(`network-lifecycle`/`join`=`created`, written before the agent has actually joined the tailnet —
see `network_facts` in runs/observed.py). This module adds the REAL signal: `reconcile_join`
queries Headscale's own node registry (`HeadscaleCli.node_online`) and, once the
run's agent node is confirmed online, records a `network-lifecycle`/`join`=`confirmed` fact. A
later change repoints the infra map to key off `confirmed` instead of `created` — this module
only produces the fact.

`reconcile_join` is sync + directly unit-testable via injected `headscale` + `store` (mirrors the
server-owned-model injection pattern in terrain_catchup_v2.py). `maybe_reconcile_join` is the
delivery-facing kicker: it resolves the real Headscale client + the run's persisted agent user,
then drains `reconcile_join` on a daemon thread under its OWN per-run in-flight lock (separate
from both v1's and v2's catch-up locks), mirroring `maybe_start_catchup_v2` exactly. DEGRADE
SAFELY throughout: no Headscale plane, no persisted agent user, or any Headscale query failure is
a silent no-op — this must never raise into a caller (e.g. a viewer poll on the terrain route).
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from xorcise.core.config import get_settings
from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.rest.run_create import _real_headscale_cli, _use_real_headscale
from xorcise.core.runs.observed import ObservedFactsStore, SqliteObservedFactsStore

if TYPE_CHECKING:
    from xorcise.core.headscale import HeadscaleCli

_inflight: set[str] = set()
_lock = threading.Lock()

# The worker POLLS Headscale for the agent's node coming online, so a single kick (from the agent
# fetching its join bundle, or a terrain view) reliably catches the join without depending on the
# node being online at one exact instant. Bounded so a run whose agent never joins doesn't poll
# forever: ~2 minutes total, comfortably covering agent start-up + tailnet join.
_POLL_ATTEMPTS = 24
_POLL_INTERVAL_SECONDS = 5.0


def _is_confirmed(facts: tuple[ObservedFact, ...]) -> bool:
    return any(
        f.kind == "network-lifecycle" and f.name == "join" and f.value == "confirmed" for f in facts
    )


# The OTel collector connection, persisted as a runtime fact at connection time (the first
# observed span), symmetric with the join-confirmed fact above — so both XORCISE-infra
# connections are durable DB records with a connection time, not values re-derived on every read.
_TELEMETRY_KIND = "telemetry-lifecycle"
_TELEMETRY_NAME = "collector"
_TELEMETRY_VALUE = "connected"


def _is_telemetry_confirmed(facts: tuple[ObservedFact, ...]) -> bool:
    return any(
        f.kind == _TELEMETRY_KIND and f.name == _TELEMETRY_NAME and f.value == _TELEMETRY_VALUE
        for f in facts
    )


def run_join_confirmed(run_id: str) -> bool:
    """Cheap read: True iff a `network-lifecycle`/`join`=`confirmed` fact already exists."""
    return _is_confirmed(SqliteObservedFactsStore().list_for_run(run_id))


def run_telemetry_confirmed(run_id: str) -> bool:
    """Cheap read: True iff a `telemetry-lifecycle`/`collector`=`connected` fact already exists."""
    return _is_telemetry_confirmed(SqliteObservedFactsStore().list_for_run(run_id))


def reconcile_join(
    run_id: str,
    *,
    headscale: HeadscaleCli,
    agent_user: str,
    store: ObservedFactsStore,
) -> None:
    """Sync + directly testable: if not already confirmed and the agent's node is online in
    Headscale, record the `confirmed` fact. Never raises — a Headscale query failure (control
    plane down, malformed response, anything) degrades to a silent no-op."""
    if _is_confirmed(store.list_for_run(run_id)):
        return
    try:
        online = headscale.node_online(agent_user)
    except Exception:
        return
    if not online:
        return
    store.record(
        ObservedFact(run_id=run_id, kind="network-lifecycle", name="join", value="confirmed")
    )


def reconcile_telemetry(run_id: str, *, has_telemetry: bool, store: ObservedFactsStore) -> None:
    """Sync + directly testable: if not already recorded and the run has exported ≥1 span
    (`has_telemetry`), persist the `telemetry-lifecycle`/`collector`=`connected` fact.
    `has_telemetry` is injected (the caller reads the trace store) — a pure store decision."""
    if not has_telemetry:
        return
    if _is_telemetry_confirmed(store.list_for_run(run_id)):
        return
    store.record(
        ObservedFact(
            run_id=run_id, kind=_TELEMETRY_KIND, name=_TELEMETRY_NAME, value=_TELEMETRY_VALUE
        )
    )


def _has_telemetry(run_id: str) -> bool:
    """True iff the run has exported ≥1 raw span. Lazy import keeps the OTel store off this
    module's import path (plane isolation); never raises."""
    try:
        from xorcise.core.otel.store import SqliteTraceStore

        return bool(SqliteTraceStore().read(run_id))
    except Exception:
        return False


def maybe_reconcile_join(run_id: str) -> None:
    """Delivery-facing kicker for the XORCISE-infra CONNECTION reconciler. Persists two runtime
    facts at connection time: `network-lifecycle`/`join`=`confirmed` (the agent's node online in
    Headscale) and `telemetry-lifecycle`/`collector`=`connected` (the run's first exported span).
    No-op once BOTH are recorded. Spawns a daemon thread (own per-run lock, mirroring
    `maybe_start_catchup_v2`) that POLLS for each connection and latches its fact. The join half
    needs the real Headscale client + the persisted agent user; the telemetry half needs neither,
    so a stub/local run (no real Headscale) still records telemetry. DEGRADE SAFELY throughout —
    never raises into a caller (e.g. a `/terrain2` view or the `/join.sh` handler)."""
    store = SqliteObservedFactsStore()
    facts = store.list_for_run(run_id)
    join_done = _is_confirmed(facts)
    telemetry_done = _is_telemetry_confirmed(facts)
    if join_done and telemetry_done:
        return  # both connections already recorded — nothing to reconcile

    agent_user = next(
        (f.value for f in facts if f.kind == "network-lifecycle" and f.name == "agent-user"),
        None,
    )
    # The join half is reconcilable only with a real Headscale plane + the run's agent user (the
    # settings check is cheap + pure — never build the docker-exec client on the request thread).
    can_join = (not join_done) and agent_user is not None and _use_real_headscale(get_settings())
    if not can_join and telemetry_done:
        return  # can't help the join and telemetry is already recorded

    with _lock:
        if run_id in _inflight:
            return
        _inflight.add(run_id)

    def _work() -> None:
        try:
            cli: HeadscaleCli | None = None
            if can_join:
                # Build the real client (probe can raise / docker-execs) INSIDE the worker so the
                # request thread never blocks on it; degrade to telemetry-only on a build failure.
                try:
                    cli = _real_headscale_cli(get_settings())
                except Exception:
                    cli = None
            # Poll until each connection is recorded (or unreconcilable), or the window elapses.
            for attempt in range(_POLL_ATTEMPTS):
                current = store.list_for_run(run_id)
                if cli is not None and agent_user is not None and not _is_confirmed(current):
                    reconcile_join(run_id, headscale=cli, agent_user=agent_user, store=store)
                if not _is_telemetry_confirmed(current):
                    reconcile_telemetry(run_id, has_telemetry=_has_telemetry(run_id), store=store)
                final = store.list_for_run(run_id)
                # Join is "settled" when confirmed OR we have no client to ever confirm it with.
                join_settled = _is_confirmed(final) or cli is None
                if join_settled and _is_telemetry_confirmed(final):
                    return
                if attempt < _POLL_ATTEMPTS - 1:
                    time.sleep(_POLL_INTERVAL_SECONDS)
        finally:
            with _lock:
                _inflight.discard(run_id)

    threading.Thread(target=_work, name=f"infra-reconcile-{run_id}", daemon=True).start()
