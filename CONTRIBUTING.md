# Contributing to XORCISE

Thanks for considering a contribution. This guide covers setting up, running the checks,
and proposing a change.

**Found a security vulnerability?** Do not open an issue or a pull request —
see [SECURITY.md](SECURITY.md).

## Setup

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Node 22 is needed for the
frontend. Docker Engine is required only for the integration/e2e lanes and for running real
missions.

```bash
git clone https://github.com/xorcise-ai/xorcise.git
cd xorcise
uv sync --locked --extra dev   # exactly what CI installs
pre-commit install             # optional but recommended — runs the same checks as CI
```

`--locked` makes uv fail rather than silently re-resolve if `uv.lock` is out of date with
`pyproject.toml`. CI uses the same flag, so if this works for you it will work there.

For the frontend:

```bash
cd frontend
nvm use      # reads .nvmrc → Node 22, the only version CI tests
npm ci
```

> **Do not use a shallow clone.** The version is derived from git tags via `hatch-vcs`;
> `git clone --depth 1` produces a package that thinks it is version `0.1.dev0`.

## Running the checks

CI runs all of these; run them locally before opening a pull request:

```bash
uv run pytest               # the test suite
uv run mypy                 # bare invocation — the config covers src + tests
uv run ruff check .         # lint
uv run ruff format --check .  # formatting (CI verifies; pre-commit fixes it for you)
uv run lint-imports         # the architecture contracts in .importlinter
```

And for the frontend, from `frontend/`:

```bash
npm run typecheck
npm test
```

### Test lanes

Tests are organised into marked lanes; each test carries exactly one lane marker, assigned
automatically from the directory it lives in:

```bash
uv run pytest -m unit          # pure, fast, no Docker — the default lane for most changes
uv run pytest -m adapters      # adapter-level tests (e.g. the Docker driver)
uv run pytest -m topology      # architecture/layout parity checks
uv run pytest -m integration   # cross-component, needs Docker
uv run pytest -m e2e           # the full loop, skip-guarded on missing infrastructure
```

| Lane | Needs Docker | Parallel-safe | Runs on a pull request? |
| --- | --- | --- | --- |
| `unit` | no | yes (`-n auto`) | ✅ always |
| `topology` | no | no | ✅ always |
| `adapters` | some (self-skip) | no | ✅ always |
| `integration` | **yes** | no | ⚠️ only with the `full-ci` label |
| `e2e` | **yes** + fixed ports | no | ⚠️ only with the `full-ci` label |

Run the full `unit` lane (not just a `-k` slice) before you consider a change done — scoped
runs can hide contract and migration failures elsewhere in the suite.

**Run only one Docker lane at a time.** `integration` and `e2e` bind fixed host ports
(run-control on `:3001`, plus the Headscale control plane). Running two at once, or running
them while you have a live `xorcise up`, produces failures that look like real bugs but are
port collisions. Run `xorcise down` first.

Docker-dependent tests **skip themselves** when no daemon is present. A green run without
Docker means "not tested", not "passed" — read the skip summary.

## Continuous integration

Every pull request runs:

| Job | What it checks |
| --- | --- |
| `smoke` | lint, formatting, strict mypy, import walls, the single-distribution guard |
| `test` | the `unit`, `topology` and `adapters` lanes |
| `test-heavy` | `integration` and `e2e` — only on `main` or with the `full-ci` label |
| `frontend` | Next.js typecheck and vitest |
| `build-dist` | builds the real wheel and verifies it actually contains the UI, then installs it into a clean environment and runs the CLI |
| `lint-actions` | audits the workflow files themselves for security problems |
| **`ci-ok`** | aggregates all of the above — **the single required status check** |

On a pull request from a fork, CI runs with a read-only token and **no access to any
repository secret**. A maintainer approves the first workflow run for each new contributor.

### The `full-ci` label

Ask a maintainer to add the **`full-ci`** label if your change touches the runner, the
control plane, networking, ports, containers, or `xorcise up`. That re-runs CI with the
Docker-heavy lanes included, and `ci-ok` then requires them to pass.

## Architecture ground rules

- Read the architecture reference at [docs.xorcise.ai](https://docs.xorcise.ai) (Reference →
  Contributing) first: the layers and the strictly-inward dependency rule.
  `.importlinter` enforces it mechanically. If it fails, the fix is almost never to
  edit `.importlinter` — it is that the import points the wrong way.
- **Stubs are filled in place.** Some packages exist as thin scaffolds waiting for their
  implementation; fill them where they are rather than moving them or creating a new
  location.
- New code needs tests in the correct lane, and imports must stay side-effect free.
- That same reference records the constraints the codebase depends on and why they
  exist. Read the sections relevant to your area first — several rules exist because the
  obvious simplification is wrong. If your change makes one of them untrue, update the
  documentation and the enforcement in the same change.

## Dependencies and lockfiles

Both ecosystems are lockfile-enforced in CI (`uv sync --locked`, `npm ci`), which fails if a
manifest and its lockfile disagree.

```bash
# Python — after editing pyproject.toml
uv lock && uv sync --extra dev && uv lock --check

# Frontend — from frontend/
npm install <package>     # updates package.json AND package-lock.json
```

Commit the lockfile alongside the manifest. When adding a dependency, say in the pull request
**why** it is needed — every dependency is a permanent maintenance and supply-chain cost.

### The 7-day maturity rule

**Do not adopt a package version that is less than 7 days old.** This applies to new
dependencies and to upgrades, in both ecosystems.

The threat is a hijacked or compromised package publishing a malicious release. In practice
those are found and yanked within hours to days, so the risk is concentrated in a release's
first few days — exactly when "just take the newest" would grab it. Waiting a week lets the
rest of the ecosystem absorb that risk first; a version still standing after seven days has
been installed by thousands of other people.

Note what this is *not*: it is not "stay on old versions". Staying current is itself a
security control — stale pins are how known CVEs survive. The rule delays adoption by a week;
it does not license letting dependencies rot.

- **Dependabot enforces this automatically** via `cooldown.default-days: 7` in
  [`.github/dependabot.yml`](.github/dependabot.yml), so its version-bump PRs only ever
  propose releases that have already cleared the window.
- **Security updates are deliberately exempt.** GitHub does not apply cooldown to Dependabot
  security updates, and that is correct — a fix for a known CVE must land now, not next week.
- **Manual additions are on you.** `uv add` and `npm install` take the newest version
  immediately and know nothing about this policy. Check the release date before you commit
  (`pip index versions <pkg>`, or the "Published" date on npm), and if you must take
  something newer, say so explicitly in the pull request and justify it.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
`feat: …`, `fix: …`, `docs: …`, `test: …`, `chore: …`, `ci: …`, `style: …`.
Keep the subject imperative and under ~72 characters; use the body for the "why".

Pull requests are **squash-merged**, so the PR *title* becomes the commit message on `main` —
write it as the changelog entry you want.

## Proposing a change

1. Fork the repo and create a topic branch off `main`.
2. Make the change, with tests.
3. Run the checks above.
4. Open a pull request on GitHub describing what changed and why. Small, focused PRs
   review faster than large ones.
5. If `main` moves while your PR is open, refresh it — GitHub requires the branch to be up to
   date before merging:
   ```bash
   git fetch origin && git rebase origin/main && git push --force-with-lease
   ```
   This is not bureaucracy: two PRs can each pass CI independently and still break `main`
   together. Re-testing against the current `main` is what catches that.

For anything non-trivial (a new feature, a behaviour change, anything touching the
architecture), open an issue first to discuss the approach before investing in code.

Never commit secrets. Real credentials belong in `~/.xorcise/.env` (chmod 0600), never in
the repo; `.env.example` documents every setting by name. Run logs are equally sensitive —
they contain a mission's solution and its run credentials in cleartext — which is why
`scratch/` is gitignored. Secret scanning with push protection is enabled, but it is a
backstop, not a substitute for care.

## Releases and versioning

**Merging a pull request does not release anything.** It adds your change to whatever the next
release contains. A maintainer publishes by pushing a version tag; it is normal for a dozen
pull requests to ship together under one version.

XORCISE follows [Semantic Versioning](https://semver.org/) and is currently pre-1.0
(`0.MINOR.PATCH`):

| Change | Version bump |
| --- | --- |
| Bug fix, docs, internal refactor with no visible change | `0.1.0` → `0.1.1` |
| New backwards-compatible capability | `0.1.1` → `0.2.0` |
| Breaking change to a public interface | `0.2.0` → `0.3.0` (bump the minor, document loudly) |

"Public interface" means anything users or automation depend on: CLI commands and options,
configuration files, environment variables, mission manifests and schema, agent/adapter
contracts, documented Python APIs, REST contracts, machine-readable output formats, and
installation requirements.

If your change touches any of those, say so in the pull request and apply the
`breaking-change` label.

### Release-note labels

Maintainers apply one of these to every pull request; it decides which section of the
generated release notes your change lands in:

`breaking-change` · `feature` · `enhancement` · `bug` · `security` · `documentation` ·
`dependencies` · `internal` · `skip-release-notes`

---

## Contributor License Agreement

XORCISE is published by **Fifth Domain Pty Ltd (ACN 606 251 585)**, which owns the project and
holds the rights needed to license it as a whole.

Before your first pull request can be merged, you need to sign the
[Contributor License Agreement](CLA.md). The CLA assistant will comment on your pull request with
a link; signing takes a minute and covers all your future contributions. You keep the copyright in
your own work — the CLA grants Fifth Domain the licence it needs to distribute and, if it ever
chooses to, relicense the project.

If you are contributing in the course of employment, or your employer otherwise holds rights in
your work, your employer needs to sign the Corporate CLA instead. Email <legal@xorcise.ai>.

By contributing, you also confirm that you have read the
[Code of Conduct](CODE_OF_CONDUCT.md).
