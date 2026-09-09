"""Every label this repo's configuration references must actually exist.

Labels live in GitHub's database, not in git. That asymmetry is the whole problem: a config
file can name a label that was never created, and nothing anywhere fails. GitHub drops an
unknown label from an issue form or a Dependabot config **silently** — no error, no warning,
no annotation — so the only symptom is an issue or a pull request that quietly arrives
unlabelled, which looks exactly like a human forgetting to label it.

That is not hypothetical. Before this contract existed, five labels were referenced by
configuration and none of them existed (`feature`, `breaking-change`, `dependencies`,
`internal`, `skip-release-notes`), which meant:

* every feature request filed through the form arrived with no label at all, because
  `feature_request.yml` asks for `feature`;
* Dependabot's declared `dependencies` label never applied to a single one of its pull
  requests;
* four of the nine generated release-note sections were unreachable, including
  ⚠️ Breaking changes — the one section a user MUST read before upgrading. A pull request
  titled `feat(runner)!:` merged with no `breaking-change` label because there was none to
  apply.

`.github/labels.yml` fixes the asymmetry by declaring the label set in git, where it can be
reviewed and diffed. This test is what makes the declaration load-bearing rather than
decorative: it fails the moment a config file references a label the repo does not declare,
which is the failure that was previously invisible.

It also binds the `pr-contract` job's inline label list to `.github/release.yml`. That list is
hard-coded in the workflow on purpose — the job must stay a few seconds of `bash` with no
toolchain to install — so something has to notice when a release-note category is added or
renamed and the gate is not told. That something is this test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.topology

ROOT = Path(__file__).resolve().parents[2]
GITHUB = ROOT / ".github"

LABELS_PATH = GITHUB / "labels.yml"
RELEASE_NOTES_PATH = GITHUB / "release.yml"
DEPENDABOT_PATH = GITHUB / "dependabot.yml"
ISSUE_TEMPLATE_DIR = GITHUB / "ISSUE_TEMPLATE"
PR_CONTRACT_PATH = GITHUB / "workflows" / "pr-contract.yml"
LABEL_SYNC_PATH = GITHUB / "workflows" / "label-sync.yml"


def _load(path: Path) -> dict[Any, Any]:
    loaded = yaml.safe_load(path.read_text())
    assert isinstance(loaded, dict), f"{path} did not parse as a mapping"
    return loaded


# `on:` is YAML 1.1's boolean true, so a workflow's trigger block parses under the key `True`,
# not `"on"`. Every reader here goes through this rather than `workflow["on"]`, which looks
# correct and raises KeyError.
def _triggers(workflow: dict[Any, Any]) -> dict[Any, Any]:
    triggers = workflow.get(True, workflow.get("on", {}))
    assert isinstance(triggers, dict), "workflow triggers did not parse as a mapping"
    return triggers


DECLARED = _load(LABELS_PATH)
RELEASE_NOTES = _load(RELEASE_NOTES_PATH)
DEPENDABOT = _load(DEPENDABOT_PATH)
PR_CONTRACT = _load(PR_CONTRACT_PATH)


def declared_names() -> set[str]:
    return {entry["name"] for entry in DECLARED["labels"]}


def release_note_labels() -> list[str]:
    """The labels that route a pull request into a release-notes section.

    `"*"` is the catch-all category, not a real label — it matches whatever no other category
    claimed, so nothing is ever dropped from the notes. It is excluded here because it can
    never be applied to a pull request.
    """
    names: list[str] = []
    for category in RELEASE_NOTES["changelog"]["categories"]:
        names.extend(label for label in category["labels"] if label != "*")
    return names


def excluded_labels() -> list[str]:
    """Labels that keep a pull request OUT of the release notes entirely."""
    return list(RELEASE_NOTES["changelog"].get("exclude", {}).get("labels", []))


def selectable_labels() -> set[str]:
    """Every label a pull request may satisfy the `pr-contract` gate with.

    The union of the routing categories and the exclusion list. `skip-release-notes` is a
    deliberate, complete answer to "which section does this belong in?" — none of them — so
    the gate must accept it even though it names no category.
    """
    return set(release_note_labels()) | set(excluded_labels())


def referenced_labels() -> dict[str, set[str]]:
    """Every label named by configuration, grouped by the file that names it."""
    referenced: dict[str, set[str]] = {}

    referenced[".github/release.yml"] = selectable_labels()

    dependabot: set[str] = set()
    for update in DEPENDABOT["updates"]:
        dependabot.update(update.get("labels", []))
    referenced[".github/dependabot.yml"] = dependabot

    for template in sorted(ISSUE_TEMPLATE_DIR.glob("*.yml")):
        if template.name == "config.yml":  # the chooser, not a form — it has no labels
            continue
        form = _load(template)
        referenced[f".github/ISSUE_TEMPLATE/{template.name}"] = set(form.get("labels", []))

    return referenced


class TestDeclarationIsWellFormed:
    """A declaration the sync workflow cannot apply is worse than none: it looks authoritative."""

    def test_every_label_has_a_name_colour_and_description(self) -> None:
        for entry in DECLARED["labels"]:
            assert entry.get("name"), f"label entry with no name: {entry!r}"
            name = entry["name"]
            assert entry.get("color"), f"{name}: no colour"
            # A label with no description is a label whose meaning lives only in someone's
            # head. The taxonomy in CONTRIBUTING.md is the long form; this is the tooltip.
            assert entry.get("description"), f"{name}: no description"

    def test_colours_are_bare_six_digit_hex(self) -> None:
        # GitHub's API rejects a leading `#` and any length but six.
        for entry in DECLARED["labels"]:
            colour = entry["color"]
            assert not colour.startswith("#"), f"{entry['name']}: colour must not carry '#'"
            assert len(colour) == 6, f"{entry['name']}: colour must be six hex digits"
            assert all(c in "0123456789abcdefABCDEF" for c in colour), (
                f"{entry['name']}: colour is not hex"
            )

    def test_descriptions_fit_githubs_limit(self) -> None:
        # GitHub rejects a description over 100 characters. Without this, an over-long one
        # fails the sync at run time — on `main`, after review, where the only symptom is a
        # red workflow on a merge commit.
        for entry in DECLARED["labels"]:
            length = len(entry["description"])
            assert length <= 100, (
                f"{entry['name']}: description is {length} characters; GitHub's limit is 100"
            )

    def test_no_duplicate_names(self) -> None:
        names = [entry["name"] for entry in DECLARED["labels"]]
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"declared twice: {sorted(duplicates)}"


class TestEveryReferencedLabelIsDeclared:
    """The failure this whole file exists for: a config naming a label that does not exist."""

    def test_referenced_labels_are_declared(self) -> None:
        declared = declared_names()
        undeclared: dict[str, list[str]] = {}
        for source, labels in referenced_labels().items():
            missing = sorted(labels - declared)
            if missing:
                undeclared[source] = missing

        assert not undeclared, (
            "these labels are referenced by configuration but not declared in "
            f".github/labels.yml, so GitHub will silently drop them: {undeclared}"
        )

    def test_release_note_categories_have_no_duplicate_labels(self) -> None:
        # One label in two categories makes the section a pull request lands in depend on
        # the order of the file, which is not a thing a reviewer can reason about.
        names = release_note_labels()
        duplicates = {name for name in names if names.count(name) > 1}
        assert not duplicates, f"label routes to more than one release-note section: {duplicates}"


class TestPrContractKnowsTheReleaseNoteLabels:
    """The gate's inline list must agree with the file that defines the categories.

    Whether this workflow is a REQUIRED status check lives in the repository's ruleset, not in
    git, so no test here can assert it — the same git/GitHub asymmetry this file exists to
    contain. CONTRIBUTING.md records that `pr-contract` must be registered as required, and
    the ruleset is the one place to verify it.
    """

    def test_the_contract_job_exists(self) -> None:
        jobs = PR_CONTRACT["jobs"]
        assert "contract" in jobs, "pr-contract.yml has no contract job"

    def test_title_and_label_edits_retrigger_the_workflow(self) -> None:
        # The gate reads the title and labels off the pull-request event, so the event types
        # ARE the gate's ability to recover. Without `edited`, a corrected title never re-runs
        # the check that rejected it; without `labeled`, applying the demanded label does not
        # clear it; without `unlabeled`, removing the last one leaves a stale pass behind.
        types = _triggers(PR_CONTRACT)["pull_request"]["types"]
        for trigger in ("opened", "edited", "labeled", "unlabeled", "synchronize"):
            assert trigger in types, f"pull_request.types is missing '{trigger}'"

    def test_the_gate_takes_no_token_and_no_code(self) -> None:
        # This workflow reads an attacker-controlled title on every fork pull request. It must
        # stay a `pull_request` trigger with no permissions and no checkout: `pull_request_target`
        # would hand a fork's PR a writable token.
        assert "pull_request_target" not in _triggers(PR_CONTRACT), (
            "pr-contract must never use pull_request_target — it reads untrusted PR titles"
        )
        assert PR_CONTRACT["permissions"] == {}, "pr-contract must request no permissions"
        steps = PR_CONTRACT["jobs"]["contract"]["steps"]
        assert not any("uses" in step for step in steps), (
            "pr-contract must not check out or run any repository code"
        )

    def test_title_is_read_from_env_not_interpolated(self) -> None:
        # A pull-request title is attacker-controlled. `${{ }}` inside `run:` pastes it into
        # the shell; `env:` passes it as data. zizmor checks this too — this states it in the
        # lane that runs on every pull request.
        step = PR_CONTRACT["jobs"]["contract"]["steps"][0]
        assert "${{" not in step["run"], (
            "pr-contract must not interpolate any expression into its script"
        )
        assert "github.event.pull_request.title" in step["env"]["TITLE"]

    def test_inline_label_list_matches_release_notes_config(self) -> None:
        env = PR_CONTRACT["jobs"]["contract"]["steps"][0]["env"]
        declared_in_gate = set(env["RELEASE_NOTE_LABELS"].split())

        assert declared_in_gate == selectable_labels(), (
            "the pr-contract gate's RELEASE_NOTE_LABELS disagrees with .github/release.yml — "
            "a label the gate accepts but the config does not route lands the change in "
            '"Other changes", and one the config routes but the gate rejects blocks a '
            "correctly-labelled pull request. "
            f"gate-only={sorted(declared_in_gate - selectable_labels())} "
            f"config-only={sorted(selectable_labels() - declared_in_gate)}"
        )


class TestLabelSyncIsSafe:
    """Deleting a label strips it from every issue that carries it, and that cannot be undone."""

    def test_sync_workflow_exists(self) -> None:
        assert LABEL_SYNC_PATH.exists(), "no label-sync workflow to apply labels.yml"

    def test_sync_never_deletes(self) -> None:
        text = LABEL_SYNC_PATH.read_text()
        # A label removed from GitHub is removed from every issue and pull request that
        # carried it, with no record of what it was. The sync reconciles additively and
        # reports extras instead; this asserts nobody ever "tidies" that up.
        assert "--method DELETE" not in text, (
            "label-sync must never delete a label: deletion silently strips it from every "
            "issue and pull request carrying it, irreversibly"
        )

    def test_sync_runs_only_on_main(self) -> None:
        workflow = _load(LABEL_SYNC_PATH)
        triggers = _triggers(workflow)
        # Syncing from a branch would let an unreviewed labels.yml rewrite the live label
        # set before anyone approved it.
        assert triggers.get("push", {}).get("branches") == ["main"], (
            "label-sync must only push labels from main"
        )
