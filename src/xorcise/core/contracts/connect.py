"""Connect-prompt wire DTOs (LEAF). The two-artifact split.

A run emits a **MissionPrompt** (what the agent reads at runtime: run-control REST URL +
auth keys + tailnet join + target) and a **LaunchProfile** (the pre-start process env —
OTel). render_prompt_text renders the MissionPrompt ONLY; the LaunchProfile is the
harness-facing artifact (build_launch_profile) and is NEVER inlined into the agent prompt —
telemetry is harness-owned. Only the xorcise.run_id correlation marker rides the
prompt. Imports nothing internal."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConnectTarget(_Frozen):
    name: str
    host: str
    # Optional: a target address surfaced from the manifest static_ips carries no port
    # (the port lives in the objective text). When None, render shows name + host only.
    port: int | None = None


class ConnectArtifact(_Frozen):
    """An artifact the agent must submit over run-control (mirrors the manifest ArtifactSpec).

    Rendered into the prompt's run-control recipe so a prompt-only agent knows what to submit
    and under which name. The flag is the artifact named ``flag``."""

    name: str
    required: bool = True
    description: str | None = None


class ConnectAttachment(_Frozen):
    """A mission companion file the agent may download (mirrors the manifest Attachment).

    Rendered into the prompt so a prompt-only agent knows the file exists and can mint its
    short-lived signed download link by name (GET /attachments/<name>). Only the name
    (and, for context, the media type) is surfaced — bytes never ride the run-control channel."""

    name: str
    media_type: str | None = None


class MissionPrompt(_Frozen):
    """What the agent reads at runtime. Carries no OTLP/OTel field (that is the LaunchProfile)."""

    run_id: str
    mission: str
    objective: str
    login_server: str
    join_key: str  # one-time tailnet pre-auth key (minted by the network fence)
    run_control_url: str  # the authenticated REST run-control base URL
    run_control_key: str  # per-run bearer for run-control (distinct from the tailnet join key)
    targets: tuple[ConnectTarget, ...] = ()
    # The manifest's expected artifacts: rendered so a prompt-only agent knows what to
    # submit + under which name. Empty on a stub/minimal run.
    artifacts: tuple[ConnectArtifact, ...] = ()
    # The manifest's companion files: rendered so a prompt-only agent learns they exist
    # and can mint the signed download link by name. Empty when the mission ships no files.
    attachments: tuple[ConnectAttachment, ...] = ()
    # Air-gapped self-sufficiency: the self-signed control-plane CA (PEM) the agent
    # must trust, and the "headscale.local:<host-ip>" alias it must resolve. Empty on a plain
    # (non-TLS) or stub run; when set, render_prompt_text emits the full SSL_CERT_FILE recipe.
    ca_cert: str = ""
    host_alias: str = ""
    # How much authored intel THIS run may disclose under its per-run intel policy (0 ⇒ none). The
    # renderer advertises GET /intel only when >0, and states "no intel" otherwise. 0 by default so
    # a stub/minimal prompt discloses nothing until the create spine computes the policy-filtered N.
    intel_available: int = 0


class LaunchProfile(_Frozen):
    """The pre-start process env (OTel) for one harness (harness-aware).

    Produced by the per-harness telemetry provider (runs/telemetry) selected by the run's
    source_agent, mirroring how source_agent selects the replay adapter. ``env`` carries the
    static OTLP endpoint/protocol plus any harness-specific flags; a known harness's env also
    carries ``OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<id>`` so every OTLP batch correlates
    (``correlation="resource-attr"``). The generic fallback stays run-agnostic and relies on the
    prompt's xorcise.run_id marker (``correlation="prompt-sentinel"``). ``notes`` are
    operator hints (never env). Additive: an empty LaunchProfile() stays valid.
    """

    env: tuple[tuple[str, str], ...] = ()
    # How this profile's traces bind to the run: "resource-attr" = OTEL_RESOURCE_ATTRIBUTES stamps
    # every batch (full correlation); "prompt-sentinel" = best-effort via the prompt marker only.
    correlation: Literal["resource-attr", "prompt-sentinel"] = "prompt-sentinel"
    # Operator-facing hints (e.g. "append to any existing OTEL_RESOURCE_ATTRIBUTES"). Never env.
    notes: tuple[str, ...] = ()


class StartupTips(_Frozen):
    """Copy-paste launch tips for a host-run harness.

    A superset of the LaunchProfile's env with a ready-to-run ``command`` (the mission substituted
    and shell-quoted), a ``shell_block`` (``export K=V`` lines then the command), and ``tips``
    (per-harness GUI launch guidance lines) — what a user who installed the harness pastes to
    launch it pointed at the run's collector. Built by the delivery layer from a LaunchProfile +
    the provider's launch-command template; ``command``/``shell_block`` are None/"" for a
    launch-agnostic harness. Additive."""

    env: tuple[tuple[str, str], ...] = ()
    command: str | None = None
    shell_block: str = ""
    correlation: Literal["resource-attr", "prompt-sentinel"] = "prompt-sentinel"
    notes: tuple[str, ...] = ()
    tips: tuple[str, ...] = ()
