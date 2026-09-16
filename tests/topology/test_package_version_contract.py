"""The package version must come from PACKAGE release tags, and from nothing else.

`[tool.hatch.version] source = "vcs"` derives the version by shelling out to `git describe`.
Left unconfigured, setuptools_scm describes against *every* tag in the repository — and this
repository publishes two unrelated tag series from the same history:

* `v*`                — the package releases, which ARE the version, and
* `mission-base-v*`   — container releases of the mission base image, which are not.

Nothing separates them at the git level, so whichever was tagged most recently wins. When that
is a container tag, an untagged build reports the *image's* generation as the package version
(`2.0.1.dev14+g9d5384e57` while PyPI was at `0.1.2`).

Tagged releases stay correct either way — a tag at HEAD wins on distance — which is exactly why
this needs a test: the failure is invisible in the one situation anybody checks. It is the
untagged builds that are wrong, and one of them matters more than it looks. `_update.py` compares
the installed version against the latest on PyPI, so a source install reading `2.0.1.dev14`
compares greater than every real release and is told it is up to date forever.

These tests describe a synthetic repository carrying both tag series rather than asserting on the
flag string, because the contract is the resolution behaviour, not the spelling of `--match`.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.topology

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())

# What setuptools_scm runs when `git_describe_command` is left unset. Hard-coded rather than
# imported: setuptools_scm is a BUILD dependency (via hatch-vcs) and is absent from the test
# environment. If a future setuptools_scm changes its default, these tests still describe what we
# require — an unconfigured build would simply start failing them, which is the point.
SETUPTOOLS_SCM_DEFAULT = ["git", "describe", "--dirty", "--tags", "--long", "--match", "*[0-9]*"]

RELEASE_TAG = "v0.1.0"
CONTAINER_TAG = "mission-base-v2.0.0"  # matches TAG_PREFIX in test_mission_base_release_contract


def _configured_describe_command() -> list[str]:
    """The command hatch-vcs will actually run, or setuptools_scm's default when unconfigured."""
    raw_options = PYPROJECT["tool"]["hatch"]["version"].get("raw-options", {})
    return list(raw_options.get("git_describe_command", SETUPTOOLS_SCM_DEFAULT))


def _git(repo: Path, *args: str) -> str:
    """Run git in `repo` with an identity of its own, so a developer's config cannot affect it."""
    out = subprocess.run(
        ["git", "-c", "user.email=t@t.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    (repo / "f").write_text(message)
    _git(repo, "add", "f")
    _git(repo, "commit", "-m", message)


@pytest.fixture
def repo_with_both_tag_series(tmp_path: Path) -> Path:
    """A history where the CONTAINER tag is the most recent — the case that goes wrong.

    The package release is two commits back, so a describe that honours both series resolves the
    container tag and a describe restricted to `v*` resolves the release.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "the release commit")
    _git(repo, "tag", RELEASE_TAG)
    _commit(repo, "a base image rebuild")
    _git(repo, "tag", CONTAINER_TAG)
    _commit(repo, "ordinary work since")
    return repo


def test_the_version_ignores_container_release_tags(repo_with_both_tag_series: Path) -> None:
    """An untagged build must report the last PACKAGE release, not the last container release."""
    described = _git(repo_with_both_tag_series, *_configured_describe_command()[1:])

    assert described.startswith(RELEASE_TAG), (
        f"the version resolved from {described!r}, but must derive from {RELEASE_TAG!r} — "
        f"{CONTAINER_TAG!r} is a container release, not a package version"
    )
    assert CONTAINER_TAG not in described


def test_a_tagged_release_still_reports_its_own_version(repo_with_both_tag_series: Path) -> None:
    """The guard must not cost us the one case that was already correct.

    Restricting the match is only safe if a real release still describes as itself, so this pins
    the behaviour the issue relied on when it called tagged releases unaffected.
    """
    _git(repo_with_both_tag_series, "tag", "v0.2.0")

    described = _git(repo_with_both_tag_series, *_configured_describe_command()[1:])

    assert described.startswith("v0.2.0"), (
        f"a tag at HEAD must win: expected v0.2.0, resolved from {described!r}"
    )


def test_the_describe_command_is_pinned_in_the_build_config() -> None:
    """Fail loudly if the setting is dropped, rather than silently inheriting the broken default.

    Without this, deleting `raw-options` leaves the behavioural tests passing whenever no
    container tag happens to be the most recent one — which is most of the time.
    """
    raw_options = PYPROJECT["tool"]["hatch"]["version"].get("raw-options", {})

    assert "git_describe_command" in raw_options, (
        "[tool.hatch.version] must pin git_describe_command; without it setuptools_scm describes "
        "against every tag, including the mission-base-v* container releases"
    )
