# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, pull requests,
or discussions.**

Report privately using GitHub's **Private Vulnerability Reporting**:

> **Security** tab → **Advisories** → **Report a vulnerability**

This creates a private advisory that only you and the maintainers can see, so the problem can
be fixed before it becomes public.

If you cannot use GitHub's advisory form, email **guru@xorcise.ai** with the subject line
`XORCISE security report`.

### What to include

The more of this you can provide, the faster a fix lands:

- **What the vulnerability is** — the class of issue (privilege escalation, sandbox escape,
  RCE, credential exposure, supply-chain, etc.).
- **Where it is** — affected component, file path, or command. The XORCISE version
  (`xorcise --version`) and how you installed it.
- **How to reproduce it** — exact steps, commands, and any proof-of-concept. A working
  reproduction is by far the most valuable part of a report.
- **What an attacker gains** — what the impact is and who is exposed.
- **Your environment** — OS, Python version, Docker version, and whether you were running
  locally or against a remote deployment.

### What to expect

| Stage | Target |
| --- | --- |
| Acknowledgement that your report was received | within **3 business days** |
| Initial assessment (in scope? severity? reproducible?) | within **10 business days** |
| Progress updates while a fix is being developed | at least every **2 weeks** |
| Fix released and advisory published | varies with severity and complexity |

XORCISE is maintained by a small team. If you have not heard back within the acknowledgement
window, please send a follow-up — it means something went wrong with the notification, not
that your report was ignored.

We will credit you in the published advisory unless you ask us not to. Please give us a
reasonable opportunity to release a fix before disclosing publicly.

## Supported versions

XORCISE is pre-1.0 and moves quickly. Security fixes are issued for the **most recent
release** only.

| Version | Supported |
| --- | --- |
| Latest release | ✅ |
| Anything older | ❌ — upgrade to the latest release |

If you are running from a git checkout rather than a release, update to the latest `main`.

## Scope

XORCISE is a platform for evaluating cyber AI agents. It **deliberately runs offensive tooling
against deliberately vulnerable targets**, so the boundary between "working as designed" and
"vulnerability" needs stating explicitly.

### ✅ In scope

Anything that lets a mission, an agent, or a contributor reach beyond what the design
permits:

- **Isolation / sandbox escape** — a mission container or an agent breaking out to the host,
  reaching the host network, or accessing another run's resources.
- **Control-plane flaws** — authentication or authorisation bypass in the run-control API,
  Headscale/tailnet ACL failures that let runs see each other, node impersonation.
- **The harness and runner** — command injection, path traversal, or unsafe deserialisation in
  XORCISE code that processes mission bundles, manifests, agent output, or traces.
- **Credential and secret handling** — leakage of API keys, tailnet auth keys, registry
  credentials, or certificates into logs, traces, reports, or mission containers.
- **Packaging and supply chain** — anything letting someone tamper with the published
  `xorcise` distribution, the release pipeline, or the CI/CD workflows.
- **Installer and CLI** — privilege escalation or arbitrary code execution triggered by
  installing or running XORCISE.
- **Cloud integration** — flaws in how XORCISE authenticates to or scopes access against
  remote catalog/registry services.
- **Web UI** — XSS, CSRF, or authentication flaws in the bundled frontend.

### ❌ Not in scope

- **Vulnerabilities inside mission bundles** — whether bundled in this repository or pulled
  from a catalog. These are intentional. SQL injection, weak crypto, hardcoded flags, command
  injection and outdated components in a mission are the *content*: they exist so an agent has
  something to find. They are not XORCISE product vulnerabilities, and any mission bundles
  present in the repository are excluded from code scanning for exactly this reason.

  *However*: if a mission's vulnerability lets an attacker escape the mission's isolation
  boundary and affect the host or other runs, **that is in scope** — the escape is a XORCISE
  flaw even though the vulnerability that enabled it is intentional.

- **The fact that XORCISE runs untrusted agent code and offensive tooling.** That is the
  product. Reports amounting to "this tool can attack things" will be closed.
- **Missing hardening that has no demonstrated impact** — absent security headers, verbose
  version banners, and similar, without a concrete attack.
- **Vulnerabilities in third-party dependencies with no XORCISE-specific exploit path.**
  Report these upstream. Dependabot already tracks known advisories here.
- **Findings from automated scanners with no manual validation or working reproduction.**
- **Social engineering, physical attacks, or denial of service** against project
  infrastructure.

## Operational note for users

XORCISE executes untrusted code by design. Run it on infrastructure you are willing to lose:
a dedicated VM or an isolated cloud environment, never a workstation holding credentials or
data you care about, and never on a network segment you would not want a compromised agent to
reach.

## Verifying what you installed

Release artifacts are built by a GitHub Actions workflow and carry **build provenance
attestations**, which cryptographically bind each file to the repository, workflow and commit
that produced it. You can verify a downloaded artifact with the GitHub CLI:

```bash
# Replace xorcise-ai/xorcise with the repository this package was published from.
gh attestation verify xorcise-<version>-py3-none-any.whl --repo xorcise-ai/xorcise
```

XORCISE is published to PyPI using **Trusted Publishing (OIDC)**. There is no long-lived PyPI
API token to steal: the publishing identity is minted per release, for one workflow, and
expires within minutes.
