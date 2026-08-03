"""Strict check-op ingest validation (the "grading stuck forever" fix, Part A).

An unknown op or a wrong arg shape used to sail through preflight and crash grading at dispatch
(bare KeyError/TypeError), wedging the run at 202 "grading" forever. The gate now lives on the
contract (CheckOp Literal + arg-shape validator) so every ingest path — local preflight, REST
ingest_bundle, catalog install_pulled — rejects it; an ALREADY-installed record that predates the
tightening degrades to "not installed" instead of raising from every get_installed consumer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.mission import Check
from xorcise.core.missions.errors import PreflightError
from xorcise.core.missions.preflight import preflight
from xorcise.core.missions.runtime import INSTALLED_FILE, get_installed

pytestmark = pytest.mark.unit


# ═══ Check model: op vocabulary ═══


@pytest.mark.parametrize(
    "op,args",
    [
        ("equals", {"expected": "XORCISE{x}"}),
        ("matches_format", {"pattern": r"XORCISE\{.+\}"}),
        ("observed", {}),
        ("lesser_than", {"value": 25}),
    ],
)
def test_known_ops_with_right_args_validate(op, args):
    c = Check(id="c", source="artifacts", ref="flag", op=op, args=args)
    assert c.op == op


def test_unknown_op_rejected_at_contract():
    # model_validate (not the constructor) so the deliberately-invalid op typechecks
    with pytest.raises(ValidationError, match="op"):
        Check.model_validate(
            {"id": "c", "source": "artifacts", "ref": "flag", "op": "regex", "args": {}}
        )


# ═══ Check model: arg shape (the TypeError-at-grade-time twin) ═══


def test_missing_required_arg_rejected():
    with pytest.raises(ValidationError, match="check 'c' op 'equals': missing args: expected"):
        Check(id="c", source="artifacts", ref="flag", op="equals", args={})


def test_unexpected_arg_rejected():
    with pytest.raises(ValidationError, match="unexpected args: patern"):
        Check(id="c", source="artifacts", ref="flag", op="matches_format", args={"patern": "x"})


def test_observed_takes_no_args():
    with pytest.raises(ValidationError, match="check 'c' op 'observed': unexpected args"):
        Check(id="c", source="artifacts", ref="flag", op="observed", args={"expected": 1})


# ═══ Preflight: the ingest gate names the offending check ═══


def _bundle(tmp_path: Path, op: str, args: dict[str, object]) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = {
        "schema_version": "2.0",
        "metadata": {"mission_id": "c", "name": "c", "objective": "o", "type": "lab"},
        "environment": {},
        "checks": [{"id": "flag", "source": "artifacts", "ref": "flag", "op": op, "args": args}],
    }
    (bundle / "mission.json").write_text(json.dumps(manifest))
    (bundle / "docker-compose.yml").write_text("services: {}\n")
    return bundle


def test_preflight_rejects_unknown_op_naming_the_loc(tmp_path: Path):
    with pytest.raises(PreflightError, match=r"checks\.0\.op"):
        preflight(_bundle(tmp_path, "regex", {"pattern": "x"}))


def test_preflight_rejects_bad_arg_shape_with_the_reason(tmp_path: Path):
    # The arg-shape rule is a model validator on Check (loc "checks.0"); the message must keep
    # the reason, not just the loc.
    with pytest.raises(PreflightError, match=r"checks\.0.*missing args: expected"):
        preflight(_bundle(tmp_path, "equals", {}))


def test_preflight_accepts_a_valid_check(tmp_path: Path):
    m = preflight(_bundle(tmp_path, "matches_format", {"pattern": r"XORCISE\{.+\}"}))
    assert m.checks[0].op == "matches_format"


# ═══ get_installed: legacy record predating the tightening degrades, never raises ═══


def _legacy_record(tmp_path: Path, slug: str) -> Path:
    root = tmp_path / slug
    root.mkdir()
    record = {
        "version": 1,
        "origin": "your_own",
        "manifest": {
            "schema_version": "2.0",
            "metadata": {"mission_id": slug, "name": slug, "objective": "o", "type": "lab"},
            "environment": {},
            "checks": [
                # installed BEFORE the CheckOp Literal existed — invalid under today's contract
                {"id": "flag", "source": "artifacts", "ref": "flag", "op": "regex", "args": {}}
            ],
        },
        "mission_ref": {"mission_id": slug, "image": "img:0"},
    }
    (root / INSTALLED_FILE).write_text(json.dumps(record))
    return root


def test_get_installed_degrades_on_invalid_stored_manifest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    _legacy_record(tmp_path, "legacy")
    with caplog.at_level(logging.WARNING, logger="xorcise.core.missions.runtime"):
        assert get_installed("legacy", tmp_path) is None
    assert "legacy" in caplog.text and "re-ingest" in caplog.text
