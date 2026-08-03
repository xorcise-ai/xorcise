import pytest

from xorcise.core.config import Settings
from xorcise.core.otel.mirror import MirrorConfigError, resolve_mirror

pytestmark = pytest.mark.unit


def _settings(**kw) -> Settings:
    # _env_file=None keeps the unit hermetic (ignore ~/.xorcise/.env + config.toml)
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_mirror_is_off_by_default() -> None:
    s = _settings()
    assert s.otel_mirror_enabled is False
    assert resolve_mirror(s) is None  # type: ignore[func-returns-value]  # off -> no-op, no error, no exporter


def test_enabling_without_endpoint_raises_clear_error() -> None:
    with pytest.raises(MirrorConfigError) as exc:
        resolve_mirror(_settings(otel_mirror_enabled=True))
    assert "reserved" in str(exc.value).lower()


def test_enabling_with_endpoint_still_raises_not_implemented() -> None:
    with pytest.raises(MirrorConfigError):
        resolve_mirror(
            _settings(otel_mirror_enabled=True, otel_mirror_endpoint="https://otel.cust:4318")
        )
