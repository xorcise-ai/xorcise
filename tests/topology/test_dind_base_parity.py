"""The nested-Rosetta probe must measure the image XORCISE actually ships.

Nested Rosetta is not uniform across docker-in-docker releases: on this host an amd64 child runs
fine inside `docker:dind` 29.x but dies inside `docker:27-dind` with "rosetta error: failed to
open elf". So a probe pointed at a NEWER dind than the fused image's base reports a capability
the fused image does not have — a false green that would flip the macOS default onto a runtime
that cannot start amd64 mission services.

Nothing else couples these two files, so drift is silent and only shows up as a bad decision at
deploy time. Hence this parity check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from xorcise.core.runner.docker.build import BASE_VERSION, BASE_VERSION_LABEL
from xorcise.core.runner.docker.rosetta import NESTED_PROBE_IMAGE

pytestmark = pytest.mark.topology

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "containers" / "mission-base" / "Dockerfile"


def test_probe_image_matches_the_fused_image_base() -> None:
    froms = re.findall(r"^FROM\s+(\S+)", DOCKERFILE.read_text(), re.MULTILINE)
    assert froms, f"no FROM found in {DOCKERFILE}"
    assert froms[0] == NESTED_PROBE_IMAGE, (
        f"the nested-Rosetta probe measures {NESTED_PROBE_IMAGE!r} but the fused image is built "
        f"FROM {froms[0]!r} — the probe would report a capability the shipped image lacks. "
        "Update NESTED_PROBE_IMAGE in runner/docker/rosetta.py and RE-VERIFY nesting on the new "
        "base before trusting it."
    )


def test_base_version_label_matches_the_code_constant() -> None:
    """The base's declared generation (the label every fused image inherits and the client gates
    on) must equal build.BASE_VERSION. Drift means the code refuses artifacts the base actually
    produced, or vice versa — silent, since nothing else couples them."""
    labels = re.findall(
        rf'^LABEL\s+{re.escape(BASE_VERSION_LABEL)}="([^"]+)"', DOCKERFILE.read_text(), re.MULTILINE
    )
    assert labels, f"no `LABEL {BASE_VERSION_LABEL}=...` in {DOCKERFILE}"
    assert labels[0] == BASE_VERSION, (
        f"the base Dockerfile declares {BASE_VERSION_LABEL}={labels[0]!r} but build.BASE_VERSION "
        f"is {BASE_VERSION!r} — bump BOTH together on a breaking base change."
    )
