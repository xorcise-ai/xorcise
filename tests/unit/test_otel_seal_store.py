import pytest

from xorcise.core.otel.store import InMemorySealStore

pytestmark = pytest.mark.unit


def test_seal_marks_run_sealed() -> None:
    store = InMemorySealStore()
    assert store.is_sealed("r") is False
    store.seal("r")
    assert store.is_sealed("r") is True
    assert store.sealed_at("r") is not None


def test_seal_is_idempotent_first_timestamp_wins() -> None:
    store = InMemorySealStore()
    store.seal("r")
    first = store.sealed_at("r")
    store.seal("r")
    assert store.sealed_at("r") == first


def test_unsealed_run_has_no_timestamp() -> None:
    assert InMemorySealStore().sealed_at("ghost") is None
