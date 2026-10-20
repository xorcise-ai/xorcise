# Compose profiles

> **Experimental scaffolding, not a deployment.** These files are not a way to run XORCISE on
> several machines. Multi-host deployment is unfinished (see [`../../README.md`](../../README.md)).
> The only working install is `xorcise up`, which runs the `all` role in-process on one host and
> does not use these files.

There is one Docker Compose file per XORCISE role. They exist so the role set has a concrete,
checked shape while multi-host deployment is built out. Each profile is one `build` and one
`command`. None of them has ports, volumes, environment, the Docker socket or a health check.
Starting one gives you a container that runs `xorcise serve --role <role>` and nothing else.

| File | Role | What that role does today |
|---|---|---|
| `all.yaml` | every plane in one process | The complete install. `xorcise up` runs this role in-process, not through this file. |
| `control.yaml` | the REST API and web UI | Never launches a run, yet `POST /api/runs` answers 201 with a run id. The run stays `created`. Only `all` and `runner` own the Docker and Headscale planes. |
| `runner.yaml` | the Docker-driving runner | Serves only `GET /healthz`. The runner control service is not built yet. |
| `headscale.yaml` | the tailnet control plane | Serves only `GET /healthz`. Runs use the Headscale container that `xorcise up` provisions. |
| `collector.yaml` | the OTLP receiver | Receives and stores OTLP. |

`xorcise serve --role <role>` prints these caveats when it starts, and `xorcise role list`
marks every role except `all` as experimental.

## The one rule

There is **exactly one profile per role**, and the set here must match the roles in
[`ROLE_MANIFEST.toml`](../../../ROLE_MANIFEST.toml). `xorcise.core.roles.compose.resolve()`
reads these files at runtime, and `tests/topology/test_parity.py` fails CI if the profiles and
the manifest ever drift apart. Add a role in one place and you must add it in the other.
