import pytest

from xorcise.core.otel.store import SqliteSealStore

pytestmark = pytest.mark.adapters


def test_seal_persists_across_fresh_store(migrated_home) -> None:
    SqliteSealStore().seal("run-x")
    # a brand-new instance (simulating a process restart) still sees the seal
    assert SqliteSealStore().is_sealed("run-x") is True
    assert SqliteSealStore().sealed_at("run-x") is not None


def test_unsealed_run_not_sealed(migrated_home) -> None:
    assert SqliteSealStore().is_sealed("nope") is False
