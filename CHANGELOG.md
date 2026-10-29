# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Versions are derived from git tags (hatch-vcs).

## [0.2.1] - 2026-10-29

**Upgrading.** This release adds database migration `0009`. Migrations never run on boot, and
`xorcise up` refuses a database that is behind. Run `xorcise down` before you install this
release, because `xorcise up` adopts a 0.2.0 server that is still running instead of migrating.
Then back up `~/.xorcise/xorcise.db`, since `0009` deletes some grades and its downgrade restores
none of them, and run:

```
xorcise db upgrade
xorcise up
```

Upgrade the CLI and the server together. Scripts that read run states, run triggers or
`xorcise leaderboard --json` should check the first three entries under Changed.

- **`0009` records when each run's agent started, and ends the finished runs whose agent never
  did.** Every run gains `started_at` and `start_trigger`, backfilled from the evidence it already
  holds. A finished run that timed out or was stopped with no telemetry, no submission, no fetch
  of its brief or an attachment, and no confirmed join is ended `never_started` or `cancelled`,
  and the grade an earlier build gave it is deleted. `terminal_detail` keeps the old trigger and
  score. Earlier builds did not record every start signal, so a run whose agent worked but sent no
  telemetry, submitted nothing and fetched no brief is relabelled too. Its grade had nothing of
  the agent's to score either way. A downgrade ends both new triggers `crashed`, which earlier
  builds never grade. (#211, fixes #187, #209)

### Added

- **The performance views show every run and a median, not an average.** An average mixed runs
  that mean different things: a perfect solve whose judge never ran scored 50%, and an
  environment failure was not the agent's attempt at all. `GET /api/results/runs` serves every
  finished run with its scores, and `GET /api/results/summary?by=all|agent|mission|mission_agent`
  serves the median, quartiles, best and mean, the checks' and the judge's averages, and a count
  of each kind of run left out. The median covers runs graded with a judge, including timed-out
  runs at the score they earned. Runs with no judge, stopped by an operator, whose grading failed,
  that hit an environment failure or that are still grading are counted and disclosed, never
  included. An older mission version is its own group. Results › Agents draws one column per run
  with the median across it, Results › Missions plots checks against judge for each mission
  version, and a run ledger on the agent page replaces the score-over-time chart. The dashboard,
  the Agents list and the result page's cross-run card read the same summary. A grade recorded
  when grading crashed is now marked `grading_failed`, where it read as no judge. (#206, fixes
  #205)

### Changed

- **A working run reads `xorcising`, and its start is recorded.** Nothing ever moved a run out of
  `created`, so every surface read Created for the whole life of a working run. A run now moves to
  `xorcising` the first time its agent acts: its first telemetry export, a run-control call with
  the run's key, or the fetch of its tailnet key. `RunEntry.state` is one of `created`,
  `xorcising` and `terminal`, and `RunEntry` gains `started_at`, `start_trigger` and
  `start_trigger_label`. `xorcise run status --json` on an unfinished run returns `"created"` or
  `"xorcising"` where it returned `"active"`. The console, the CLI and the report show Created and
  Started separately, where they labelled the creation time Started, and the last column of
  `xorcise run list` is now titled Created. To migrate, read `xorcising` where you read `active`.
  (#211, fixes #209)
- **A run whose agent never started is not graded.** Stopping a run before its agent starts now
  ends it `cancelled`, and a budget that runs out first ends it `never_started`. Earlier builds
  graded the empty evidence and recorded a 0.00 against the agent. Both endings share the new
  outcome `not_started`: `GET /api/runs/{id}/result` answers
  `not_graded`, a regrade answers 409, and the performance views and the dashboard's Evaluated
  count leave the run out. The run is still sealed, released and exported. A stop that loses a
  race with the agent's first signal is recorded as `operator` and graded as before. To migrate,
  treat `cancelled` and `never_started` as you treat `deploy_failed` and `crashed`.
  (#211, fixes #187)
- **`xorcise leaderboard` follows the performance views' rule.** It reads the server's summary and
  shows Median, Mean and Best, with counts of timed-out, stopped, grading-failed, infra-failed and
  no-judge runs. `--json` keeps v0.2.0's keys, but their values follow the median's rule:
  `avg_overall` now includes timed-out runs and leaves out runs with no judge. It needs a server
  with `/api/results/summary`. (#206)
- **New runs read the run-control bearer from the environment.** The launch block exports
  `XORCISE_RUN_BEARER` before the agent's command, and the stored prompt refers to the variable
  instead of carrying the token. A custom launch command that starts the agent in a container must
  forward it, for example with `docker run -e XORCISE_RUN_BEARER`. `XORCISE_TS_DIR` now names the
  parent of `join.sh`'s work directory, not the directory itself, and is refused when another user
  can write to it. A `tailscale` client too old to read its key from a file stops the join with an
  upgrade message instead of taking the key on its command line. (#196)
- **Dependencies.** The pinned `opentelemetry-proto` moves from 1.44.0 to 1.45.1, and the
  console's `lucide-react` from 0.577 to 1.45. Frontend tests move to vitest 5. The rest are
  minor and patch updates to Python, frontend and GitHub Actions dependencies.
  (#151, #153, #177, #178, #198, #214)
- **Contributor tooling.** `next build` and `next dev` no longer leave the working tree dirty.
  The real-daemon reap tests refuse to run on a host whose Docker holds containers they did not
  create, instead of tearing down its live runs. (#200, #212, fixes #126)

### Fixed

- **Every run's inner Docker daemon left its volume behind.** A run container's
  `/var/lib/docker` is an anonymous volume holding the inner daemon's whole image store, and
  teardown removed the container without it. One development host had 393 of them holding
  195 GB. Teardown now removes the volume with the container. `xorcise down` removes every managed
  container by id, with its volume, reports only what it removed, and carries on past one that
  will not go instead of aborting before the Headscale teardown. Volumes leaked by earlier builds
  carry no label, so XORCISE leaves them alone. `docker volume prune` reclaims them, but it
  removes every unused anonymous volume on the host. (#212, fixes #66)
- **Viewing a finished run no longer changes its evidence.** Opening a sealed run's page could
  record a telemetry fact after the seal, so the run then read `mismatch` and its report showed a
  MISMATCH callout, though nothing had been tampered with. The join reconciler could do the same
  for two minutes after a run ended. The observed-facts store now refuses a fact for a sealed run,
  and sealing records the telemetry fact first. Runs that already read `mismatch` for this reason
  are not repaired, because a repair would delete rows from sealed evidence. (#217, fixes #194)
- **A grade can no longer start while its run is being deleted.** `DELETE /api/runs/{id}` and
  `DELETE /api/agents/{name}` hold each run's grading slot from their checks to the end of the
  purge. A grade turned away by a delete that is then refused is scheduled again.
  (#211, fixes #188)
- **`xorcise doctor` clears the nested-container refusal the server holds.** After a host was
  fixed, for example by enabling Rosetta on Apple Silicon, runs stayed refused until
  `xorcise down && xorcise up`, because `doctor` re-probed in its own process. It now has the
  server re-probe through the new `POST /api/system/nested-support/refresh` and shows the
  server's verdicts, falling back to a local probe when no server answers. (#213, fixes #195)
- **A slow catalog can no longer hold a page load open.** The announcements fetch checked its
  five-second deadline only once headers had arrived, so a catalog that sent one header at a time
  held the page load open (22.5 s measured, unbounded in principle). The fetch now runs on its own
  thread, a page load waits for it at most five seconds, and page loads share one fetch per
  catalog. (#218, fixes #161)
- **A server with no runner refuses runner work instead of faking it.** Under
  `serve --role control`, a run create answered 201 for a run that stayed `created` forever, a
  mission pull recorded an install with no image, and a release marked an environment released
  while its container kept running. These now answer 503 and name what was refused, and the
  background jobs that need a runner do not start. Role `all` (`xorcise up`) is unaffected.
  (#202, fixes #168)
- **A distributed install refuses to listen on loopback only.** With the distributed topology and
  a loopback `host`, nothing answered at the address agents and the collector were given. `serve`
  and `up` now refuse, and say to set `host` in `config.toml`. (#202, fixes #169)
- **Console fixes.** A focused control draws one focus indicator, not its own ring plus the global
  outline, and the timeline-waterfall markers draw their own (#182). A finished run states its
  status once on its live view, and the Objective chip, which read Solved for a run that failed
  its objective, is gone (#38). The result page shows the model the harness reported and the
  telemetry window, in the report's own words, from the new `agent_model` and `telemetry_window`
  fields of `GET /api/runs/{id}/result` (#146). The All Runs table and an agent's run history say
  No judge instead of 0%, and agent history rows carry `judge_status` (#143). Text fills its card
  instead of stopping at a 68-character measure (#203).
  (#204, fixes #38, #143, #146, #182, #203)
- **The Generic harness no longer claims span support it cannot know.** The register and edit
  agent pages showed the generic adapter's profile as, for example, "7 supported" with green
  checks. For `generic`, a blank Harness ID or a label of your own they now show every span group
  as unknown, and choosing Generic shows a warning that the generic adapter is not verified
  against any harness. (#207)
- **`xorcise config set-model --span-max-tokens` works on its own.** It was rejected as "nothing
  to set". (#200, fixes #124)
- **`serve` explains a plane whose startup fails on uvicorn 0.53 and later.** It exited 3 with no
  message. (#200, fixes #122)
- **The compose profiles README no longer advertises a multi-host install.** It says the profiles
  are scaffolding, and that `xorcise up` is the only working install. (#200, fixes #170)

### Security

- **The tailnet join key and the run-control bearer stay off process command lines.** Any local
  user can read every process's arguments. `join.sh` passed the join key to `tailscale up` and the
  bearer to `curl` as arguments, in sidecar mode as well as native, and the launch command carried
  the whole prompt, bearer included, for the entire run. Claude Code and Codex also logged that
  prompt into the run's traces. The key now reaches `tailscale up` as a mode-0600 file, the bearer
  reaches `curl` on stdin, and the prompt refers to `$XORCISE_RUN_BEARER`. The join script's
  reapers poll the new `GET /api/runs/{id}/alive` with a watch token that reveals only whether
  the run is still going. `/prompt` and `xorcise run prompt` still inline the token for a
  hand-off, and runs created before this release keep their stored prompts. (#196, fixes #172)
- **Headscale is pinned by digest, and the CA key is in no container.** `xorcise up` ran the
  floating `headscale/headscale:stable`, which moved to v0.29 while the adapter was written for
  v0.28. It now runs `headscale/headscale:v0.29.5`, verified by image ID, and refuses to replace a
  newer Headscale with it, because Headscale cannot migrate a database backwards. A host that
  cannot pull is told how to `docker save` and `docker load` the image. On the next `up` the CA
  key moves to `headscale/ca/`, which no container mounts, and the router stops receiving the
  server's TLS private key. A change of host IP re-signs the server certificate with the existing
  CA instead of minting a new one. Existing installs keep the old `:stable` image on disk.
  (#199, fixes #171)
- **A config value can no longer break or extend `~/.xorcise/.env`.** The model, terrain-model
  and network setters wrote values unquoted, so a line break in one (for example in
  `headscale_url`) could corrupt the file or add a variable of its own. A value with a line
  break now gets a 422 and nothing is written, `config set-model --key` refuses one too, and
  `.env` is never readable by other users while it is written. (#200, fixes #160)

## [0.2.0] - 2026-10-16

**Upgrading.** This release adds database migrations `0003` to `0008`. Migrations never run on
boot, and `xorcise up` refuses a database that is behind. Run `xorcise down` before you install
this release, because `xorcise up` adopts a 0.1.3 server that is still running instead of
migrating. Then back up `~/.xorcise/xorcise.db`, since `0005` deletes scores and its downgrade
restores nothing, and run:

```
xorcise db upgrade
xorcise up
```

- **`0003` records why a run ended, with the logs the readiness gate read.** (#98)
- **`0004` stores the evidence digest taken when a run seals.** (#139)
- **`0005` deletes the scores of runs that ended `deploy_failed` or `crashed`.** Some may be
  above zero. The runs, their telemetry and their events stay. (#164)
- **`0006` sets every agent with a missing or blank kind to `generic`.** (#176)
- **`0007` records when a run's environment was released.** Finished lab runs from before the
  upgrade read Releasing until the release sweeper revisits each one once. (#185)
- **`0008` makes telemetry batch numbers unique per run.** A run that already holds a duplicate
  is renumbered and named in a warning. No payload changes. (#190)

### Breaking changes

- **Run status is one closed vocabulary, worded by the server.** `RunEntry.terminal_trigger`
  accepts only `done`, `timeout`, `operator`, `deploy_failed` and `crashed`. `RunEntry` gains
  required read-only `outcome` and `status` fields, and `GET /agents/{name}/history` rows gain
  `status`. The CLI and the console print one label set (Completed, Timed out, Stopped, Deploy
  failed, Crashed, Created, Running, Ended), and the report's Status row prints the label
  instead of `terminal (timeout)`. To migrate, render `status` instead of mapping triggers
  yourself, and upgrade the CLI with the server. An older CLI fails on `xorcise run status` for
  a run that was not graded. (#164, fixes #159, #165)
- **A run cut short by an environment failure is never graded.** A run that ends `deploy_failed`
  or `crashed` is still sealed, released and exported, but gets no score.
  `GET /runs/{id}/result`, `/report` and `/stats` answer `200 {"status": "not_graded", ...}`,
  `POST /runs/{id}/regrade` answers 409, and `xorcise run report` exits 1. These runs count
  under Runs and a new Infra failed column (`infra_failed` in `xorcise leaderboard --json`), but
  not in Avg, Best, the completion and partial rates, or the dashboard's pass rate. Migration
  `0005` deletes the scores already recorded for them. To migrate, branch on
  `status == "not_graded"` as you do on `"grading"`, and stop polling when you see it.
  (#164, fixes #109)
- **Every agent has a kind, `generic` by default.** `AgentEntry.kind` is now a required string,
  so `GET /agents`, `POST /agents` and `PUT /agents/{name}` return `generic` where they returned
  `null`. `kind` stays optional on input, and an agent saved without one, including by a `PUT`
  that omits it, is stored as `generic`. Runs are unaffected. To migrate, read `generic` where
  you read `null`, and run `xorcise db upgrade` for migration `0006`. (#176, fixes #141)
- **A finished run reports `releasing` until its environment is gone, and deletes can refuse.**
  `GET /runs/{id}/environment` gains the state `releasing`, which a finished lab run reports
  until its teardown lands. It used to answer `released` for every finished lab run.
  `DELETE /runs/{id}` and `DELETE /agents/{name}` can answer 409 with `reason` (`running`,
  `grading` or `unreleased`), `run_ids` and `forceable`. Deleting an agent whose run is still
  running is refused, where it used to delete the run too. To migrate, treat `releasing` as
  "finished, not torn down yet" and show the 409's `detail`. Terminate a running run before
  deleting it, retry a `grading` refusal once the grade settles, and send `force=true` only when
  `forceable` is true (`xorcise run delete --force`, `xorcise agent rm --force`).
  (#185, fixes #173)

### Added

- **Mission image disk can be reclaimed and repaired.** `xorcise mission prune` lists the
  mission images nothing uses, and `--yes` removes them. `xorcise mission delete <id> --purge`
  removes the image with the install, and `xorcise mission pull <id> --refresh` downloads an
  installed mission's image again to repair a damaged layer. The console offers all three. Prune
  never removes an image in use by an install, an unfinished run, a container or an active pull.
  (#174, fixes #44)
- **`xorcise run export` exports a set of runs at once.** Select with `--mission`, `--agent`,
  `--since` and `--genuine-only`. Each finished run lands in `<out>/<run-id8>/` with its report,
  `result.json`, `traces.otlp.jsonl` and `events.jsonl`. A run that was not graded has no
  report. (#140, fixes #114)
- **XORCISE Remote announcements show as banners.** Maintenance windows, incidents and
  all-clears appear below the header or in the XORCISE Remote catalog tab. The console reads
  `GET /api/announcements` when a page loads, retries a failed fetch up to twice, and never
  polls. With the remote catalog disabled or unreachable, nothing shows.
  (#137, #163, fixes #136, #162)
- **The OTLP receiver records the batches it drops.** A batch that matches no run, or arrives
  after its run sealed, is logged and counted under `drops` on the receiver's `/healthz`. Set
  `otel_drop_spool_enabled = true` in `config.toml` to keep those batches in a bounded spool.
  (#132)

### Changed

- **Every mission install now contacts the registry.** `xorcise mission pull`, `mission update`,
  the console's Pull and the auto-pull in `xorcise run create` pull even when the image is
  already local, at the cost of one round trip. If the registry is unreachable the install fails
  instead of reusing the local copy, so check setups that pre-load images with `docker load`.
  Installed missions are not affected. (#174)
- **An agent kind no harness answers to is flagged everywhere.** `xorcise agent register` and
  `agent update` warn, suggest the closest harness and ask y/N, and exit 2 without a terminal
  unless `--yes` is given. `POST /agents` and `PUT /agents/{name}` return the warning in
  `warnings`, and the console shows it. The console's Custom harness card is now Generic, and
  `xorcise run list` gains a Harness column. (#131, #176, fixes #119, #141)
- **Telemetry that XORCISE cannot interpret is labelled as such.** In a run rendered by the
  generic adapter, a span no rule recognises shows as unclassified with its raw name, instead of
  as a tool call with an invented body. A run whose telemetry carries no content gets a warning
  in the replay, the report and `xorcise run status`. Older runs are re-counted on first read,
  so their tool-call counts can drop. (#129, #130, fixes #120)
- **The console moves from Next.js 15 to 16 and from React 18 to 19.** The pinned
  `opentelemetry-proto` moves from 1.42.1 to 1.44.0. Frontend test tooling moves to vitest 4,
  jsdom 30 (from 25) and `@testing-library/jest-dom` 7.
  (#9, #70, #85, #89, #90, #100, #101, #103, #104, #108)
- **Contributor tooling.** CI runs the Docker-heavy lanes on every pull request that changes
  more than documentation, a test fails when a config names a label `.github/labels.yml` does
  not declare, and `CLAUDE.md` installs from `uv.lock`. A frontend call that only failed under
  jsdom is fixed. (#88, #91, #92, #94, #180, fixes #87, #93, #106)

### Fixed

- **Lab missions run on arm64 Linux hosts.** The nested-container check always probed at
  `linux/amd64`, so it refused every lab run on an arm64 Linux host without x86 emulation. It
  now probes at the mission's own platform, and a refusal shows the probe's output.
  (#80, fixes #79)
- **`up`, `down`, `status`, `ui` and `db upgrade` agree on whether a server is running.** A
  `down` whose kill fails now exits 1 instead of reporting "stopped", and `up` adopts a running
  server instead of starting a second one on the same database. `status` and `ui` no longer
  report a running server as stopped when `HTTP_PROXY` is set.
  (#82, #97, fixes #72, #73, #74, #86)
- **Background jobs run once and no longer freeze the server.** `serve` started reconcile, the
  budget watchdog and the readiness gate once per listen address. They now start once, and the
  budget watchdog grades a timed-out run on a worker thread, so REST and OTLP keep answering.
  OTLP ingest also left that loop and numbers each batch once, instead of re-reading the run's
  whole trace for every batch. A database too busy to take a batch now answers 503 with
  `Retry-After`, which exporters retry, instead of a 500 they drop.
  (#81, #183, #190, fixes #43, #147, #166)
- **A deploy failure fails closed and keeps its evidence.** An inner Docker daemon that does not
  answer now reads as not ready instead of ready. The readiness gate records the container and
  daemon logs, shown on the run page, and tears down once. Its default window is 300 s, up from
  90 s (`XORCISE_READINESS_TIMEOUT_SECONDS`). `xorcise doctor` now says when this host's address
  has changed since `xorcise up` provisioned the control plane. (#98, #99, fixes #43, #62)
- **A failed environment teardown is retried until it lands.** A brief Docker or Headscale
  outage used to leak a run's container until the next boot, and its tailnet nodes for good. A
  release sweeper now retries with backoff (`release_sweep_interval_seconds`, `0` disables),
  `xorcise system` counts failed teardowns, and a failed run create tears down what it started.
  A run whose teardown keeps failing stays listed as an ungraded Deploy failed run while the
  sweeper retries. (#185, #189, fixes #167, #173)
- **Deleting a run removes everything recorded for it.** Telemetry, the seal, agent events,
  observed facts and `runs/<id>/` used to stay behind. Deletes now remove them, and the next
  boot clears what older deletes left. Restoring an older `xorcise.db` makes that boot remove
  the `runs/<id>/` directories of runs newer than the backup. (#186, fixes #117)
- **Commits no longer fail with "database is locked" behind an open read.** SQLite now runs in
  WAL mode. While a server runs, `xorcise.db-wal` sits beside `xorcise.db`, so back up both.
  (#181, fixes #107)
- **The judge works with strict chat templates.** Each criterion call ended with a system
  message, which templates that require it first (Qwen) reject, so the judge half came back
  unavailable. The new order also suits strict-alternation templates (Mistral-family on vLLM),
  and `xorcise config test` probes with the same shape. (#127, fixes #111, #144, #145)
- **Scores and reports say what they rest on.** `xorcise run status`, `run list` (`0.50*`) and
  the leaderboard (a No judge column) flag a score with no judge half. Reports show the model
  the harness reported, and a Telemetry window row beside Duration, so an agent that died after
  a second no longer reads as 30 minutes of work. (#128, #133, #134, fixes #110, #113)
- **A build from a git checkout reports the right version.** Source, editable and nightly
  builds took their version from the mission base image's `mission-base-v*` tags and reported
  `2.0.x`. They now read the package's own release tags. A PyPI install was never affected.
  (#123, fixes #67)

### Security

- **The evidence seal is tamper-evident.** At seal time XORCISE records a SHA-256 over the run's
  raw spans, raw logs, submitted artifacts and observed facts, after any telemetry batch in
  flight has landed. The report and a regrade re-hash and say whether it still matches.
  `GET /runs/{id}/result` carries `evidence_status` and re-hashes only with `?verify=1`. Older
  runs read as unknown, never tampered. This detects modification. It is not a signature.
  (#139, #190, fixes #116, #184)
- **A model key no longer has to pass through argv.** `xorcise config set-model --key-stdin` and
  `config set-terrain-model --key-stdin` read the key from a pipe or a hidden prompt, which
  keeps it out of shell history and the process list. (#125, fixes #115)

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
