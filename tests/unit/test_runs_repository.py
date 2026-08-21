from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xorcise.core import runs


def test_list_runs_returns_newest_first(migrated_home):
    # GET /runs leads with the most recent run so the Run History page + the dashboard's
    # Recent-runs panel don't have to re-sort. created_at is a microsecond Python default, so
    # sequential creates are strictly ordered.
    runs.create_run("a", "c1", run_id="r1")
    runs.create_run("a", "c2", run_id="r2")
    runs.create_run("a", "c3", run_id="r3")

    assert [r.run_id for r in runs.list_runs()] == ["r3", "r2", "r1"]


def test_intel_policy_defaults_to_all_and_round_trips(migrated_home):
    # A run created without a policy defaults to "all" (back-compat); a chosen policy persists and
    # is carried on RunEntry for both create and read paths.
    default = runs.create_run("a", "c1", run_id="r1")
    assert default.intel_policy == "all"
    chosen = runs.create_run("a", "c2", run_id="r2", intel_policy="i1,i3")
    assert chosen.intel_policy == "i1,i3"
    reread = runs.get("r2")
    assert reread is not None and reread.intel_policy == "i1,i3"


def test_reserve_then_finalize_persists_intel_policy(migrated_home):
    # The reservation captures the policy; finalize keeps it — mirrors the create spine.
    runs.reserve_run("rr1", "ag", "c", network_cidr="", entry_cidrs="", intel_policy="none")
    reserved = runs.get("rr1")
    assert reserved is not None and reserved.intel_policy == "none"
    finalized = runs.finalize_run("rr1", intel_policy="none")
    assert finalized.intel_policy == "none"


def test_active_cidrs_excludes_terminal_and_empty(migrated_home):
    # active_cidrs is the set of /24s in use by NON-terminal runs (empties excluded);
    # subnet allocation excludes these so a new run never reuses a live run's subnet.
    a = runs.create_run("agent-1", "sqli", run_id="r1", network_cidr="10.200.1.0/24")
    runs.create_run("agent-1", "idor", run_id="r2", network_cidr="10.200.2.0/24")
    runs.create_run("agent-1", "x", run_id="r3", network_cidr="")  # no subnet → excluded
    runs.mark_terminal("r1", "done", datetime.now(UTC))  # freed on terminal

    assert runs.active_cidrs() == {"10.200.2.0/24"}
    assert a.run_id == "r1"


def test_active_run_networks_returns_nonterminal_with_entry_cidrs(migrated_home):
    # the ACL is rendered from persisted non-terminal runs; active_run_networks() returns
    # (run_id, entry_cidrs) for each, excluding terminal runs and runs with no entry_cidrs.
    runs.create_run("a", "c1", run_id="r1", entry_cidrs="10.200.1.0/24")
    runs.create_run("a", "c2", run_id="r2", entry_cidrs="10.200.2.0/25,10.200.2.128/25")
    runs.create_run("a", "c3", run_id="r3", entry_cidrs="")  # no cidrs → excluded
    runs.mark_terminal("r1", "done", datetime.now(UTC))  # terminal → excluded

    assert dict(runs.active_run_networks()) == {
        "r2": ("10.200.2.0/25", "10.200.2.128/25"),
    }


def test_reserve_run_makes_cidr_active_before_finalize(migrated_home):
    # the reservation is durable + visible the instant it's taken (before deploy/finalize).
    runs.reserve_run("rr1", "ag", "sqli", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    assert "10.200.5.0/24" in runs.active_cidrs()
    assert dict(runs.active_run_networks())["rr1"] == ("10.200.5.0/24",)


def test_reserve_run_rejects_a_duplicate_live_cidr(migrated_home):
    # the partial unique index makes a concurrent same-subnet reservation fail atomically.
    from sqlalchemy.exc import IntegrityError

    runs.reserve_run("rr1", "ag", "c", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    with pytest.raises(IntegrityError):
        runs.reserve_run(
            "rr2", "ag", "c", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24"
        )


def test_terminal_frees_the_cidr_for_reuse(migrated_home):
    # the index is partial (non-terminal only) — a terminal run's subnet is reusable.
    runs.reserve_run("rr1", "ag", "c", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    runs.mark_terminal("rr1", "done", datetime.now(UTC))
    runs.reserve_run("rr2", "ag", "c", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    assert dict(runs.active_run_networks()) == {"rr2": ("10.200.5.0/24",)}


def test_active_runs_to_reconcile_flags_deployed_vs_reserved_only(migrated_home):
    # boot reconcile needs, per non-terminal run, whether deploy finalized
    # (prompt set) or the row is merely a reservation (crash between reserve and finalize).
    runs.reserve_run("res", "ag", "c", network_cidr="10.200.1.0/24")  # reserved only → prompt ""
    runs.reserve_run("dep", "ag", "c", network_cidr="10.200.2.0/24")
    runs.finalize_run("dep", prompt="P")  # finalized → prompt set → was_deployed True
    runs.reserve_run("term", "ag", "c", network_cidr="10.200.3.0/24")
    runs.finalize_run("term", prompt="P")
    runs.mark_terminal("term", "done", datetime.now(UTC))  # terminal → excluded

    assert dict(runs.active_runs_to_reconcile()) == {"res": False, "dep": True}


def test_finalize_run_fills_in_the_reserved_row(migrated_home):
    runs.reserve_run("rr1", "ag", "sqli", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    run = runs.finalize_run(
        "rr1", budget_seconds=300, prompt="P", run_control_key="rk", join_key="jk", model="m"
    )
    assert run.run_id == "rr1" and run.budget_seconds == 300
    assert runs.get_prompt("rr1") == "P"
    assert runs.get_join_key("rr1") == "jk"
    assert "10.200.5.0/24" in runs.active_cidrs()  # cidr preserved across finalize


def test_delete_run_releases_the_reservation(migrated_home):
    runs.reserve_run("rr1", "ag", "c", network_cidr="10.200.5.0/24", entry_cidrs="10.200.5.0/24")
    runs.delete_run("rr1")
    assert runs.active_cidrs() == set()
    assert runs.get("rr1") is None


def test_record_result_is_idempotent_on_run_id(migrated_home):
    """A second record_result for the same run_id is first-write-wins (no double-write).

    Async grading + the watchdog can both reach a terminal run; recording must not double-write.
    """
    from xorcise.core import reporting
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown

    g1 = GradeResult(
        run_id="rr", overall=1.0, breakdown=ScoreBreakdown(deterministic=1.0, judge=1.0)
    )
    reporting.record_result("rr", "ag", g1)
    g2 = GradeResult(
        run_id="rr", overall=0.0, breakdown=ScoreBreakdown(deterministic=0.0, judge=0.0)
    )
    reporting.record_result("rr", "ag", g2)  # must NOT insert a second row or overwrite
    assert len(reporting.agent_history("ag")) == 1
    got = reporting.get_result("rr")
    assert got is not None and got.overall == 1.0  # first write wins


def test_create_run_persists_conditions(migrated_home):
    """Model + sandbox_ref are persisted and surfaced on create + get."""
    entry = runs.create_run(
        agent_id="ag1",
        mission="c1",
        run_id="r1",
        budget_seconds=120,
        prompt="p",
        run_control_key="k",
        model="claude-opus-4-8",
        sandbox_ref="img@sha256:abc",
    )
    assert entry.model == "claude-opus-4-8"
    assert entry.sandbox_ref == "img@sha256:abc"
    got = runs.get("r1")
    assert got is not None
    assert got.model == "claude-opus-4-8"
    assert got.sandbox_ref == "img@sha256:abc"


def test_create_run_conditions_default_none(migrated_home):
    """Model + sandbox_ref default to None (back-compat)."""
    entry = runs.create_run(agent_id="ag2", mission="c2")
    assert entry.model is None
    assert entry.sandbox_ref is None


def test_create_run_tags_agent_and_lists(migrated_home):
    entry = runs.create_run(agent_id="a1", mission="c1")
    assert entry.run_id and entry.agent_id == "a1"
    assert entry.mission == "c1"
    assert entry.state == "created"
    assert entry.created_at is not None
    listed = runs.list_runs()
    assert [r.run_id for r in listed] == [entry.run_id]
    assert listed[0].agent_id == "a1"


def test_get_returns_run_or_none(migrated_home):
    assert runs.get("ghost") is None
    created = runs.create_run(agent_id="a1", mission="c1")
    found = runs.get(created.run_id)
    assert found is not None and found.agent_id == "a1"


def test_create_run_persists_versions(migrated_home):
    """agent_version + install_revision are persisted and surfaced on create + get."""
    entry = runs.create_run(
        agent_id="ag1",
        mission="c1",
        run_id="r1",
        budget_seconds=60,
        prompt="p",
        run_control_key="k",
        agent_version=2,
        install_revision=3,
    )
    assert entry.agent_version == 2 and entry.install_revision == 3
    got = runs.get("r1")
    assert got is not None
    assert got.agent_version == 2 and got.install_revision == 3


def test_create_run_versions_default_one(migrated_home):
    """agent_version + install_revision default to 1 (back-compat)."""
    entry = runs.create_run(agent_id="ag2", mission="c2")
    assert entry.agent_version == 1
    assert entry.install_revision == 1


def test_delete_for_agent_removes_only_that_agent(migrated_home):
    runs.create_run(agent_id="a1", mission="c1")
    runs.create_run(agent_id="a2", mission="c2")
    assert runs.delete_for_agent("a1") == 1
    remaining = runs.list_runs()
    assert all(r.agent_id == "a2" for r in remaining)
    assert len(remaining) == 1


def test_create_run_persists_join_key_and_narrow_getter_reads_it(migrated_home):
    # the per-run tailnet join key is persisted (for the /connect endpoint) but read only
    # via the narrow getter — it must never ride RunEntry (which "never carries the bearer").
    entry = runs.create_run(
        agent_id="a1", mission="c", run_control_key="rk", join_key="tskey-secret"
    )
    assert runs.get_join_key(entry.run_id) == "tskey-secret"
    # not leaked onto the general run surface
    assert not hasattr(runs.get(entry.run_id), "join_key")
    # absent run -> None
    assert runs.get_join_key("nope") is None
    # default is empty (back-compat with existing create_run callers)
    plain = runs.create_run(agent_id="a2", mission="c")
    assert runs.get_join_key(plain.run_id) == ""


def test_create_run_persists_source_agent(migrated_home):
    entry = runs.create_run(agent_id="a1", mission="c1", run_id="r1", source_agent="openhands")
    assert entry.source_agent == "openhands"
    got = runs.get("r1")
    assert got is not None and got.source_agent == "openhands"


def test_source_agent_defaults_generic(migrated_home):
    assert runs.create_run(agent_id="a2", mission="c2").source_agent == "generic"


def test_finalize_run_sets_source_agent(migrated_home):
    runs.reserve_run("rr", "ag", "c", network_cidr="10.200.9.0/24")
    run = runs.finalize_run("rr", prompt="p", source_agent="langgraph")
    assert run.source_agent == "langgraph"


def test_source_agent_snapshot_survives_agent_update_and_delete(migrated_home):
    # The AC: source_agent is frozen at create — NOT resolved through the registry — so a run
    # keeps its adapter after the agent is re-declared or removed (mirrors model/agent_version).
    from xorcise.core import agents

    agents.register(name="scout", kind="openhands")
    runs.create_run(agent_id="scout-id", mission="c1", run_id="r1", source_agent="openhands")
    agents.update_agent("scout", kind="claude-code")  # re-declare, bumps version
    after_update = runs.get("r1")
    assert after_update is not None
    assert after_update.source_agent == "openhands"  # unchanged
    agents.remove("scout")
    after_delete = runs.get("r1")
    assert after_delete is not None
    assert after_delete.source_agent == "openhands"  # survives delete (no FK/join)


def test_readiness_scan_skips_static_and_reservation_only_runs(migrated_home):
    # The readiness gate terminates runs whose ENVIRONMENT never came up. Two kinds of run have no
    # environment to wait for and must never be scanned:
    #   * static (attachment-only) runs — no subnet/fence/container by design (empty network_cidr).
    #     Scanning them finds no container, reads it as a dead environment, and would terminate a
    #     perfectly healthy run once its window expired.
    #   * reservation-only rows whose deploy never finished (the boot reconciler's business).
    runs.reserve_run("lab", "ag", "c", network_cidr="10.200.1.0/24", entry_cidrs="10.200.1.0/24")
    runs.finalize_run("lab", budget_seconds=300, prompt="P")
    runs.reserve_run("static", "ag", "c", network_cidr="", entry_cidrs="")
    runs.finalize_run("static", budget_seconds=300, prompt="P")
    runs.reserve_run(
        "reserved", "ag", "c", network_cidr="10.200.2.0/24", entry_cidrs="10.200.2.0/24"
    )

    scanned = [run_id for run_id, _ in runs.deployed_non_terminal_runs()]
    assert scanned == ["lab"]
