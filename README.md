<p align="center">
  <img src="https://raw.githubusercontent.com/xorcise-ai/xorcise/main/assets/readme-banner.svg" width="820" alt="XORCISE.AI — Trust Evidence, not Claims.">
</p>

<p align="center">
  <a href="https://pypi.org/project/xorcise/"><img alt="PyPI" src="https://img.shields.io/pypi/v/xorcise?style=flat-square&labelColor=0f0c07&color=e8b84b"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-e8b84b?style=flat-square&labelColor=0f0c07">
  <img alt="Tested on Ubuntu" src="https://img.shields.io/badge/ubuntu-tested-e8b84b?style=flat-square&labelColor=0f0c07&logo=ubuntu&logoColor=e8b84b">
  <a href="https://github.com/xorcise-ai/xorcise/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/xorcise-ai/xorcise/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="LICENSE"><img alt="License Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-e8b84b?style=flat-square&labelColor=0f0c07"></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#what-comes-out">Output</a> ·
  <a href="#bring-your-agent">Agents</a> ·
  <a href="#missions">Missions</a> ·
  <a href="#open-source">Open source</a> ·
  <a href="https://xorcise.ai">Website</a> ·
  <a href="https://docs.xorcise.ai">Docs</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security</a>
</p>

---

**Run your cyber-AI agent against a real mission. Watch everything it does. Grade the evidence.**

> AI can take action. It cannot bear consequences.

A benchmark score tells you an agent finished. It says nothing about the destructive commands
it tried on the way there. XORCISE runs the agent against a live target inside a contained
environment, records every command, tool call and dead end as OpenTelemetry evidence, and
grades that evidence against the mission's own criteria.

Trust is not declared. It is demonstrated.

## Quickstart

Tested on Ubuntu. Needs **Python 3.12+** and **Docker Engine**.

```bash
pip install xorcise
xorcise doctor                            # checks the host first
xorcise up                                # boots the stack, prints the console URL
```

```bash
xorcise config set-model --name <model> --key <key>          # the judge — half the score
xorcise agent register --name my-agent --kind claude-code
xorcise mission list
xorcise mission pull aviary-access
xorcise run create --agent my-agent --mission aviary-access
xorcise run launch-cmd <run_id>           # paste into your agent's terminal, then run it
xorcise run status <run_id>               # score, breakdown, evidence
```

`xorcise down` stops it all. No Docker on the box? `xorcise up --stub` is the
self-contained demo. `xorcise --help` has the rest, and
[docs.xorcise.ai](https://docs.xorcise.ai) walks through a first run end to end.

Prefer to work from source? See [Contributing → Setup](CONTRIBUTING.md#setup).

## What comes out

| | |
|---|---|
| **Live trace** | every command, tool call and message, streaming into the console as it happens |
| **Score** | deterministic checks plus a bring-your-own-model judge |
| **Report** | the full run record, exportable — Markdown, HTML, JSONL |
| **Leaderboard** | agents ranked across recorded results |

Every run gets its own private network and a fresh environment, created for the run and
destroyed after it. An agent under evaluation cannot reach the host, or another run.

## Bring your agent

XORCISE evaluates the agent you already use.

| | |
|---|---|
| **OpenHands** | full trace + tool-call capture |
| **Claude Code** | via OTLP telemetry |
| **Codex CLI** | via OTLP telemetry |
| **Anything custom** | register it, drive it with the connect prompt, submit over REST |

Activity is normalised into one event model, so the trace, the grading and the report read
the same whichever harness produced the run.

## Missions

A **mission** is a self-contained target: services, a network, and the criteria an agent is
graded against. Packaged as bundles, pulled on demand.

Missions are **deliberately vulnerable** — SQL injection, IDOR, network pivots. That is the
point: they exist so an agent has something real to find.

> Run XORCISE on infrastructure you are willing to lose — a dedicated VM or an isolated cloud
> environment, never a workstation holding credentials you care about. It executes untrusted
> agent code against vulnerable targets by design.
>
> **Only point XORCISE at systems you own, or that you have specific written authorisation to
> test.** See [Acceptable use](ACCEPTABLE_USE.md).

## Open source

XORCISE goes public in parts, not whole. This repository is the engine — the CLI, harness
adapters, isolation, grading and console — under Apache-2.0, with issues and pull requests
open.

The evaluation technology is open source. The commercial layer — managed deployment, runtime,
command and sovereign hosting — is not. The agent skills and the documentation source are
published separately as they are readied.

## Documentation & help

| | |
|---|---|
| [xorcise.ai](https://xorcise.ai) | the project website — what XORCISE is and who it is for |
| [Documentation](https://docs.xorcise.ai) | first run, missions, grading, traces, the full CLI and API reference |
| [Contributing](CONTRIBUTING.md) | dev setup, the test lanes, the PR process, versioning |
| [Security](SECURITY.md) | what's in scope, and how to report privately |
| [Acceptable use](ACCEPTABLE_USE.md) | what to point XORCISE at, export control, sanctions |
| [Maintainers](MAINTAINERS.md) · [Code of Conduct](CODE_OF_CONDUCT.md) | who to ask, and how we work |

Found a vulnerability? **Do not open a public issue** — [report it privately](SECURITY.md).
Flaws in the harness, the isolation boundary or the supply chain are in scope; flaws inside a
mission are the content.

## License

[Apache-2.0](LICENSE) © 2026 Fifth Domain Pty Ltd (ACN 606 251 585)

XORCISE.AI is a business name of Fifth Domain Pty Ltd. Contributions are accepted under the
[Contributor License Agreement](CLA.md). "XORCISE" and "XORCISE.AI", the associated logos and
wordmarks, and the `xorcise.ai` domain are trademarks — see the
[trademark policy](https://xorcise.ai/policies/trademark/) and [`NOTICE`](NOTICE).

---

<p align="center">XORCISE<b>.</b>AI — Trust Evidence, not Claims.</p>
