# Containers

Build contexts that ship as package data so a real install can build them on demand
(there is no published registry to pull them from).

- `mission-base/` — the fused per-mission base layer: `docker:dind` plus the Tailscale
  client and the runner control entrypoint. Each per-mission fused image is
  `FROM xorcise/mission-base` with the mission's inner stack baked in as
  `/mission/images.tar`, loaded on boot so a deploy needs no inner pull.
  `xorcise.core.runner.docker.build` tags the result locally as
  `xorcise/mission-<slug>:<version>`.
