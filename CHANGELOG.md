# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are derived from git tags (hatch-vcs).

## [0.1.3] - 2026-08-21

### Added

- **Missions carry their creator's own version, and updating one is a single action** — the
  catalog serves schema 3.0 manifests with a required SemVer version, which this client
  previously refused outright: `xorcise mission pull` died on a pydantic traceback for every
  published mission. Manifests now validate as 2.0 or 3.0, and one this build cannot read says
  so ("upgrade XORCISE") instead of raising. An installed mission compares its recorded index
  digest against the catalog's current one and reads "Update available" only where that is
  true; `xorcise mission update <id>` and the console's update action re-pull it in place,
  atomically, and an already-current install is an untouched no-op. The delete-then-pull
  remedy is retired everywhere it used to be suggested.
- **A mission runs on your machine's own architecture when it has one** — the client no longer
  pins `linux/amd64`, so an Apple Silicon or other ARM host runs a validated native arm64
  image rather than emulating an amd64 one. The decision is made before any byte moves: an
  explicit `XORCISE_DOCKER_PLATFORM` still wins unconditionally, a pre-contract catalog
  behaves byte-for-byte as it did before, and a mission with no runnable platform on this host
  is refused up front — naming what it does support — instead of failing after a download. The
  platform that actually landed is verified after the pull, recorded in the install, and
  carried into the run's evidence and its report. `xorcise doctor` and the mission detail page
  both show it.
- **A run's raw OpenTelemetry stream exports as JSONL** — `GET /api/runs/{id}/otlp.jsonl`, an
  "OTLP Traces (.jsonl)" button on the result page, and `xorcise run traces --export`. The
  file carries no XORCISE framing: it is the Collector's own `otlpjson` format, so it feeds
  straight into an OpenTelemetry Collector and on to Jaeger, Tempo or anything else that reads
  OTLP. Exports work mid-run — the evidence stores are append-only with whole-batch commits,
  so a partial export is always a valid prefix of the final stream — and a snapshot can only
  be over-labelled partial, never under-labelled complete.

### Security

- **The tailnet is the only path between an agent and its mission** — a run's mission networks
  are now created `internal`, so no mission service has a route off the box; the run's own
  Tailscale router is the single egress, and the agent reaches the mission over the tailnet
  rather than over any shared bridge. The target can open connections back to the agent, which
  it previously could not — an inbound rule scoped to that one run's router, which now carries
  a **per-run** ACL tag. That scoping is the point: while every router shared one `tag:router`
  identity, each run's inbound rule matched *every* run's router, so a mission that pivoted
  through its router had a compiled path to a concurrent run's agent on any port. The policy
  gate now pairs each router tag with its single permitted agent and rejects any policy that
  does not.
- **A mission that cannot be confined is refused rather than deployed** — a compose file
  declaring a network named `xorcise-egress` (the reserved name for the run's own egress
  network) or an `external: true` network is refused at build time with the collision named.
  Both would have deployed a run that reported itself confined while one network silently was
  not: the reserved name was overwritten, and `internal` is a no-op on a network Compose did
  not create.

### Changed

- **BREAKING: a mission's containers run inside the run's own container** — the runner is
  DinD-only on `docker:29.7.1`. The former topology put every mission's containers on the
  operator's own daemon, where parallel runs collided on fixed container names and published
  ports. Missions fused against an older base generation are gated with a named refusal in
  both the CLI and the console rather than failing partway through a run.
- **The console uses the design system it already declared** — the surface ladder, status
  palette and named type scale in `globals.css` were largely bypassed: 36 hardcoded colour
  values, 9 raw Tailwind type sizes and 21 grids with no base column template, across 29
  files, all now zero. The primitives the system implies but never had — `StatTile`, `Chip`,
  `StatusDot`, `Radio`, `Checkbox`, `Avatar` — exist and are used, and three rules in the test
  suite read the source and fail on a regression, so the drift cannot silently return.
- **Releases publish; deployments discover** — the mission-base release workflow ends at a
  verified multi-platform artifact on GHCR and notifies nothing. Deployments poll the registry
  and adopt a release through their own review. This repository therefore holds no deployment
  URL, no token and no secret, and no automated hand ever rests on a consumer's adoption
  lever. `containers/mission-base/RELEASING.md` records what MAJOR, MINOR and PATCH mean for
  that image and that a published version is immutable.
- **`run events export` reads the server, not the local store** — the last CLI command still
  reading run evidence in-process now goes through `GET /runs/{id}/events.jsonl`, matching
  `run report` and `run traces --export`. Its default output path moves to the working
  directory alongside them; the server still writes its own copy under `~/.xorcise/runs/<id>/`
  when a run seals.

### Fixed

- **`xorcise serve` shuts down promptly** — the nested-container support probe is started at
  boot and deliberately never awaited, but it ran on asyncio's default executor, which
  `asyncio.run()` joins before the process may exit. Every listener closed in about 0.3 s and
  the process then sat for the remainder of a probe documented at 20-40 s. It now runs on a
  daemon thread, so teardown walks away from it — which is exactly what a failed warm-up is
  documented to cost: a cold memo the first run recomputes.
- **Stat figures render at their declared size** — two rungs of the type scale were missing
  from the tailwind-merge configuration, so any class list pairing them with a colour had the
  size silently deleted rather than overridden. Every toned `StatTile` and the scorecard's
  hero percentage rendered at the inherited size. The scale and the configuration are now
  pinned against each other by a test.

## [0.1.2] - 2026-08-06

### Added

- **Missions quote their download size before you pull** — the catalog now carries each
  mission's compressed size, and every surface where a pull gets decided quotes it: the
  mission card in the console (with the image/attachment split in its tooltip), the mission
  detail page, `xorcise mission list` (a new Size column) and `xorcise mission show`. The
  figure is a ceiling, not a prediction — missions share base layers, so a pull that reuses
  layers already on disk transfers less. An unknown size reads as unknown, never as "0 B",
  and an installed mission quotes no size at all: its bytes are already on disk.
- **The mission detail page draws the real terrain map** — the hand-rolled linear
  Agent → Service flow is gone; the page now renders the same map the live run view uses,
  projected from the mission manifest with no run attached, so what you study on the
  mission page is exactly the graph a run of it starts from.
- **The live run's terrain map goes fullscreen** — a toggle expands the same map instance
  into a full-viewport overlay, so pan, zoom and the live feed carry over; Escape, the
  backdrop or the toggle collapse it back into the split pane.

### Fixed

- **The terrain map's chrome no longer steals space from the graph** — a paragraph-length
  mission summary now clamps to two lines behind Show more instead of squashing the graph,
  the view toolbar no longer overlaps the canvas at constrained widths, and a minimised
  legend no longer costs the graph a dead column on the right. The graph is also memoised,
  so panning no longer re-renders every node and edge on each pointer move.

### Changed

- **Empty catalog tabs say what is actually true** — the Other providers tab is now a
  designed coming-soon state (a provider constellation drawn in the terrain map's own
  vocabulary) instead of a dead-end box identical to "no search results", and an empty
  Your Own tab no longer instructs you to ingest a bundle — an action this build cannot
  perform yet. Both surfaces share one coming-soon panel and point at the mission library
  that works today.

## [0.1.1] - 2026-08-04

### Added

- **Missions install themselves on `run create`** — `xorcise run create` no longer refuses an
  uninstalled mission; it pulls it first, docker-run style, which is what `POST /runs` already
  did. The pull renders the same honest progress as `xorcise mission pull`, Ctrl-C still cancels
  the job server-side, and all of its messaging goes to stderr so `--json` output stays
  parseable. A failed or externally cancelled pull exits 1 with no run created; a pull still
  going when the client's poll cap expires exits 3 and continues server-side.
- **Model refusals are surfaced as evidence** — when a provider blocks a request on policy, the
  OpenHands and Claude Code adapters now record it as a labelled refusal event instead of
  discarding the failed call. A refusal is a fact about the run and is graded as one. The
  console discloses which harnesses support refusal detection, so an unsupported harness reads
  as "unknown", never as "no refusals".

### Fixed

- **Run replay keeps the agent's own chronology** — events are ordered by the agent's clock
  rather than by when XORCISE received them, so a delayed OpenTelemetry export can no longer
  move a prompt behind the response it produced. Ordering stays deterministic when timestamps
  tie.

### Security

- **Mission slugs can no longer escape the install store** — a slug arriving from a REST path
  parameter, a request body or a bundle manifest now resolves through a single choke point that
  rejects anything which is not a direct child of the install root. Previously a slug carrying a
  path separator, `..` or an absolute path could aim reads, the delete path's `rmtree`, and an
  install's staging, backup and final writes outside that root. Reads now degrade to "not
  installed"; installs fail preflight with a named error.
- **The filesystem browser answers only the local operator** — `GET /api/fs/list` exposes the
  server host's directory tree, so it is now gated on the peer address: non-loopback clients —
  LAN peers under an explicit `XORCISE_HOST=0.0.0.0` bind, and agent containers reaching the API
  over the Docker bridge — get a 403.

### Changed

- **The README quickstart runs as written** — it now follows the CLI's own golden path: set the
  judge model, register the agent with `--kind`, pull a real library mission, then
  `run launch-cmd` for the block you paste into the agent's terminal.
- **The published policies match the shipped software** — the documented default bind is stated
  as it really is (loopback plus the Docker bridge gateway, widening to the IPv4 wildcard only
  when you ask for it, or when the gateway cannot be determined at boot), the security
  response-time commitments that could not yet be honoured are gone, and `ACCEPTABLE_USE.md`
  now ships inside the wheel, as the policy says it does.
- **Fifth Domain Pty Ltd is named as the copyright holder**, and a Contributor License Agreement
  now covers contributions. The licence itself is unchanged: Apache-2.0.

## [0.1.0] - 2026-08-03

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
