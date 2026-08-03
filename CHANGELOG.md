# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are derived from git tags (hatch-vcs).

## [0.1.0] - Unreleased

First public release.

### Added

- **CLI-first single distribution** — `pip install xorcise`, then `xorcise up` boots the
  whole stack on one host and prints the console URL; `xorcise doctor` checks the host,
  `xorcise down` tears everything down. A stub mode (`xorcise up --stub`) runs the full
  loop without Docker.
- **Agent registry** — register any cyber-AI agent (OpenHands, Claude Code CLI, Codex CLI,
  or anything custom) as the unit of evaluation; runs and results accrue to the agent's
  track record, with versioned re-declarations for comparability.
- **Missions** — real target environments packaged as pullable images (a free mission
  library) or authored locally as bundles (`mission.json` + compose stack) and ingested
  into a locally built, fused mission image.
- **Isolated runs** — each run gets its own private WireGuard tailnet (Tailscale +
  Headscale) with a per-run ACL as the hard network boundary: the agent can reach exactly
  its one mission and nothing else. Environments are created per run and destroyed at
  teardown.
- **Evidence collection** — a built-in OpenTelemetry collector captures every command,
  tool call and message the agent emits (traces and logs), correlated per run; harness
  adapters normalise the raw telemetry into a replayable event stream.
- **50/50 evidence-anchored grading** — deterministic, mission-defined checks (the
  reproducible half) combined with a rubric-bound, bring-your-own-model LLM judge (the
  qualitative half), graded over the sealed run evidence, never the agent's claims.
- **Run control for agents** — a bearer-authenticated REST surface for the agent under
  test: fetch the mission brief and intel, submit artifacts, and mark the run done.
- **Results and reporting** — per-run scorecards with full breakdowns, exportable run
  reports (Markdown/HTML), per-run event exports (JSONL), agent history, and a
  leaderboard across recorded results.
- **Web console** — a live trace feed, terrain map, run replay, results, and settings UI
  served by the same process at `/ui`.
- **Deployment flexibility** — all-local by default; a distributed topology supports
  relocating the mission plane to a remote host while the control plane and console stay
  local.
- **Live lifecycle feedback** — `xorcise up`'s frontend install/rebuild and `xorcise
  down`'s stop/reap/teardown steps show a live spinner with real build phases and elapsed
  time on a terminal (piped output stays line-based for scripts).
- **Update notice** — `xorcise up` mentions a newer released version when one exists on
  PyPI (checked in the background, cached for a day, silent on any failure; opt out with
  `XORCISE_NO_UPDATE_CHECK=1`; source checkouts are never nagged).
