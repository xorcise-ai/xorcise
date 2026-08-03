"""_split_ref: docker-py pulls ALL tags unless the ref is split into (repo, tag)."""

from __future__ import annotations

import pytest

from xorcise.core.runner.docker.driver import _split_ref

pytestmark = pytest.mark.unit


def test_split_ecr_ref_with_tag() -> None:
    repo, tag = _split_ref("registry.example.com/xorcise/mission-sqli-login:100bdaf-base1")
    assert repo == "registry.example.com/xorcise/mission-sqli-login"
    assert tag == "100bdaf-base1"


def test_split_ref_without_tag() -> None:
    repo, tag = _split_ref("xorcise/mission-sqli-login")
    assert repo == "xorcise/mission-sqli-login"
    assert tag is None


def test_split_ref_registry_port_is_not_a_tag() -> None:
    # A ':' in the registry host (a port) precedes the last '/', so it must NOT be read as a tag.
    repo, tag = _split_ref("localhost:5000/xorcise/mission-x")
    assert repo == "localhost:5000/xorcise/mission-x"
    assert tag is None
