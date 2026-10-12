"""Mission image lifecycle: which local images XORCISE owns, and which are safe to remove."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests._helpers import install_mission
from xorcise.core.contracts.errors import ContractError
from xorcise.core.rest.mission_images import (
    Candidate,
    Failure,
    ImageOutcome,
    Reason,
    apply_prune,
    owned_slug,
    plan_prune,
    prune,
    purge_image,
)
from xorcise.core.runner.docker import ImageRecord, StubDockerDriver

pytestmark = pytest.mark.unit

_BASE2 = {"ai.xorcise.base.version": "2", "org.opencontainers.image.title": "XORCISE mission-base"}
_BASE1 = {"org.opencontainers.image.title": "xorcise-mission-base"}  # old cloud generation
_ECR = "123456789012.dkr.ecr.us-east-1.amazonaws.com"


def _img(
    image_id: str, *tags: str, labels: dict[str, str] | None = None, size: int = 100
) -> ImageRecord:
    return ImageRecord(
        id=image_id, tags=tags, labels=_BASE2 if labels is None else labels, size_bytes=size
    )


# owned_slug ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tags", "labels", "slug"),
    [
        (("xorcise/mission-sqli-login:local",), _BASE2, "sqli-login"),
        (("xorcise/mission-sqli-login:local",), _BASE1, "sqli-login"),
        (("xorcise/mission-sqli-login:local",), {"ai.xorcise.base.version": "1"}, "sqli-login"),
        ((f"{_ECR}/xorcise/mis-vault:1.5.0-base2",), _BASE2, "vault"),
        ((f"{_ECR}/xorcise/test/mis-foo:abc-base1",), _BASE1, "foo"),
        (("xorcise/mis-vault:1", "xorcise/mis-vault:2"), _BASE2, "vault"),
        (("localhost:5000/xorcise/mis-vault:1",), _BASE2, "vault"),
    ],
)
def test_owned_slug_recognises_mission_images(
    tags: tuple[str, ...], labels: dict[str, str], slug: str
) -> None:
    assert owned_slug(_img("sha256:a", *tags, labels=labels)) == slug


@pytest.mark.parametrize(
    ("tags", "labels"),
    [
        (("xorcise/mission-base:latest",), _BASE2),
        (("ghcr.io/xorcise-ai/mission-base:2.0.0",), _BASE2),
        ((f"{_ECR}/xorcise/mission-base:2",), _BASE1),
        (("xorcise/mission-sqli:local",), {"org.opencontainers.image.title": "postgres"}),
        (("xorcise/mission-sqli:local", "myorg/keep:1"), _BASE2),
        (("xorcise/mission-sqli:local", "xorcise/mission-base:latest"), _BASE2),
        (("xorcise/mission-sqli:local", "xorcise/mission-other:local"), _BASE2),
        ((), _BASE2),
        (("postgres:16",), {}),
        (("xorcise/mission-sqli:local",), {}),
        (("someuser/xorcise/mis-vault:1",), _BASE2),
    ],
)
def test_owned_slug_refuses_everything_else(tags: tuple[str, ...], labels: dict[str, str]) -> None:
    assert owned_slug(_img("sha256:a", *tags, labels=labels)) is None


# plan_prune -------------------------------------------------------------------------------


def test_plan_names_superseded_and_uninstalled_images_and_skips_protected_ones() -> None:
    images = [
        _img("sha256:cur", "xorcise/mis-vault:2", size=300),
        _img("sha256:old", "xorcise/mis-vault:1", size=200),
        _img("sha256:gone", "xorcise/mission-sqli:local", size=50),
        _img("sha256:pg", "postgres:16", labels={}),
    ]
    assert plan_prune(images, protected_ids={"sha256:cur"}, installed_slugs={"vault"}) == [
        Candidate("sha256:old", ("xorcise/mis-vault:1",), "vault", 200, Reason.SUPERSEDED),
        Candidate("sha256:gone", ("xorcise/mission-sqli:local",), "sqli", 50, Reason.NOT_INSTALLED),
    ]


# protection, gathered from the real install store, run table and pull jobs ------------------


def _driver(*images: ImageRecord) -> StubDockerDriver:
    d = StubDockerDriver()
    d.images = {i.id: i for i in images}
    return d


def _candidate_ids(driver: StubDockerDriver, root: Path) -> list[str]:
    return [c.image_id for c in prune(driver, root, apply=False).candidates]


def test_installed_image_is_protected_with_every_tag_it_carries(migrated_home: Path) -> None:
    install_mission(migrated_home, slug="vault")  # records xorcise/mission-vault:0
    driver = _driver(
        _img("sha256:inst", "xorcise/mission-vault:0", "xorcise/mission-vault:old"),
        _img("sha256:prev", "xorcise/mission-vault:prev"),
    )
    assert _candidate_ids(driver, migrated_home / "missions") == ["sha256:prev"]


def test_digest_pinned_install_protects_its_image(migrated_home: Path) -> None:
    root = migrated_home / "missions"
    install_mission(migrated_home, slug="vault")
    record = root / "vault" / "installed.json"
    record.write_text(
        record.read_text().replace("xorcise/mission-vault:0", "reg.io/xorcise/mis-vault@sha256:d")
    )
    driver = _driver(
        ImageRecord(
            id="sha256:inst",
            tags=("reg.io/xorcise/mis-vault:1",),
            labels=_BASE2,
            size_bytes=1,
            digests=("reg.io/xorcise/mis-vault@sha256:d",),
        )
    )
    assert _candidate_ids(driver, root) == []


def test_a_docker_hub_spelled_ref_still_protects_its_image(migrated_home: Path) -> None:
    from xorcise.core.runs import create_run

    root = migrated_home / "missions"
    install_mission(migrated_home, slug="vault")
    record = root / "vault" / "installed.json"
    record.write_text(
        record.read_text().replace("xorcise/mission-vault:0", "docker.io/xorcise/mission-vault:0")
    )
    create_run("agent", "live", sandbox_ref="index.docker.io/xorcise/mission-live:local")
    driver = _driver(
        _img("sha256:inst", "xorcise/mission-vault:0"),
        _img("sha256:live", "xorcise/mission-live:local"),
        _img("sha256:gone", "xorcise/mission-gone:local"),
    )
    assert _candidate_ids(driver, root) == ["sha256:gone"]


def test_an_install_whose_image_cannot_be_found_protects_its_whole_mission(
    migrated_home: Path,
) -> None:
    """Fail closed: a ref spelled in a way the image list does not show could still name one
    of these images, so every image of that mission stays."""
    root = migrated_home / "missions"
    install_mission(migrated_home, slug="vault")
    record = root / "vault" / "installed.json"
    record.write_text(record.read_text().replace("xorcise/mission-vault:0", "odd/vault-ref:0"))
    driver = _driver(
        _img("sha256:a", "xorcise/mission-vault:old"),
        _img("sha256:gone", "xorcise/mission-gone:local"),
    )
    assert _candidate_ids(driver, root) == ["sha256:gone"]


def test_a_live_run_protects_its_image_and_a_finished_one_does_not(migrated_home: Path) -> None:
    from xorcise.core.runs import create_run, mark_terminal

    create_run("agent", "live", sandbox_ref="xorcise/mission-live:local")
    done = create_run("agent", "done", sandbox_ref="xorcise/mission-done:local")
    mark_terminal(done.run_id, "done", datetime.now(UTC))
    driver = _driver(
        _img("sha256:live", "xorcise/mission-live:local"),
        _img("sha256:done", "xorcise/mission-done:local"),
    )
    assert _candidate_ids(driver, migrated_home / "missions") == ["sha256:done"]


def test_container_and_active_pull_protect_their_images(migrated_home: Path) -> None:
    from xorcise.core.rest.pull_jobs import pull_job_store

    pull_job_store().start("pulling")
    driver = _driver(
        _img("sha256:box", "xorcise/mission-box:local"),
        _img("sha256:pull", "xorcise/mis-pulling:1"),
        _img("sha256:free", "xorcise/mission-free:local"),
    )
    driver.container_images.add("sha256:box")
    assert _candidate_ids(driver, migrated_home / "missions") == ["sha256:free"]


def test_static_install_with_no_image_protects_nothing(migrated_home: Path) -> None:
    root = migrated_home / "missions"
    install_mission(migrated_home, slug="notes")
    record = root / "notes" / "installed.json"
    record.write_text(record.read_text().replace("xorcise/mission-notes:0", ""))
    driver = _driver(_img("sha256:notes", "xorcise/mission-notes:old"))
    plan = prune(driver, root, apply=False)
    assert [(c.image_id, c.reason) for c in plan.candidates] == [
        ("sha256:notes", Reason.SUPERSEDED)
    ]


@pytest.mark.parametrize("record", ["{not json", '{"mission_ref": {}}', '{"origin": "library"}'])
def test_an_unreadable_install_record_aborts_the_prune(migrated_home: Path, record: str) -> None:
    root = migrated_home / "missions"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "installed.json").write_text(record)
    driver = _driver(_img("sha256:x", "xorcise/mission-x:local"))
    with pytest.raises(ContractError, match="broken"):
        prune(driver, root, apply=True)
    assert driver.removed == []


# apply ------------------------------------------------------------------------------------


def test_apply_removes_every_tag_and_records_in_use_failures() -> None:
    driver = _driver(
        _img("sha256:a", "xorcise/mis-a:1", "xorcise/mis-a:2"),
        _img("sha256:busy", "xorcise/mis-busy:1"),
        _img("sha256:c", "xorcise/mis-c:1"),
    )
    driver.container_images.add("sha256:busy")
    candidates = [
        Candidate("sha256:a", ("xorcise/mis-a:1", "xorcise/mis-a:2"), "a", 1, Reason.NOT_INSTALLED),
        Candidate("sha256:busy", ("xorcise/mis-busy:1",), "busy", 1, Reason.NOT_INSTALLED),
        Candidate("sha256:c", ("xorcise/mis-c:1",), "c", 1, Reason.NOT_INSTALLED),
    ]
    result = apply_prune(driver, candidates)
    assert result.removed == ("sha256:a", "sha256:c")
    assert result.failed == (Failure("sha256:busy", ("xorcise/mis-busy:1",), "in use"),)
    assert [i.id for i in driver.list_images()] == ["sha256:busy"]


def test_dry_run_reports_without_removing(migrated_home: Path) -> None:
    driver = _driver(_img("sha256:a", "xorcise/mis-a:1", size=7))
    plan = prune(driver, migrated_home / "missions", apply=False)
    assert (plan.applied, plan.reclaimable_bytes, plan.removed) == (False, 7, ())
    assert driver.removed == []


def test_applied_prune_removes_candidates(migrated_home: Path) -> None:
    driver = _driver(_img("sha256:a", "xorcise/mis-a:1"), _img("sha256:b", "xorcise/mis-b:1"))
    plan = prune(driver, migrated_home / "missions", apply=True)
    assert (plan.applied, plan.removed) == (True, ("sha256:a", "sha256:b"))
    assert driver.list_images() == []


# purge (delete --purge) -------------------------------------------------------------------


def test_purge_removes_an_unprotected_mission_image(migrated_home: Path) -> None:
    driver = _driver(_img("sha256:a", "xorcise/mission-a:0", "xorcise/mission-a:old"))
    purge = purge_image(
        "xorcise/mission-a:0", driver=driver, install_root=migrated_home / "missions"
    )
    assert purge.outcome is ImageOutcome.REMOVED
    assert driver.list_images() == []


@pytest.mark.parametrize(
    ("ref", "outcome"),
    [
        ("", ImageOutcome.NONE),
        ("xorcise/mission-absent:0", ImageOutcome.NONE),
        ("myorg/custom:1", ImageOutcome.KEPT),
    ],
)
def test_purge_leaves_what_it_cannot_or_must_not_remove(
    migrated_home: Path, ref: str, outcome: ImageOutcome
) -> None:
    driver = _driver(_img("sha256:c", "myorg/custom:1"))
    assert (
        purge_image(ref, driver=driver, install_root=migrated_home / "missions").outcome is outcome
    )
    assert [i.id for i in driver.list_images()] == ["sha256:c"]


def test_purge_keeps_an_image_another_install_or_a_live_run_uses(migrated_home: Path) -> None:
    from xorcise.core.runs import create_run

    install_mission(migrated_home, slug="twin")  # records xorcise/mission-twin:0
    create_run("agent", "live", sandbox_ref="xorcise/mission-live:local")
    driver = _driver(
        _img("sha256:twin", "xorcise/mission-twin:0"),
        _img("sha256:live", "xorcise/mission-live:local"),
    )
    root = migrated_home / "missions"
    twin = purge_image("xorcise/mission-twin:0", driver=driver, install_root=root)
    live = purge_image("xorcise/mission-live:local", driver=driver, install_root=root)
    assert (twin.outcome, twin.detail) == (
        ImageOutcome.KEPT,
        "kept xorcise/mission-twin:0: used by an installed mission",
    )
    assert (live.outcome, live.detail) == (
        ImageOutcome.KEPT,
        "kept xorcise/mission-live:local: used by an active run",
    )
    assert len(driver.list_images()) == 2


def test_purge_reports_a_failed_removal(migrated_home: Path) -> None:
    driver = _driver(_img("sha256:a", "xorcise/mission-a:0"))

    def refuse(ref: str) -> None:
        raise RuntimeError("daemon said no")

    driver.remove_image = refuse  # type: ignore[method-assign]
    purge = purge_image(
        "xorcise/mission-a:0", driver=driver, install_root=migrated_home / "missions"
    )
    assert (purge.outcome, purge.detail) == (ImageOutcome.FAILED, "daemon said no")
