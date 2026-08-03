# CLAUDE.md — working in this repo

XORCISE runs cyber-AI agents against real missions inside an isolated per-run environment,
records everything they do as OpenTelemetry evidence, and grades that evidence. It ships as
ONE pip distribution (`xorcise`), CLI-first; all internal code lives under `xorcise.core.*`
(bare `xorcise.*` is reserved for products).

- Website: <https://xorcise.ai>
- Documentation: <https://docs.xorcise.ai>
- Source: <https://github.com/xorcise-ai/xorcise> (Apache-2.0)

This file is guidance for AI coding assistants working ON XORCISE itself. If you are trying to
USE XORCISE — install it, run a mission, read a trace — start at the documentation site, not
here. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request; it carries the
setup, the test lanes and the rules this file only summarises.

## Architecture: the dependency rule

Imports point strictly inward, top to bottom:

`cli` → `roles` → delivery (`rest` | `frontend`) → `harness_adapters` → domain modules
(`runs · agents · targets · reporting · orchestration · runcontrol · home · seams`) →
part-islands (`eval · runner · headscale · otel · catalog · missions · code`) →
kernel (`db` | `observability`, over `config`) → `contracts` (leaf).

Part-islands never import each other; only `core/seams.py` may import `premium`.
Enforcement is mechanical: the contracts in `.importlinter` (run `uv run lint-imports`) and
the parity tests in `tests/topology`.

The full architecture reference — every layering rule, the invariants the code depends on,
and why several obvious simplifications are wrong — lives at
<https://docs.xorcise.ai> under Reference → Contributing. Read it before changing anything
structural: several of the rules above look like arbitrary ceremony until you know what they
prevent.

## Toolchain

Python 3.12 + uv.

```bash
uv pip install -e ".[dev]"
uv run pytest                 # test lanes: pytest -m unit|adapters|topology|integration|e2e
uv run mypy                   # bare — config covers src + tests, same scope as CI
uv run ruff check .
uv run lint-imports
```

## Hard conventions (CI-guarded)

- **Zero side-effect imports** — `src/xorcise/__init__.py` and every package import clean;
  FastAPI router registration lives in `roles/boot/`.
- **One distribution** — everything ships in the single `xorcise` package; `xorcise.*`
  top-level names are reserved for products (guard).
- **Stubs are filled in place** — do NOT move packages; if one is missing, fix the scaffold,
  never ad-hoc a new location.
