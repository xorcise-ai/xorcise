from __future__ import annotations

import importlib

import pytest

from xorcise.core.config import (
    HEADSCALE_PORT,
    OTLP_PORT,
    REST_PORT,
    RUNNER_PORT,
)
from xorcise.core.roles.boot import AppSpec

# Expected default ports derive from config — the single source of truth.
# With no XORCISE_* override, each role's apps() must bind exactly its config defaults.
CASES = {
    "role_all": sorted([REST_PORT, OTLP_PORT]),
    "role_control": [REST_PORT],
    "role_collector": [OTLP_PORT],
    "role_runner": [RUNNER_PORT],
    "role_headscale": [HEADSCALE_PORT],
}


@pytest.mark.parametrize("module_name, expected_ports", CASES.items())
def test_boot_apps_ports(module_name: str, expected_ports: list[int]) -> None:
    mod = importlib.import_module(f"xorcise.core.roles.boot.{module_name}")
    specs = mod.apps()
    assert all(isinstance(s, AppSpec) for s in specs)
    assert sorted(s.port for s in specs) == expected_ports
