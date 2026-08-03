# Deploy

> **Experimental.** Multi-module (distributed) deployment — running each role on a
> separate host — is under active development and not yet a supported configuration.
> The single-host install below is the supported path today. The per-role compose
> profiles are provided for evaluation and may change between releases.

Where XORCISE runs. `compose/profiles/<role>.yaml` mirrors the role set in
`ROLE_MANIFEST.toml`, one file per role — `xorcise.core.roles.compose.resolve()` reads them
at runtime, and `tests/topology/test_parity.py` fails if the two ever drift apart.

A single-host install needs none of this: `xorcise up` boots the `all` role in-process.
This is the recommended way to run XORCISE.
