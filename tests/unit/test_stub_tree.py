import importlib

import pytest

# Every package that MUST exist as an importable, side-effect-free stub.
PACKAGES = [
    "xorcise.core.contracts",
    "xorcise.core.config",
    "xorcise.core.observability",
    "xorcise.core.db",
    "xorcise.core.db.migrations",
    "xorcise.core.home",
    "xorcise.core.rest",
    "xorcise.core.rest.routers",
    "xorcise.core.frontend",
    "xorcise.core.runs",
    "xorcise.core.runcontrol",
    "xorcise.core.agents",
    "xorcise.core.targets",
    "xorcise.core.reporting",
    "xorcise.core.orchestration",
    "xorcise.core.orchestration.clients",
    "xorcise.core.eval",
    "xorcise.core.otel",
    "xorcise.core.otel.ingest",
    "xorcise.core.otel.store",
    "xorcise.core.runner",
    "xorcise.core.runner.docker",
    "xorcise.core.headscale",
    "xorcise.core.catalog",
    "xorcise.core.missions",
    "xorcise.core.code",
    "xorcise.core.premium",
    "xorcise.core.roles",
    "xorcise.core.roles.boot",
]

CONTRACT_MODULES = [
    "xorcise.core.contracts.rest",
    "xorcise.core.contracts.control",
    "xorcise.core.contracts.otlp",
    "xorcise.core.contracts.catalog_v1",
    "xorcise.core.contracts.mission",
    "xorcise.core.contracts.grading",
    "xorcise.core.contracts.telemetry",
    "xorcise.core.contracts.premium_v1",
    "xorcise.core.contracts.roles",
    "xorcise.core.contracts.errors",
    "xorcise.core.contracts.fs",
]


@pytest.mark.parametrize("name", PACKAGES + CONTRACT_MODULES)
def test_package_importable(name):
    mod = importlib.import_module(name)
    assert mod is not None


def test_seams_importable():
    importlib.import_module("xorcise.core.seams")
