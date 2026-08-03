from __future__ import annotations

from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    Check,
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    RubricCriterion,
)
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission


@pytest.mark.unit
def test_installed_exposes_rubric_and_checks(tmp_path: Path):
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c", name="c", objective="o", type="lab"),
        environment=EnvironmentSpec(),
        rubric=(RubricCriterion(id="r1", text="t", weight=1.0),),
        checks=(Check(id="flag", source="artifacts", ref="flag", op="observed"),),
    )
    root = tmp_path / "c"
    root.mkdir()
    ref = MissionRef(mission_id="c", image="img:0")
    (root / INSTALLED_FILE).write_text(InstalledMission("c", root, manifest, ref).to_record())

    inst = InstalledMission.from_root(root)
    assert [r.id for r in inst.rubric] == ["r1"]
    assert [c.id for c in inst.checks] == ["flag"]
