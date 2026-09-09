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

import re
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
WORKFLOW_DIR = GITHUB / "workflows"


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

    for template in sorted([*ISSUE_TEMPLATE_DIR.glob("*.yml"), *ISSUE_TEMPLATE_DIR.glob("*.yaml")]):
        if template.stem == "config":  # the chooser, not a form — it has no labels
            continue
        form = _load(template)
        referenced[f".github/ISSUE_TEMPLATE/{template.name}"] = set(form.get("labels", []))

    # The workflows name labels too, and those uses are the ones that go stale silently: a
    # label read by an `if:` expression or created by `gh label create` is invisible to every
    # other check here. Without this, `full-ci` (ci.yml) and `ci-nightly` (nightly.yml,
    # issue-contract.yml) could be deleted from labels.yml with the whole suite still green —
    # exactly the drift this file exists to catch.
    for workflow in sorted(WORKFLOW_DIR.glob("*.yml")):
        names: set[str] = set()
        text = workflow.read_text()
        for pattern in (
            # Anchored on `contains(`: `join(...labels.*.name, '|')` in pr-contract.yml
            # matches a bare `labels.*.name,` pattern and would yield `|` as a label name.
            # BOTH event objects: `issue-contract.yml` gates on
            # `github.event.issue.labels.*.name` ('ci-nightly'), so anchoring this on
            # pull_request alone left that reference uncaught — `ci-nightly` survived only
            # via nightly.yml's `gh label create`, and the comment above claimed otherwise.
            r"""contains\(\s*github\.event\.(?:issue|pull_request)\.labels\.\*\.name,\s*['"]([^'"]+)['"]""",
            r"""gh\s+label\s+create\s+([A-Za-z0-9][\w.-]*)""",
            r"""--label[=\s]+['"]?([A-Za-z0-9][\w.-]*)['"]?""",
        ):
            names.update(re.findall(pattern, text))
        if names:
            referenced[f".github/workflows/{workflow.name}"] = names

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


class TestEncodingHolds:
    """The sync renders labels.yml as TSV. A value carrying a delimiter breaks the record."""

    def test_no_name_or_description_carries_a_delimiter(self) -> None:
        for entry in DECLARED["labels"]:
            for field in ("name", "description"):
                value = entry[field]
                assert "\t" not in value, f"{entry['name']}: {field} contains a tab"
                # Worse than a tab: a newline splits one TSV row in two, the reconcile loop
                # reads the continuation as a label NAME with an empty colour, GitHub answers
                # 422, and `set -e` aborts with every later label unsynced.
                assert "\n" not in value, f"{entry['name']}: {field} contains a newline"

    def test_the_sync_rejects_both_delimiters(self) -> None:
        text = LABEL_SYNC_PATH.read_text()
        assert '"\\t" in value or "\\n" in value' in text, (
            "the sync's render step must reject tabs AND newlines: guarding only the field "
            "delimiter leaves the record delimiter open"
        )

    def test_the_sync_matches_label_names_case_insensitively(self) -> None:
        text = LABEL_SYNC_PATH.read_text()
        # GitHub label names are case-insensitively unique. A case-sensitive existence check
        # sends a drifted `Bug` to POST, takes a 422 `already_exists`, and `set -e` aborts
        # the run with every later label unsynced.
        assert "grep -Fxi" in text, (
            "the sync must match live label names case-insensitively, or a label that "
            "differs only by case aborts the whole run"
        )


class TestTheGateIsRegisterable:
    """The status-check context is the job's display name. It has to stay a bare identifier."""

    def test_the_job_names_itself_exactly_pr_contract(self) -> None:
        name = PR_CONTRACT["jobs"]["contract"]["name"]
        # A maintainer registers this string in the `main` ruleset. When it was
        # `pr-contract (title, release-note label)`, registering `pr-contract` would have
        # created a required check that never reports and left every pull request stuck at
        # "Expected — Waiting for status". Descriptive wording belongs on the step.
        assert name == "pr-contract", (
            f"the contract job must be named exactly 'pr-contract', not {name!r}: this string "
            "is the status-check context a maintainer registers as required"
        )

    def test_ci_does_not_retrigger_on_label_events(self) -> None:
        ci = _load(WORKFLOW_DIR / "ci.yml")
        types = _triggers(ci)["pull_request"]["types"]
        # A release-note label is mandatory on every pull request, so `labeled` here would
        # supersede and restart the whole suite when that label lands — taking the in-flight
        # run's `ci-ok` with it and flipping an already-green request back to not-passing.
        assert "labeled" not in types, (
            "ci.yml must not trigger on `labeled`: pr-contract makes a label mandatory on "
            "every pull request, so this would restart the full suite on every one of them "
            f"(types: {types})"
        )


class TestTheTaxonomyIsDocumentedWhereItIsClaimed:
    """The release-note label set is hand-copied into prose. Nothing bound the copies."""

    def test_every_release_note_label_appears_in_contributing(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text()
        missing = sorted(label for label in selectable_labels() if f"`{label}`" not in text)
        # This PR exists because five labels were referenced by configuration and declared
        # nowhere. The mirror of that is a label declared in git and documented nowhere: an
        # author cannot apply what the contributor guide never mentions.
        assert not missing, (
            f"release-note labels missing from CONTRIBUTING.md: {missing}. The gate requires "
            "exactly one of them, so each has to be named where authors are told to pick one."
        )

    def test_every_release_note_label_appears_in_the_pull_request_template(self) -> None:
        text = (GITHUB / "PULL_REQUEST_TEMPLATE.md").read_text()
        missing = sorted(label for label in selectable_labels() if label not in text)
        assert not missing, (
            f"release-note labels missing from .github/PULL_REQUEST_TEMPLATE.md: {missing}. "
            "The template is where an author reads the list while opening the request."
        )


class TestTheCommitTypeListIsBound:
    """The label list was bound meticulously; the *type* list was copied around unbound."""

    @staticmethod
    def _gate_types() -> set[str]:
        run = PR_CONTRACT["jobs"]["contract"]["steps"][0]["run"]
        match = re.search(r"types='([^']+)'", run)
        assert match, "pr-contract no longer declares a types='...' list"
        return set(match.group(1).split("|"))

    def test_dependabots_commit_prefix_is_a_recognised_type(self) -> None:
        types = self._gate_types()
        for update in DEPENDABOT["updates"]:
            prefix = update.get("commit-message", {}).get("prefix")
            ecosystem = update["package-ecosystem"]
            # Both configs lean on this: pr-contract exempts bots from the LABEL half only,
            # so `build(deps): ...` has to satisfy the title half unaided. Rename the prefix
            # without touching the gate and every Dependabot pull request fails the title.
            assert prefix in types, (
                f"the {ecosystem} block commits with prefix {prefix!r}, which is not in the "
                f"pr-contract type list {sorted(types)}: its pull requests would fail the "
                "title check, and bots are exempt from the label half only"
            )

    def test_the_documented_types_match_the_gate(self) -> None:
        types = self._gate_types()
        claude = re.search(r"`((?:feat|fix)[a-z ]+revert)`", (ROOT / "CLAUDE.md").read_text())
        assert claude, "CLAUDE.md no longer lists the commit types in one backticked run"
        documented = set(claude.group(1).split())
        # Drift here is invisible: an author reads the list in CLAUDE.md, the gate reads its
        # own copy, and nothing compares them until a valid-looking title is rejected.
        assert documented == types, (
            "CLAUDE.md and the pr-contract gate disagree about the commit types — "
            f"only in CLAUDE.md: {sorted(documented - types)}, "
            f"only in the gate: {sorted(types - documented)}"
        )

    def test_contributing_documents_every_type_the_gate_accepts(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text()
        missing = sorted(t for t in self._gate_types() if f"`{t}: " not in text)
        assert not missing, (
            f"types the gate accepts but CONTRIBUTING.md does not document: {missing}"
        )


class TestDocumentedSecurityGuaranteesAreReal:
    """CONTRIBUTING.md states the maturity window as a fact. Nothing checked the value."""

    def test_the_seven_day_cooldown_claim_holds(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text()
        claimed = re.search(r"`cooldown\.default-days:\s*(\d+)`", text)
        assert claimed, "CONTRIBUTING.md no longer states the cooldown value"
        want = int(claimed.group(1))
        for update in DEPENDABOT["updates"]:
            actual = update.get("cooldown", {}).get("default-days")
            ecosystem = update["package-ecosystem"]
            # The stated reason is supply-chain maturity: a hijacked release is usually
            # yanked within days, so the danger window is a version's infancy. Dropping the
            # value silently would leave the guarantee documented and gone.
            assert actual == want, (
                f"CONTRIBUTING.md promises cooldown.default-days: {want}, but the "
                f"{ecosystem} block has {actual!r}"
            )


class TestBootstrapDefinitionsAgree:
    """`nightly.yml` creates `ci-nightly` inline so a fresh fork works before the first sync."""

    def test_nightly_bootstrap_matches_the_declaration(self) -> None:
        declared = {e["name"]: e for e in DECLARED["labels"]}["ci-nightly"]
        text = (WORKFLOW_DIR / "nightly.yml").read_text()
        # A second definition of a label labels.yml owns. They agree today; the point of this
        # test is that an edit to one cannot silently diverge from the other — nightly's
        # `gh label create ... || true` swallows every failure, so nothing else would notice.
        colour = re.search(r"gh label create ci-nightly.*?--color\s+(\S+)", text, re.S)
        description = re.search(
            r"gh label create ci-nightly.*?--description\s+\"([^\"]+)\"", text, re.S
        )
        assert colour and description, "nightly.yml no longer bootstraps ci-nightly as expected"
        assert colour.group(1).lower().strip('"') == declared["color"].lower(), (
            f"nightly.yml bootstraps ci-nightly as {colour.group(1)!r} but labels.yml "
            f"declares {declared['color']!r}"
        )
        assert description.group(1) == declared["description"], (
            f"nightly.yml bootstraps ci-nightly with {description.group(1)!r} but labels.yml "
            f"declares {declared['description']!r}"
        )


class TestDependabotAppliesExactlyOneReleaseNoteLabel:
    """Every Dependabot pull request must satisfy the same one-label rule humans do."""

    def test_each_update_block_applies_exactly_one(self) -> None:
        selectable = selectable_labels()
        for update in DEPENDABOT["updates"]:
            applied = set(update.get("labels", []))
            release_note = applied & selectable
            ecosystem = update["package-ecosystem"]
            assert len(release_note) == 1, (
                f"the {ecosystem} update block applies "
                f"{sorted(release_note) or 'no'} release-note label(s); exactly one is "
                "required. Two is the state pr-contract rejects for a human author, and "
                "release.yml files a pull request under the FIRST matching category, so "
                "the second never fires. Zero means it lands in 'Other changes'."
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
        # Every realistic spelling, not just the one this workflow happens to use today:
        # `-X DELETE`, `--method=DELETE`, `gh label delete` and `curl -X DELETE` delete just
        # as permanently, and a literal-substring check on one of them waves the rest through.
        destructive = re.search(
            r"(?:-X|--method[=\s])\s*DELETE\b|\blabel\s+delete\b", text, re.IGNORECASE
        )
        assert destructive is None, (
            "label-sync must never delete a label: deletion silently strips it from every "
            "issue and pull request carrying it, irreversibly (found "
            f"{destructive.group(0)!r})"
        )

    def test_sync_runs_only_on_main(self) -> None:
        workflow = _load(LABEL_SYNC_PATH)
        triggers = _triggers(workflow)
        # Syncing from a branch would let an unreviewed labels.yml rewrite the live label
        # set before anyone approved it.
        assert triggers.get("push", {}).get("branches") == ["main"], (
            "label-sync must only push labels from main"
        )
        # `push` being branch-filtered is not enough: `workflow_dispatch` accepts any ref
        # from the Run-workflow dropdown, so the job needs its own guard, or an unreviewed
        # labels.yml can be applied straight from a branch.
        guard = str(workflow["jobs"]["sync"].get("if", ""))
        assert "github.ref" in guard and "default_branch" in guard, (
            "the sync job must be pinned to the default branch: workflow_dispatch accepts "
            f"any ref, so push.branches alone does not hold the line (if: {guard!r})"
        )
