from __future__ import annotations

from datetime import UTC, datetime, timedelta

from xorcise.core.contracts.agent_event import AgentEvent, AgentEventKind, RawTraceRef
from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.runs.terrain_update_store import _UpdateInput
from xorcise.core.runs.terrain_updates_infra import infra_updates


def _dt(sec: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sec)


def _fact(kind: str, name: str, value: str, *, created_at: datetime | None = None) -> ObservedFact:
    return ObservedFact(run_id="r1", kind=kind, name=name, value=value, created_at=created_at)


def _ev(eid: str, body: str, kind: AgentEventKind = AgentEventKind.terminal_output) -> AgentEvent:
    return AgentEvent(
        run_id="r1",
        id=eid,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        source_agent="a",
        kind=kind,
        title=body[:40],
        body=body,
        raw_ref=RawTraceRef(run_id="r1", raw_seq=1, span_id="s"),
    )


# infra_updates now returns (anchor, _UpdateInput) pairs — helpers to project them.
def _by_target(updates: list[tuple[datetime | None, _UpdateInput]]) -> dict[str, _UpdateInput]:
    """target_id -> update (last-wins, e.g. the final m:agent-rc note)."""
    return {u.target_id: u for _, u in updates}


def _targets(updates: list[tuple[datetime | None, _UpdateInput]]) -> set[tuple[str, str]]:
    return {(u.target_kind, u.target_id) for _, u in updates}


def _anchors_for(
    updates: list[tuple[datetime | None, _UpdateInput]], target_id: str
) -> list[datetime | None]:
    return [a for a, u in updates if u.target_id == target_id]


def test_join_confirmed_fact_discovers_hs_join_node_and_activates_agent_hs_edge():
    """Real join-confirmed fact (agent joined tailnet) lights up hs:join + m:agent-hs."""
    updates = infra_updates([_fact("network-lifecycle", "join", "confirmed")], [])
    by_target = _by_target(updates)

    node = by_target["hs:join"]
    assert node.target_kind == "node"
    assert node.state == "discovered"
    assert node.event_id is None
    assert node.note  # v1's infra label restored (non-empty)

    edge = by_target["m:agent-hs"]
    assert edge.target_kind == "edge"
    assert edge.active is True
    assert edge.event_id is None
    assert edge.note


def test_join_created_fact_alone_yields_no_hs_join_activation():
    """Setup-time join-created fact (before agent really joins) produces no activation."""
    updates = infra_updates([_fact("network-lifecycle", "join", "created")], [])
    assert _targets(updates) == set(), "Expected no updates for join-created-only"


def test_artifact_submission_discovers_rc_artifacts_node_and_activates_agent_rc_edge():
    # collapsed rc:* node notes are invisible, so the submission must ALSO light the agent<->rc edge
    updates = infra_updates([], [("artifact", _dt(10))])
    by_target = _by_target(updates)
    node = by_target["rc:artifacts"]
    assert node.target_kind == "node" and node.state == "discovered" and node.note
    edge = by_target["m:agent-rc"]
    assert edge.target_kind == "edge" and edge.active is True and edge.note


def test_flag_counts_as_an_artifact_submission():
    updates = infra_updates([], [("flag", _dt(10))])
    targets = {u.target_id for _, u in updates}
    assert "rc:artifacts" in targets and "m:agent-rc" in targets


def test_complete_submission_discovers_rc_done_node_and_activates_agent_rc_edge():
    updates = infra_updates([], [("complete", _dt(10))])
    by_target = _by_target(updates)
    node = by_target["rc:done"]
    assert node.target_kind == "node" and node.state == "discovered" and node.note
    edge = by_target["m:agent-rc"]
    assert edge.target_kind == "edge" and edge.active is True and edge.note


def test_intel_submission_discovers_rc_intel_node_and_activates_agent_rc_edge():
    updates = infra_updates([], [("intel", _dt(7))])
    by_target = _by_target(updates)
    node = by_target["rc:intel"]
    assert node.target_kind == "node" and node.state == "discovered" and node.note
    assert by_target["m:agent-rc"].active is True
    assert _anchors_for(updates, "rc:intel") == [_dt(7)]  # anchored to the intel submission time


def test_attachment_fetch_fact_discovers_rc_attachments_node_and_activates_agent_rc_edge():
    updates = infra_updates(
        [_fact("runcontrol-lifecycle", "attachment", "fetched", created_at=_dt(3))], []
    )
    by_target = _by_target(updates)
    node = by_target["rc:attachments"]
    assert node.target_kind == "node" and node.state == "discovered" and node.event_id is None
    assert by_target["m:agent-rc"].active is True and by_target["m:agent-rc"].event_id is None
    assert _anchors_for(updates, "rc:attachments") == [_dt(3)]  # anchored to the fetch fact time


def test_each_run_control_interaction_anchors_to_its_own_record_time():
    """The crux of per-path anchoring: every rc:* node AND every agent<->run-control edge update
    lands at ITS OWN driving-record created_at — so time-travel shows the correct last run-control
    action at each fold position, instead of all rc updates collapsing onto one heuristic time."""
    updates = infra_updates(
        [
            _fact("runcontrol-lifecycle", "prompt", "fetched", created_at=_dt(1)),
            _fact("runcontrol-lifecycle", "attachment", "fetched", created_at=_dt(4)),
        ],
        [("intel", _dt(6)), ("artifact", _dt(9)), ("complete", _dt(12))],
    )
    assert _anchors_for(updates, "rc:prompt") == [_dt(1)]
    assert _anchors_for(updates, "rc:attachments") == [_dt(4)]
    assert _anchors_for(updates, "rc:intel") == [_dt(6)]
    assert _anchors_for(updates, "rc:artifacts") == [_dt(9)]
    assert _anchors_for(updates, "rc:done") == [_dt(12)]
    # the single agent<->run-control edge is lit once per interaction, each at its own time
    assert _anchors_for(updates, "m:agent-rc") == [_dt(1), _dt(4), _dt(6), _dt(9), _dt(12)]


def test_completed_run_agent_rc_edge_note_reflects_the_latest_interaction_not_the_brief():
    # brief -> submission -> done: the run-control edge note surfaces the LAST action (done), so a
    # completed run's Run-control node no longer shows only "fetched the brief".
    updates = infra_updates(
        [_fact("runcontrol-lifecycle", "prompt", "fetched", created_at=_dt(1))],
        [("artifact", _dt(10)), ("complete", _dt(20))],
    )
    rc_edge_notes = [u.note for _, u in updates if u.target_id == "m:agent-rc"]
    assert len(rc_edge_notes) == 3  # brief, artifact, done
    assert rc_edge_notes[-1] == "Marked the run done"  # last-wins when the fold picks the latest


def test_telemetry_active_discovers_collector_node_and_activates_agent_collector_edge():
    """When telemetry_ts is set, collector node + m:agent-collector edge are activated."""
    updates = infra_updates([], [], telemetry_ts=_dt(5))
    by_target = _by_target(updates)

    collector_node = by_target["collector"]
    assert collector_node.target_kind == "node"
    assert collector_node.state == "discovered"
    assert collector_node.event_id is None
    assert collector_node.note

    collector_edge = by_target["m:agent-collector"]
    assert collector_edge.target_kind == "edge"
    assert collector_edge.active is True
    assert collector_edge.event_id is None
    assert collector_edge.note
    assert _anchors_for(updates, "collector") == [_dt(5)]  # anchored to the first-span receipt


def test_telemetry_inactive_omits_collector():
    """When telemetry_ts is None (default), no collector activation."""
    updates = infra_updates([], [], telemetry_ts=None)
    targets = {u.target_id for _, u in updates}
    assert "collector" not in targets
    assert "m:agent-collector" not in targets


def test_telemetry_default_omits_collector():
    """When telemetry_ts omitted (default), no collector activation."""
    updates = infra_updates([], [])
    targets = {u.target_id for _, u in updates}
    assert "collector" not in targets
    assert "m:agent-collector" not in targets


def test_no_evidence_yields_no_updates():
    assert infra_updates([], []) == []


def test_all_signals_together_yield_all_updates_with_confirmed_join():
    """Confirmed join + artifacts + complete + telemetry → the infra updates plus the
    agent-connected node (the agent lights up once it has reached XORCISE)."""
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed", created_at=_dt(2))],
        [("artifact", _dt(10)), ("complete", _dt(20))],
        telemetry_ts=_dt(5),
    )
    assert _targets(updates) == {
        ("node", "agent"),
        ("node", "hs:join"),
        ("edge", "m:agent-hs"),
        ("node", "rc:artifacts"),
        ("node", "rc:done"),
        ("edge", "m:agent-rc"),  # submissions light the agent<->run-control edge too
        ("node", "collector"),
        ("edge", "m:agent-collector"),
    }


# --- agent node lights up on connect (yellow) -------------------------------------------------


def test_agent_node_discovered_on_telemetry_connect():
    """The agent workspace node must turn active (yellow) as soon as it connects — the OTel
    collector receiving spans is that signal. Previously the agent node stayed grey all run."""
    updates = infra_updates([], [], telemetry_ts=_dt(5))
    agent = next(u for _, u in updates if u.target_id == "agent")
    assert agent.target_kind == "node" and agent.state == "discovered" and agent.note


def test_agent_node_discovered_on_join_confirmed():
    updates = infra_updates([_fact("network-lifecycle", "join", "confirmed")], [])
    agent = next(u for _, u in updates if u.target_id == "agent")
    assert agent.target_kind == "node" and agent.state == "discovered"


def test_agent_node_anchors_to_the_earliest_connection_signal():
    # agent "connects" at min(join, first-telemetry) on the server clock
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed", created_at=_dt(30))],
        [],
        telemetry_ts=_dt(10),
    )
    assert _anchors_for(updates, "agent") == [_dt(10)]


def test_agent_node_stays_grey_without_any_connection():
    # No join, no telemetry — the agent hasn't connected, so it stays defined (grey).
    updates = infra_updates([], [("artifact", _dt(10))])
    assert not any(u.target_id == "agent" for _, u in updates)


# --- infra updates are the deterministic plane: NEVER event_id-linked to a span ---------------


def test_infra_updates_never_carry_a_span_event_id():
    """Infra updates must all have event_id=None even when the join span is in the trace. Linking
    them to a span split the infra block across the time-travel fold (selecting the join span
    rewound to only part of the infra plane) and made the setup span a click-to-rewind target."""
    join_span = _ev("v7bl7icCJaM=:out", "=== JOINING ===\nxorcise: joined tailnet as 100.64.0.2")
    updates = infra_updates(
        [
            _fact("network-lifecycle", "join", "confirmed"),
            _fact("runcontrol-lifecycle", "prompt", "fetched"),
        ],
        [("artifact", _dt(10)), ("complete", _dt(20))],
        telemetry_ts=_dt(5),
        events=[join_span],
    )
    assert all(u.event_id is None for _, u in updates)


def test_join_span_supplies_the_address_to_the_note_but_not_the_event_id():
    """The join span only enriches the note with the assigned tailnet address; it does NOT set
    event_id (infra stays deterministic / event_id=None)."""
    join_span = _ev("v7bl7icCJaM=:out", "=== JOINING ===\nxorcise: joined tailnet as 100.64.0.2")
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed")], [], events=[join_span]
    )
    by_target = _by_target(updates)
    hs_note = by_target["hs:join"].note
    assert hs_note is not None and "100.64.0.2" in hs_note
    assert by_target["hs:join"].event_id is None
    assert by_target["m:agent-hs"].event_id is None
    assert by_target["agent"].event_id is None


def test_join_note_ignores_an_unexpanded_placeholder_and_falls_back_to_the_plain_label():
    """The join SCRIPT SOURCE echoes '...joined tailnet as $IP'; that literal shell variable (the
    trace often captures the command/source before the real output) must NOT leak into the note —
    only a real IPv4 enriches it, else the plain label stands."""
    src_span = _ev("s1:out", 'echo "xorcise: joined tailnet as $IP (userspace mode)."')
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed")], [], events=[src_span]
    )
    assert _by_target(updates)["hs:join"].note == "Agent joined the tailnet"


def test_join_note_finds_the_real_ipv4_even_after_a_placeholder_span():
    """The assigned address is found by scanning ALL events for a real IPv4 — so the script-source
    '$IP' span preceding the runtime-output span doesn't shadow the real address."""
    src_span = _ev("s1:out", "joined tailnet as $IP")  # script source, no IP
    out_span = _ev("s2:out", "xorcise: joined tailnet as 100.64.0.7 (userspace mode).")  # real
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed")], [], events=[src_span, out_span]
    )
    assert _by_target(updates)["hs:join"].note == "Agent joined the tailnet as 100.64.0.7"


def test_join_note_falls_back_when_the_ipv4_is_malformed_out_of_range():
    """A shape-matching but INVALID address (octet > 255) is not a cleanly-recovered IP — degrade to
    the plain label rather than leak garbage into the note."""
    bad = _ev("s1:out", "joined tailnet as 999.1.2.3")
    updates = infra_updates([_fact("network-lifecycle", "join", "confirmed")], [], events=[bad])
    assert _by_target(updates)["hs:join"].note == "Agent joined the tailnet"


def test_join_note_skips_a_malformed_ipv4_for_a_valid_one_later():
    """A malformed address must not shadow a real one that appears later — keep scanning."""
    bad = _ev("s1:out", "joined tailnet as 256.256.256.256")
    good = _ev("s2:out", "xorcise: joined tailnet as 100.64.0.9 (userspace mode).")
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed")], [], events=[bad, good]
    )
    assert _by_target(updates)["hs:join"].note == "Agent joined the tailnet as 100.64.0.9"


def test_brief_fetch_fact_lights_run_control_prompt():
    """The prompt-fetched fact (recorded when the agent GETs /mission) lights rc:prompt +
    m:agent-rc, with event_id=None like every other infra update."""
    updates = infra_updates([_fact("runcontrol-lifecycle", "prompt", "fetched")], [])
    by_target = _by_target(updates)
    assert by_target["rc:prompt"].state == "discovered"
    assert by_target["rc:prompt"].event_id is None
    assert by_target["m:agent-rc"].active is True
    assert by_target["m:agent-rc"].event_id is None


def test_run_control_prompt_is_fact_gated_not_span_gated():
    """No prompt-fetched fact => no rc:prompt, regardless of any trace content."""
    updates = infra_updates([], [], events=[_ev("x=:out", "anything at all")])
    targets = {u.target_id for _, u in updates}
    assert "rc:prompt" not in targets and "m:agent-rc" not in targets


def test_irrelevant_facts_and_kinds_are_ignored():
    updates = infra_updates(
        [_fact("acl-config", "something", "x")],
        [("not-a-real-kind", _dt(1))],
    )
    assert updates == []


def test_mixed_naive_and_aware_anchors_min_cleanly_for_the_agent_node():
    """Regression (terrain2 500): SQLite returns the join fact's created_at tz-naive while the
    first-span telemetry receipt is UTC-aware — the agent-node `min()` must normalize both to
    UTC-aware, not raise `TypeError: can't compare offset-naive and offset-aware datetimes`."""
    naive_join = datetime(2026, 1, 1, 0, 0, 20)  # tz-naive, as SQLite hands it back
    aware_telemetry = _dt(5)

    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed", created_at=naive_join)],
        [],
        telemetry_ts=aware_telemetry,
    )

    assert _anchors_for(updates, "agent") == [aware_telemetry]  # earliest signal wins
    # every emitted anchor is UTC-aware, including the normalized naive join fact
    assert all(a.tzinfo is not None for a, _ in updates if a is not None)
    assert _anchors_for(updates, "hs:join") == [naive_join.replace(tzinfo=UTC)]


def test_naive_join_earlier_than_aware_telemetry_anchors_the_agent_to_the_join():
    """The symmetric case: once normalized, an earlier naive join beats the telemetry receipt."""
    naive_join = datetime(2026, 1, 1, 0, 0, 2)
    updates = infra_updates(
        [_fact("network-lifecycle", "join", "confirmed", created_at=naive_join)],
        [],
        telemetry_ts=_dt(5),
    )
    assert _anchors_for(updates, "agent") == [naive_join.replace(tzinfo=UTC)]


def test_naive_submission_anchors_are_normalized_to_utc():
    """Submission created_at (SQLite-naive) is normalized like every other anchor."""
    updates = infra_updates([], [("artifact", datetime(2026, 1, 1, 0, 0, 7))])
    assert _anchors_for(updates, "rc:artifacts") == [_dt(7)]
