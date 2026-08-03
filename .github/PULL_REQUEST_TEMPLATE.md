<!--
Thanks for contributing to XORCISE.

Nothing here is busywork: each item below maps to a check a reviewer would otherwise have to
run by hand. Delete any section that genuinely does not apply, and say why.
-->

## What changed

<!-- The change itself, in a sentence or two. -->

## Why

<!-- The problem this solves. Link an issue with "Closes #123" if there is one. If this is a
     behaviour change, say what a user could not do before and can do now. -->

## Testing

<!-- What you actually ran, and what it printed. "Tests pass" is not evidence; a command and
     its result is. -->

```
# e.g. uv run pytest -m unit -n auto
```

**Test lanes affected** (tick every lane your change could plausibly break):

- [ ] `unit` — no Docker, fast
- [ ] `adapters` — port/adapter contracts
- [ ] `topology` — manifest / compose / import / extras parity
- [ ] `integration` — needs Docker or a live server process
- [ ] `e2e` — full `xorcise up` + scripted agent
- [ ] `frontend` — Next.js typecheck / vitest

> The `integration` and `e2e` lanes do **not** run on pull requests by default — they need a
> Docker daemon and bind fixed ports. If your change touches the runner, the control plane,
> networking, or `xorcise up`, ask a maintainer to add the **`full-ci`** label so those lanes
> run on this PR before it merges.

## User-facing impact

- [ ] No user-visible change (internal refactor, tests, docs only)
- [ ] Backwards-compatible addition (new command, new optional flag, new field)
- [ ] **Breaking change** — describe the break and the migration path below

<!-- Public interfaces include: CLI commands and options, configuration files, environment
     variables, mission manifests and schema, agent/adapter contracts, documented Python
     APIs, REST contracts, and machine-readable output formats. Changing any of these affects
     the next version number — see CONTRIBUTING.md. -->

## Documentation

- [ ] No documentation change needed
- [ ] Documentation updated in this PR
- [ ] Documentation needed and tracked in a follow-up issue (link it)

## Dependencies

- [ ] No dependency changes
- [ ] Dependencies changed **and** the lockfile is committed
      (`uv lock` for Python, `npm install` for `frontend/` — CI runs `uv sync --locked` and
      `npm ci`, which fail if a manifest and its lockfile disagree)

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run mypy` passes (strict)
- [ ] `uv run lint-imports` passes (architecture layer rules)
- [ ] Relevant test lanes pass locally
- [ ] **No secrets, credentials, tokens, keys, internal hostnames or customer data** appear in
      the diff — including in test fixtures, logs and comments
- [ ] No references to internal-only systems (private trackers, internal URLs, internal
      process names) in code or documentation

## Release notes

<!-- Maintainers: apply ONE of these labels so this PR lands in the right section of the
     generated release notes (.github/release.yml):
       breaking-change · feature · enhancement · bug · security · documentation ·
       dependencies · internal · skip-release-notes
-->

---

By opening this pull request you agree that your contribution is licensed under the
[Apache License 2.0](../LICENSE), and that you have read the
[Code of Conduct](../CODE_OF_CONDUCT.md).

**Found a security vulnerability?** Do not open a pull request or a public issue — follow
[SECURITY.md](../SECURITY.md) instead.
