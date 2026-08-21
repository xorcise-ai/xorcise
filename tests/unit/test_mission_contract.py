"""mission.json v2 schema (leaf DTOs). No I/O; pure shape + validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.mission import (
    ArtifactSpec,
    Attachment,
    Check,
    EnvironmentSpec,
    Intel,
    MissionManifest,
    MissionMetadata,
    RubricCriterion,
    TerrainSpec,
)


def test_attachment_round_trips_with_optional_fields() -> None:
    a = Attachment(
        name="vuln.bin", path="files/vuln.bin", media_type="application/x-elf", sha256="9f86d0"
    )
    assert Attachment.model_validate(a.model_dump()) == a


def test_attachment_minimal_round_trips() -> None:
    a = Attachment(name="libc.so.6", path="files/libc.so.6")
    assert a.media_type is None and a.sha256 is None
    assert Attachment.model_validate(a.model_dump()) == a


@pytest.mark.parametrize("missing", ["name", "path"])
def test_attachment_missing_required_field_names_it(missing: str) -> None:
    fields = {"name": "x", "path": "files/x"}
    del fields[missing]
    with pytest.raises(ValidationError) as exc:
        Attachment(**fields)
    assert missing in str(exc.value)


def test_environment_spec_lifts_poc_fields() -> None:
    env = EnvironmentSpec(
        compose_file="docker-compose.yml",
        entry_networks=("dmz_net",),
        static_ips={"web": {"dmz_net": 10}},
    )
    assert EnvironmentSpec.model_validate(env.model_dump()) == env


def test_environment_spec_defaults() -> None:
    env = EnvironmentSpec()
    assert env.compose_file == "docker-compose.yml"
    assert env.entry_networks == () and env.static_ips == {}


def test_check_uses_source_ref_op_args() -> None:
    c = Check(
        id="flag-correct", source="artifacts", ref="flag", op="equals", args={"expected": "X"}
    )
    assert Check.model_validate(c.model_dump()) == c


def test_rubric_criterion_round_trips() -> None:
    r = RubricCriterion(id="mapped-subnet", text="Agent maps the subnet", weight=0.5)
    assert RubricCriterion.model_validate(r.model_dump()) == r
    assert RubricCriterion(id="x", text="y").weight is None


def test_artifact_spec_round_trips_and_defaults_required() -> None:
    a = ArtifactSpec(name="findings", description="per-host summary")
    assert ArtifactSpec.model_validate(a.model_dump()) == a
    assert a.required is True  # defaults to required
    assert ArtifactSpec(name="optional-notes", required=False).required is False


def test_intel_round_trips() -> None:
    h = Intel(id="i1", text="Try the web host first")
    assert Intel.model_validate(h.model_dump()) == h


def test_terrain_spec_is_minimal_placeholder() -> None:
    t = TerrainSpec(summary="DMZ + hidden internal", nodes=({"id": "web", "segment": "dmz"},))
    assert TerrainSpec.model_validate(t.model_dump()) == t
    assert TerrainSpec().summary is None and TerrainSpec().nodes == ()


def test_metadata_round_trips_and_requires_mission_id_name_objective() -> None:
    m = MissionMetadata(
        mission_id="basic-pivot",
        name="Basic Pivot",
        summary="multi-host range",
        objective="map it",
        type="lab",
    )
    assert MissionMetadata.model_validate(m.model_dump()) == m
    with pytest.raises(ValidationError) as exc:
        MissionMetadata.model_validate({"name": "no id", "objective": "x"})
    assert "mission_id" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        MissionMetadata.model_validate({"mission_id": "x", "name": "no objective"})
    assert "objective" in str(exc.value)


def test_metadata_new_facets_round_trip() -> None:
    m = MissionMetadata(
        mission_id="x",
        name="X",
        summary="human blurb",
        objective="agent mission",
        proficiency="intermediate",
        specialty="web",
        type="lab",
        skills=("sqli",),
        technologies=("http",),
    )
    assert MissionMetadata.model_validate(m.model_dump()) == m


def test_metadata_skills_technologies_default_empty() -> None:
    m = MissionMetadata(mission_id="sqli", name="SQLi", objective="o", type="lab")
    assert m.skills == () and m.technologies == ()
    assert m.proficiency is None and m.specialty is None and m.summary == ""


@pytest.mark.parametrize(
    "bad", [{"id": "x"}, {"difficulty": "easy"}, {"competencies": ()}, {"description": "d"}]
)
def test_metadata_rejects_removed_fields(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as exc:
        MissionMetadata.model_validate({"mission_id": "x", "name": "X", "objective": "o", **bad})
    assert next(iter(bad)) in str(exc.value)


def test_unknown_field_is_forbidden() -> None:
    with pytest.raises(ValidationError) as exc:
        Intel.model_validate({"id": "i1", "text": "x", "bogus": 1})
    assert "bogus" in str(exc.value)


def _complete_manifest_dict() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "metadata": {
            "mission_id": "basic-pivot",
            "name": "Basic Pivot",
            "summary": "range",
            "objective": "Map the subnet and fingerprint each host.",
            "type": "lab",
        },
        "environment": {"entry_networks": ["default"], "static_ips": {"web": {"default": 10}}},
        "artifacts": [{"name": "findings", "description": "per-host summary"}],
        "rubric": [{"id": "mapped", "text": "Maps all hosts", "weight": 1.0}],
        "checks": [
            {"id": "flag", "source": "artifacts", "ref": "flag", "op": "observed", "weight": 1.0}
        ],
        "intel": [{"id": "i1", "text": "start at web"}],
        "attachments": [{"name": "vuln.bin", "path": "files/vuln.bin"}],
        "terrain": {"summary": "single segment"},
        "source": None,
        "delivery": None,
    }


def test_complete_manifest_round_trips() -> None:
    m = MissionManifest.model_validate(_complete_manifest_dict())
    assert MissionManifest.model_validate(m.model_dump()) == m
    assert m.schema_version == "2.0"
    assert m.checks[0].source == "artifacts" and m.checks[0].op == "observed"


def test_minimal_manifest_validates_with_optional_blocks_absent() -> None:
    m = MissionManifest.model_validate(
        {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": "c1",
                "name": "C1",
                "objective": "do the thing",
                "type": "lab",
            },
            "environment": {},
        }
    )
    assert m.rubric == () and m.checks == ()
    assert m.intel == () and m.attachments == () and m.artifacts == ()
    assert m.terrain is None
    assert m.metadata.objective == "do the thing"


def test_manifest_rejects_top_level_objective_and_forbidden() -> None:
    base = {
        "schema_version": "2.0",
        "metadata": {"mission_id": "c1", "name": "C1", "objective": "o", "type": "lab"},
        "environment": {},
    }
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate({**base, "objective": "o"})
    assert "objective" in str(exc.value)
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate({**base, "forbidden": ["x"]})
    assert "forbidden" in str(exc.value)


@pytest.mark.parametrize("missing", ["schema_version", "metadata", "environment"])
def test_missing_required_field_names_it(missing: str) -> None:
    d = _complete_manifest_dict()
    del d[missing]
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(d)
    assert missing in str(exc.value)


@pytest.mark.parametrize("bad_version", ["1.0", "2", "3.0", "2.0.0"])
def test_unknown_schema_version_is_rejected(bad_version: str) -> None:
    d = _complete_manifest_dict()
    d["schema_version"] = bad_version
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(d)
    assert "schema_version" in str(exc.value)


def test_premium_fields_present_but_inert() -> None:
    d = _complete_manifest_dict()
    d["source"] = "premium-origin"
    d["delivery"] = "premium-channel"
    m = MissionManifest.model_validate(d)
    assert m.source == "premium-origin" and m.delivery == "premium-channel"
    assert MissionManifest.model_validate(m.model_dump()) == m


def test_manifest_rejects_unknown_top_level_field() -> None:
    d = _complete_manifest_dict()
    d["bogus_top"] = 1
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(d)
    assert "bogus_top" in str(exc.value)


def test_mission_networks_are_confined_unless_the_author_opts_out():
    """The one network switch a mission gets, and it is an escape hatch from a restriction.

    Without it a mission that legitimately needs external access is unbuildable, and someone
    works around the confinement instead. There is deliberately NO matching switch for whether
    the target can reach the agent: that direction is always open, because the agent is a host on
    the mission network and making it unreachable is the artificial state.
    """
    from xorcise.core.contracts.control import NetworkSpec
    from xorcise.core.contracts.mission import EnvironmentSpec

    assert EnvironmentSpec().allow_egress is False
    # `agent_ingress` survives on the manifest ONLY as a compat shim for already-published
    # catalog entries (see the regression test below). Nothing carries it inward: the runner
    # contract has no such field, so there is no switch left to flip.
    assert not hasattr(NetworkSpec(), "agent_ingress")
    assert "agent_ingress" not in NetworkSpec.model_fields


def test_a_published_manifest_carrying_agent_ingress_still_installs():
    """Regression: `EnvironmentSpec` is `extra="forbid"`, and the published catalog is versioned
    independently of this code. Deleting `agent_ingress` when reachability stopped being a
    per-mission switch turned every already-published manifest that sets it into a hard install
    failure — `xorcise run create --mission segmented-pivot` died with "Extra inputs are not
    permitted" on a client that had done nothing but upgrade. The field is accepted and ignored
    until the catalog stops emitting it."""
    from xorcise.core.contracts.mission import EnvironmentSpec

    env = EnvironmentSpec.model_validate(
        {"compose_file": "docker-compose.yml", "entry_networks": ["dmz_net"], "agent_ingress": True}
    )
    assert env.entry_networks == ("dmz_net",)


def test_an_unknown_environment_key_is_still_rejected():
    """Accepting one legacy field must not turn the whole model permissive — a typo'd key is
    still the author's bug, and silently ignoring it is how a mission ships misconfigured."""
    import pytest as _pytest
    from pydantic import ValidationError

    from xorcise.core.contracts.mission import EnvironmentSpec

    with _pytest.raises(ValidationError):
        EnvironmentSpec.model_validate({"allow_egres": True})
