"""The mission-base release workflow must publish what the compatibility contract promises.

The runtime already refuses a mission fused on a base generation it cannot run. That gate reads
`ai.xorcise.base.version` off the image, so the gate is only as trustworthy as the artifact —
and the ways a release can be wrong are all ways that still produce a green workflow:

* a single-arch push still succeeds, and still resolves for whoever pushed it. The failure only
  appears for a user on the other platform, which is exactly the audience multi-arch is for.
* an arch-suffixed tag (`2.4.1-amd64`) becomes an unofficial contract the moment anything pins
  to it, and it cannot be un-published afterwards.
* a floating alias on the canonical artifact invites a downstream pipeline to pin to it, which
  breaks reproducibility silently rather than loudly.
* a tag whose MAJOR disagrees with the Dockerfile's generation publishes an image that ASSERTS a
  compatibility it was not built for. Clients trust the label, not the tag.

The workflow guards all of these at run time. This guards the guards: nothing else fails if a
future edit quietly drops one, because the workflow only executes on a release tag — by which
point the mistake is already public and immutable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from xorcise.core.runner.docker.build import BASE_VERSION

pytestmark = pytest.mark.topology

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "mission-base-release.yml"
DOCKERFILE = ROOT / "containers" / "mission-base" / "Dockerfile"

WORKFLOW_TEXT = WORKFLOW_PATH.read_text()
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)
DOCKERFILE_TEXT = DOCKERFILE.read_text()

# `on:` parses as the boolean True in YAML 1.1 unless quoted — read it either way.
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True, {}))
JOBS = WORKFLOW["jobs"]
TAG_PREFIX = "mission-base-v"


def test_only_a_release_tag_can_publish() -> None:
    """Same trust boundary as release.yml: no workflow_dispatch, no branch push."""
    assert set(TRIGGERS) == {"push"}, f"unexpected triggers: {sorted(TRIGGERS)}"
    assert TRIGGERS["push"] == {"tags": [f"{TAG_PREFIX}*"]}, (
        "publishing must be reachable ONLY by pushing a release tag — a branch trigger or "
        "workflow_dispatch would let anyone with write access publish from any ref"
    )


def test_both_architectures_are_published() -> None:
    platforms = WORKFLOW["env"]["PLATFORMS"]
    assert "linux/amd64" in platforms
    assert "linux/arm64" in platforms, (
        "arm64 is not optional — Apple Silicon is where the nesting failure this base fixes "
        "actually bites, so an amd64-only release misses the platform that most needs it"
    )


def test_architecture_is_never_encoded_in_a_tag() -> None:
    """One tag must resolve to the index. `2.4.1-amd64` is not a permitted contract."""
    offenders = re.findall(r"tags:.*?-(?:amd64|arm64)", WORKFLOW_TEXT)
    assert not offenders, f"arch-suffixed tags in the workflow: {offenders}"


def test_the_only_pushed_tag_is_the_immutable_version() -> None:
    """No `latest`, no `2`, no `2.4` on the canonical artifact.

    An alias can be added later as a deliberate decision; it cannot be withdrawn once a
    downstream pipeline pins to it. A reproducible mission build must consume version + digest.
    """
    build = next(
        s for s in JOBS["publish"]["steps"] if "build-push-action" in str(s.get("uses", ""))
    )
    tags = [t.strip() for t in str(build["with"]["tags"]).splitlines() if t.strip()]
    assert len(tags) == 1, f"exactly one tag may be pushed, got {tags}"
    assert tags[0].endswith("${{ needs.verify.outputs.release }}"), (
        f"the single pushed tag must be the release SemVer, got {tags[0]!r}"
    )


def test_the_release_version_is_not_hand_written_in_the_dockerfile() -> None:
    """The tag is the only version input.

    A literal here is a second place to forget, and the two disagreeing is undetectable from
    inside the image. The default must also be obviously-not-a-release: an unset ARG would
    label a local dev build as though it were published.
    """
    m = re.search(r"^ARG\s+BASE_RELEASE=(\S+)", DOCKERFILE_TEXT, re.MULTILINE)
    assert m, "containers/mission-base/Dockerfile declares no BASE_RELEASE build arg"
    assert "dev" in m.group(1), (
        f"BASE_RELEASE defaults to {m.group(1)!r}; a local build must not be labelled as a "
        "release — use something self-evidently unpublished like 0.0.0-dev"
    )
    assert 'LABEL ai.xorcise.base.release="${BASE_RELEASE}"' in DOCKERFILE_TEXT

    build = next(
        s for s in JOBS["publish"]["steps"] if "build-push-action" in str(s.get("uses", ""))
    )
    assert "BASE_RELEASE=${{ needs.verify.outputs.release }}" in str(build["with"]["build-args"])


def test_a_mismatched_major_is_rejected_before_anything_is_pushed() -> None:
    """The one mismatch that yields a WRONG artifact rather than a failed build.

    It must be caught in a job that `publish` depends on — checking it afterwards would mean
    discovering it once an immutable tag already exists in a public registry.
    """
    assert "verify" in JOBS["publish"]["needs"]
    gate = "\n".join(s.get("run", "") for s in JOBS["verify"]["steps"])
    assert "ai.xorcise.base.version" in gate, (
        "the verify job must compare the tag's MAJOR against the Dockerfile's generation label"
    )
    assert "containers/mission-base/Dockerfile" in gate


def test_the_dockerfile_generation_still_matches_the_code() -> None:
    """Belt and braces with test_dind_base_parity: the workflow trusts this label, so a drift
    here would publish a correctly-tagged image asserting the wrong compatibility."""
    labels = re.findall(
        r'^LABEL\s+ai\.xorcise\.base\.version="([^"]+)"', DOCKERFILE_TEXT, re.MULTILINE
    )
    assert labels == [BASE_VERSION], (
        f"Dockerfile declares {labels} but build.BASE_VERSION is {BASE_VERSION!r}"
    )


def test_the_published_index_is_read_back_from_the_registry() -> None:
    """A build succeeding says the workflow ran; only reading the registry says the right thing
    landed. The inspect job must run after publish and check the shape, not just the exit code."""
    assert "publish" in JOBS["inspect"]["needs"]
    inspect = "\n".join(s.get("run", "") for s in JOBS["inspect"]["steps"])
    assert "imagetools inspect" in inspect
    assert "image.index" in inspect, "must assert the tag resolves to an INDEX, not one manifest"
    assert "ai.xorcise.base.release" in inspect, "must assert the labels reached the image"


def test_nothing_is_cached_on_the_publish_path() -> None:
    """Caches are writable from other branches; a poisoned layer must never reach a signed
    release. Same rule release.yml states for the wheel."""
    assert "cache-from" not in WORKFLOW_TEXT
    assert "cache-to" not in WORKFLOW_TEXT
    assert "actions/cache" not in WORKFLOW_TEXT


def test_promotion_is_an_explicit_stub_not_a_silent_no_op() -> None:
    """The control-plane endpoint does not exist yet. A promotion step that 'succeeds' against
    nothing is worse than one that is visibly unimplemented, so the job must say so and must not
    pretend to send anything."""
    promote = JOBS["promote"]
    assert "not yet implemented" in promote["name"].lower()
    body = "\n".join(s.get("run", "") for s in promote["steps"])
    assert "mission_base_version" in body and "index_digest" in body, (
        "the stub must still print the payload shape, so the cloud side has a concrete contract"
    )
    assert "curl" not in body, "the stub must not attempt a real call to an endpoint that 404s"


def test_every_action_is_pinned_to_a_sha() -> None:
    """Repo convention, and the reason it exists: a mutable tag on a third-party action is
    arbitrary code execution inside the job that holds `packages: write`."""
    unpinned = [
        u
        for u in re.findall(r"uses:\s*(\S+)", WORKFLOW_TEXT)
        if not re.search(r"@[0-9a-f]{40}$", u)
    ]
    assert not unpinned, f"actions not pinned to a full commit SHA: {unpinned}"
