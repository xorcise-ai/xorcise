# Compose profiles

One Docker Compose file per XORCISE role, for **multi-host** deployments where the planes run
on separate machines. A single-host install needs none of this — `xorcise up` boots the `all`
role in-process.

| File | Role |
|---|---|
| `all.yaml` | every plane in one process (what `xorcise up` runs) |
| `control.yaml` | the REST API, web UI and run coordination |
| `runner.yaml` | the Docker-driving runner that brings missions up |
| `headscale.yaml` | the tailnet control plane that carries the agent into a run |
| `collector.yaml` | the OTLP receiver for the evidence pipeline |

## The one rule

There is **exactly one profile per role**, and the set here must match the roles in
[`ROLE_MANIFEST.toml`](../../../ROLE_MANIFEST.toml). `xorcise.core.roles.compose.resolve()`
reads these files at runtime, and `tests/topology/test_parity.py` fails CI if the profiles and
the manifest ever drift apart. Add a role in one place and you must add it in the other.
