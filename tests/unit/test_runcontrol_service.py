import pytest

from xorcise.core.contracts.mission import (
    Attachment,
    EnvironmentSpec,
    Intel,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.runcontrol.errors import (
    MissionOverError,
    MissionUnavailableError,
    UnknownAttachmentError,
)
from xorcise.core.runcontrol.service import RunControlDeps, RunControlService
from xorcise.core.runcontrol.signing import verify
from xorcise.core.runcontrol.store import InMemorySubmissionStore

pytestmark = pytest.mark.unit


def _manifest() -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id="webby", name="Webby", objective="pop the box", type="lab"
        ),
        environment=EnvironmentSpec(),
        intel=(Intel(id="i1", text="check headers"), Intel(id="i2", text="try IDOR")),
        attachments=(Attachment(name="dump.pcap", path="files/dump.pcap", media_type="x/pcap"),),
    )


def _svc(
    store: InMemorySubmissionStore, *, gate=lambda r: None, intel_policy: str = "all"
) -> RunControlService:
    deps = RunControlDeps(
        store=store,
        manifest_for=lambda r: _manifest(),
        slug_for=lambda r: "webby",
        signing_secret="secret",
        attachment_ttl=300,
        gate=gate,
        now=lambda: 1000,
        intel_policy_for=lambda r: intel_policy,
    )
    return RunControlService(deps)


def test_get_mission_returns_objective_and_attachment_names() -> None:
    info = _svc(InMemorySubmissionStore()).get_mission("r1")
    assert info.objective == "pop the box"
    assert info.attachments == ("dump.pcap",)


def test_build_runcontrol_deps_targets_for_reads_only_target_facts(migrated_home) -> None:
    # the wired targets_for reads the run's persisted facts and returns only kind="target"
    # as name->IP (the filter must match run_create's writer, tying producer→/mission reader).
    from xorcise.core.config import get_settings
    from xorcise.core.contracts.telemetry import ObservedFact
    from xorcise.core.rest.routers.runcontrol import build_runcontrol_deps
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    store = SqliteObservedFactsStore()
    store.record(ObservedFact(run_id="r9", kind="target", name="web", value="10.200.3.10"))
    store.record(ObservedFact(run_id="r9", kind="acl-config", name="entry-cidrs", value="x"))
    deps = build_runcontrol_deps(get_settings())
    assert deps.targets_for("r9") == {"web": "10.200.3.10"}  # only the target fact, name->IP


def test_get_mission_resolves_target_ip_placeholder() -> None:
    # the /mission brief substitutes <name-target-ip-> with the run's routed IP, so it
    # never leaks the unresolvable placeholder (mirrors the connect prompt).
    def _man() -> MissionManifest:
        return MissionManifest(
            schema_version="2.0",
            metadata=MissionMetadata(
                mission_id="webby",
                name="Webby",
                objective="hit http://<web-target-ip->:80",
                type="lab",
            ),
            environment=EnvironmentSpec(),
        )

    deps = RunControlDeps(
        store=InMemorySubmissionStore(),
        manifest_for=lambda r: _man(),
        slug_for=lambda r: "webby",
        signing_secret="secret",
        now=lambda: 1000,
        targets_for=lambda r: {"web": "10.200.7.10"},
    )
    info = RunControlService(deps).get_mission("r1")
    assert info.objective == "hit http://10.200.7.10:80"
    assert "<web-target-ip->" not in info.objective


def test_submit_artifact_records() -> None:
    store = InMemorySubmissionStore()
    resp = _svc(store).submit_artifact("r1", "exploit.py", "code")
    assert resp.accepted is True and resp.name == "exploit.py"
    assert store.count("r1", "artifact") == 1


def test_get_intel_walks_intel_then_exhausts() -> None:
    store = InMemorySubmissionStore()
    svc = _svc(store)
    first = svc.get_intel("r1")
    assert first.intel == "check headers" and first.remaining == 1
    second = svc.get_intel("r1")
    assert second.intel == "try IDOR" and second.remaining == 0
    third = svc.get_intel("r1")
    assert third.intel is None and third.remaining == 0
    assert store.count("r1", "intel") == 2  # exhausted call is not recorded as a grant


def test_get_intel_policy_none_discloses_nothing() -> None:
    store = InMemorySubmissionStore()
    resp = _svc(store, intel_policy="none").get_intel("r1")
    assert resp.intel is None and resp.remaining == 0
    assert store.count("r1", "intel") == 0  # nothing recorded → nothing disclosed


def test_get_intel_policy_subset_discloses_only_those_in_authored_order() -> None:
    # Policy names h2 first, but disclosure follows AUTHORED order (h1 is not allowed, so h2 leads).
    store = InMemorySubmissionStore()
    svc = _svc(store, intel_policy="i2")
    first = svc.get_intel("r1")
    assert first.intel == "try IDOR" and first.remaining == 0  # only one allowed intel
    second = svc.get_intel("r1")
    assert second.intel is None and second.remaining == 0
    assert store.count("r1", "intel") == 1


def test_get_intel_policy_all_walks_every_authored_intel() -> None:
    store = InMemorySubmissionStore()
    svc = _svc(store, intel_policy="all")
    assert svc.get_intel("r1").intel == "check headers"
    assert svc.get_intel("r1").intel == "try IDOR"
    assert svc.get_intel("r1").intel is None
    assert store.count("r1", "intel") == 2


def test_get_intel_default_dep_is_all() -> None:
    # A service built without an injected intel_policy_for defaults to disclosing all intel.
    deps = RunControlDeps(
        store=InMemorySubmissionStore(),
        manifest_for=lambda r: _manifest(),
        slug_for=lambda r: "webby",
        signing_secret="secret",
        now=lambda: 1000,
    )
    assert RunControlService(deps).get_intel("r1").intel == "check headers"


def test_mark_done_records_completion() -> None:
    store = InMemorySubmissionStore()
    assert _svc(store).mark_done("r1").state == "terminal"
    assert store.count("r1", "complete") == 1


def test_get_attachment_mints_verifiable_signed_url() -> None:
    resp = _svc(InMemorySubmissionStore()).get_attachment("r1", "dump.pcap")
    assert resp.name == "dump.pcap" and resp.media_type == "x/pcap"
    # exp = now + ttl = 1300; signature verifies under the same secret
    assert "exp=1300" in resp.url
    sig = resp.url.split("sig=")[1]
    assert verify("secret", "r1", "dump.pcap", 1300, sig) is True


def test_get_attachment_unknown_name_is_rejected() -> None:
    with pytest.raises(UnknownAttachmentError):
        _svc(InMemorySubmissionStore()).get_attachment("r1", "nope.bin")


def test_gate_blocks_operations_when_terminal() -> None:
    # wires a real gate; here we prove the seam is consulted.
    def closed(_run_id: str) -> None:
        raise MissionOverError("mission-over")

    with pytest.raises(MissionOverError):
        _svc(InMemorySubmissionStore(), gate=closed).submit_artifact("r1", "flag", "late")


def test_get_mission_raises_when_manifest_unavailable() -> None:
    # Manifest unavailable raises MissionUnavailableError (not UnknownAttachmentError).
    deps = RunControlDeps(
        store=InMemorySubmissionStore(),
        manifest_for=lambda r: None,
        slug_for=lambda r: "unknown",
        signing_secret="secret",
        attachment_ttl=300,
        now=lambda: 1000,
    )
    svc = RunControlService(deps)
    with pytest.raises(MissionUnavailableError):
        svc.get_mission("r1")
