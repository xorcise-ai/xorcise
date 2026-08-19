# tests/unit/test_manifest_schema_30.py
"""Schema 3.0 adoption — the mission-versioning contract's manifest half (MV1/MV7/A8/A11).

3.0 adds the required creator-owned SemVer `version`; 2.0 predates the field, must not carry
it, and must KEEP validating (prod is pre-contract and 2.0 history is served forever)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions.errors import PreflightError
from xorcise.core.missions.preflight import preflight

pytestmark = pytest.mark.unit


def _doc(**overrides: object) -> dict[str, object]:
    doc: dict[str, object] = {
        "schema_version": "3.0",
        "version": "1.4.2",
        "metadata": {
            "mission_id": "vanishing-point",
            "name": "Vanishing Point",
            "objective": "Find the flag.",
            "type": "lab",
        },
        "environment": {},
    }
    doc.update(overrides)
    return doc


def test_30_manifest_validates_and_carries_the_version() -> None:
    m = MissionManifest.model_validate(_doc())
    assert m.schema_version == "3.0"
    assert m.version == "1.4.2"


def test_30_requires_version() -> None:
    doc = _doc()
    del doc["version"]
    with pytest.raises(ValidationError, match="schema 3.0 requires 'version'"):
        MissionManifest.model_validate(doc)


def test_20_still_validates_without_version() -> None:
    # Prod is pre-contract: 2.0 documents (no `version`) must keep reading unchanged.
    doc = _doc(schema_version="2.0")
    del doc["version"]
    m = MissionManifest.model_validate(doc)
    assert m.schema_version == "2.0"
    assert m.version is None


def test_20_refuses_version() -> None:
    # A 2.0 document carrying `version` never existed in the wild; every other 2.0 consumer
    # (extra="forbid") refuses it, so this client must too.
    with pytest.raises(ValidationError, match="requires manifest schema 3.0"):
        MissionManifest.model_validate(_doc(schema_version="2.0"))


@pytest.mark.parametrize(
    "bad", ["01.0.0", "1.0", "1", "1.0.0-rc1", "1.0.0+build.5", "v1.0.0", "1.0.00", ""]
)
def test_version_refuses_non_semver(bad: str) -> None:
    # The A11 pattern verbatim: no leading zeros, no pre-release/build suffix — what ingest
    # enforces, so a bundle that passes here can never be refused remotely on format.
    with pytest.raises(ValidationError, match="MAJOR.MINOR.PATCH"):
        MissionManifest.model_validate(_doc(version=bad))


@pytest.mark.parametrize("good", ["0.0.0", "0.1.0", "1.4.2", "1.4.10", "10.20.30"])
def test_version_accepts_semver(good: str) -> None:
    assert MissionManifest.model_validate(_doc(version=good)).version == good


def test_live_30_shape_validates() -> None:
    # The exact key set the contract-era catalog serves for a published mission (the 2.0 keys
    # plus `version`) — pins that the client reads the full wire shape, not a lucky subset.
    doc: dict[str, object] = {
        "schema_version": "3.0",
        "metadata": {
            "mission_id": "sqli-login",
            "name": "SQLi Login",
            "summary": "A login page.",
            "objective": "Bypass the login.",
            "proficiency": "Advance Beginner",
            "specialty": "web",
            "type": "lab",
            "skills": ["Web Exploitation"],
            "technologies": ["http", "postgres"],
        },
        "environment": {
            "compose_file": "docker-compose.yml",
            "entry_networks": ["default"],
            "static_ips": {},
        },
        "artifacts": [],
        "rubric": [],
        "checks": [],
        "intel": [],
        "attachments": [],
        "terrain": None,
        "source": None,
        "delivery": None,
        "version": "1.0.0",
    }
    m = MissionManifest.model_validate(doc)
    assert m.version == "1.0.0"
    assert m.metadata.mission_id == "sqli-login"


# ── preflight (local `xorcise mission ingest`) ───────────────────────────────────────────────


def _write_bundle(tmp_path: Path, doc: dict[str, object]) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "mission.json").write_text(json.dumps(doc))
    (bundle / "docker-compose.yml").write_text("services: {}\n")
    return bundle


def test_preflight_accepts_a_30_bundle(tmp_path: Path) -> None:
    m = preflight(_write_bundle(tmp_path, _doc()))
    assert m.schema_version == "3.0"
    assert m.version == "1.4.2"


def test_preflight_names_both_supported_versions(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match=r"supported: 2\.0, 3\.0"):
        preflight(_write_bundle(tmp_path, _doc(schema_version="1.0")))
