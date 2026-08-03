import pytest

from xorcise.core import runs

pytestmark = pytest.mark.unit


def test_authenticate_accepts_owning_key(migrated_home) -> None:
    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="secret-key")
    assert runs.authenticate(run.run_id, "secret-key") is True


def test_authenticate_rejects_wrong_key(migrated_home) -> None:
    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="secret-key")
    assert runs.authenticate(run.run_id, "WRONG") is False


def test_authenticate_rejects_absent_run(migrated_home) -> None:
    assert runs.authenticate("no-such-run", "anything") is False


def test_authenticate_rejects_empty_key(migrated_home) -> None:
    runs.create_run(agent_id="a1", mission="webby", run_id="r-empty", run_control_key="")
    # an unset key never authenticates (defends a run created before the column existed)
    assert runs.authenticate("r-empty", "") is False
