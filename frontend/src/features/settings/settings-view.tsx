"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  Server,
  BookMarked,
  Network,
  Plug,
  HardDrive,
  Map,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ComingSoonPanel } from "@/components/ui/coming-soon";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { SkeletonRows } from "@/components/ui/skeleton";
import { CopyButton } from "@/components/ui/copy-button";
import { apiBaseUrl } from "@/lib/api/runtime-config";
import type {
  ModelConfigUpdate,
  PlaneStatus,
  TerrainModelConfigUpdate,
} from "@/lib/api/types";
import {
  useConfig,
  useSystem,
  useSaveAndTest,
  useSetCatalogConnected,
} from "./queries";
import { formatDuration } from "@/lib/api/format";
import { useUiStore } from "@/stores/ui";
import { ConnectionStatus, Dot } from "./connection-status";
import {
  groupByRole,
  moduleLabel,
  moduleStateLabel,
  planeState,
  roleAddress,
  roleTooltip,
  visibleModules,
  type ModuleState,
  type RoleGroup,
} from "./module-groups";
import { TokenLimitSlider } from "./token-limit-slider";

function Card({
  title,
  icon,
  highlight,
  className,
  action,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  highlight?: boolean;
  className?: string;
  /** Optional trailing element in the header row (e.g. a maturity badge). */
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section
      className={
        "min-w-0 rounded-xl border bg-card p-4 transition-colors " +
        (highlight ? "border-primary " : "border-border ") +
        (className ?? "")
      }
    >
      <div className="mb-3 flex items-center gap-2 text-primary">
        {icon}
        <h2 className="text-body font-semibold text-heading">{title}</h2>
        {action && <span className="ml-auto">{action}</span>}
      </div>
      {children}
    </section>
  );
}

/** API-key input with an explicit "configured" state (§6/§10). A saved key read as disabled/greyed:
 *  an empty, masked password box with a "leave blank to keep" placeholder. Here the input stays
 *  present and editable, but a green "Key set" badge sits on the label and the placeholder invites
 *  a replacement — so the field reads as active-with-a-value, not inert. */
function ApiKeyField({
  configured,
  value,
  onChange,
}: {
  configured: boolean;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block text-dense">
      <span className="mb-1 flex items-center gap-2 text-text-tertiary">
        API key
        {configured && <Badge variant="ok">Key set</Badge>}
      </span>
      <Input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={
          configured ? "Enter a new key to replace the saved one" : "sk-…"
        }
      />
    </label>
  );
}

/* ─────────────────────────── Judge model ─────────────────────────── */

function JudgeModelCard({ highlight }: { highlight: boolean }) {
  const config = useConfig();
  const { phase, message, saveAndTest, testOnly } = useSaveAndTest("judge");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [tokens, setTokens] = useState<number | null>(null);
  const [spanTokens, setSpanTokens] = useState<number | null>(null);

  const judge = config.data?.judge;
  const savedTokens = judge?.transcript_max_tokens ?? 0; // 0 = pre-flight cap off (the default)
  const capEnabled = (tokens ?? savedTokens) > 0;
  const savedSpanTokens = judge?.span_max_tokens ?? 2000;
  // Busy = save or the chained auto-verify is in flight; Save stays disabled so
  // the auto-test can't race a second save.
  const busy = phase === "saving" || phase === "testing";

  function onSave() {
    const update: ModelConfigUpdate = {
      key: key.trim() ? key.trim() : null,
      base_url: baseUrl.trim() ? baseUrl.trim() : null,
      model_name: modelName.trim() ? modelName.trim() : null,
      // null = untouched; 0 = disable the pre-flight cap. Send on any change from the saved value.
      transcript_max_tokens: tokens != null && tokens !== savedTokens ? tokens : null,
      // 0 is a valid value here (disable the per-span cap), so send on any change from saved.
      span_max_tokens:
        spanTokens != null && spanTokens !== savedSpanTokens ? spanTokens : null,
    };
    // Saving auto-verifies the key with a live call (Saving… → Verifying
    // connection… → Connected); the Test button re-checks via the same phase.
    saveAndTest(update, {
      onSaved: () => {
        // Clear the whole form on success: the saved values come back as
        // placeholders, so the card returns to a clean (non-dirty) state.
        setKey("");
        setBaseUrl("");
        setModelName("");
        setTokens(null);
        setSpanTokens(null);
      },
    });
  }

  // Any non-empty field means the form diverges from the saved config — used to
  // gate Save and surface an "Unsaved changes" note.
  const dirty =
    key.trim() !== "" ||
    baseUrl.trim() !== "" ||
    modelName.trim() !== "" ||
    (tokens != null && tokens !== savedTokens) ||
    (spanTokens != null && spanTokens !== savedSpanTokens);

  return (
    <Card title="Judge model" icon={<Bot className="size-4" />} highlight={highlight}>
      {config.isLoading && (
        <div role="status" aria-label="Loading config…">
          <SkeletonRows count={3} />
        </div>
      )}
      {judge && (
        <>
          <p className="mb-3 flex items-center gap-2 text-dense">
            {/* Three-state: Connected once verified this session, Configured when a
                key is saved but unverified, Not configured otherwise. */}
            <Badge variant={phase === "connected" ? "ok" : "muted"}>
              {phase === "connected"
                ? "Connected"
                : judge.configured
                  ? "Configured"
                  : "Not configured"}
            </Badge>
            {judge.configured && judge.key_hint && (
              <span className="font-mono text-text-tertiary">key {judge.key_hint}</span>
            )}
          </p>
          <p className="mb-3 max-w-full break-words text-body text-text-secondary">
            The LLM-as-judge grades runs against each mission&apos;s rubric. Bring your own
            OpenAI-compatible model — the key is stored locally (0600) and never returned.
          </p>
          <div className="space-y-2">
            <ApiKeyField configured={judge.configured} value={key} onChange={setKey} />
            <label className="block text-dense">
              <span className="mb-1 block text-text-tertiary">Base URL</span>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={judge.base_url ?? "https://api.openai.com/v1"}
              />
            </label>
            <label className="block text-dense">
              <span className="mb-1 block text-text-tertiary">Model name</span>
              <Input
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                placeholder={judge.model_name ?? "gpt-4o-mini"}
              />
            </label>
            <div className="text-dense">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-text-tertiary">Pre-flight transcript cap</span>
                <Switch
                  checked={capEnabled}
                  onChange={(next) => setTokens(next ? (savedTokens > 0 ? savedTokens : 256000) : 0)}
                  disabled={busy}
                  label="Enable a pre-flight transcript token cap"
                />
              </div>
              {capEnabled && (
                <TokenLimitSlider
                  value={(tokens && tokens > 0 ? tokens : savedTokens) || 256000}
                  onChange={setTokens}
                  disabled={busy}
                />
              )}
              <span className="mt-1 block max-w-full break-words text-caption text-text-tertiary">
                {capEnabled
                  ? "Rejects a run before calling the judge if the estimated prompt exceeds this. It's only an estimate — the model's own context limit is the real gate."
                  : "Off — the judge attempts the call and relies on the model's own context limit; a failed judge can be re-run. Enable only to pre-reject oversized evidence on a small-context model."}
              </span>
            </div>
            <label className="block text-dense">
              <span className="mb-1 block text-text-tertiary">Per-span token cap</span>
              <Input
                type="number"
                min={0}
                value={spanTokens ?? savedSpanTokens}
                onChange={(e) => setSpanTokens(Number(e.target.value))}
                disabled={busy}
              />
              <span className="mt-1 block max-w-full break-words text-caption text-text-tertiary">
                Caps each span&apos;s body — a few giant tool outputs dominate long transcripts, so
                capping keeps every action while collapsing the tail. 0 disables.
              </span>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button onClick={onSave} disabled={busy || !dirty}>
              {phase === "saving" ? "Saving…" : "Save"}
            </Button>
            <Button
              variant="outline"
              onClick={testOnly}
              disabled={busy || !judge.configured}
              title={
                judge.configured
                  ? "Call the saved model to re-check the connection"
                  : "Configure a key first"
              }
            >
              Test
            </Button>
            {dirty && phase !== "saving" && (
              <span className="text-dense text-primary">Unsaved changes</span>
            )}
            <ConnectionStatus phase={phase} message={message} />
          </div>
          <p className="mt-2 max-w-full break-words text-caption text-text-tertiary">
            Test calls the saved key, not the edits above — save first to test new values.
          </p>
        </>
      )}
      {config.isError && <p className="text-dense text-err">Couldn&apos;t load config.</p>}
    </Card>
  );
}

/* ─────────────────────────── Terrain model ─────────────────────────── */

function TerrainModelCard() {
  const config = useConfig();
  const { phase, message, saveAndTest, testOnly } = useSaveAndTest("terrain");
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [tokens, setTokens] = useState<number | null>(null);

  const terrain = config.data?.terrain;
  const savedTerrainTokens = terrain?.transcript_max_tokens ?? 256000;
  const busy = phase === "saving" || phase === "testing";

  function onSave() {
    const update: TerrainModelConfigUpdate = {
      key: key.trim() ? key.trim() : undefined,
      base_url: baseUrl.trim() ? baseUrl.trim() : undefined,
      model_name: modelName.trim() ? modelName.trim() : undefined,
      transcript_max_tokens: tokens != null && tokens > 0 ? tokens : undefined,
    };
    // Saving auto-verifies the terrain model with a live call, same lifecycle
    // as the judge card.
    saveAndTest(update, {
      onSaved: () => {
        setKey("");
        setTokens(null);
      },
    });
  }

  function onUseJudgeConfig() {
    // Clearing the override still auto-verifies: configured stays true via the
    // judge fallback, so the test confirms the fallback connection.
    saveAndTest(
      { key: "", base_url: "", model_name: "" },
      {
        onSaved: () => {
          setKey("");
          setBaseUrl("");
          setModelName("");
        },
      },
    );
  }

  return (
    <Card title="Terrain model" icon={<Map className="size-4" />}>
      {config.isLoading && (
        <div role="status" aria-label="Loading config…">
          <SkeletonRows count={3} />
        </div>
      )}
      {terrain && (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <span className="text-dense font-semibold text-heading">
              Terrain attribution model
            </span>
            <Badge variant={terrain.uses_judge_default ? "muted" : "info"}>
              {terrain.uses_judge_default ? "using judge config" : "custom"}
            </Badge>
          </div>
          <dl className="mb-3 space-y-2 text-dense">
            <div className="flex items-center justify-between gap-3">
              <dt className="text-text-tertiary">Current model</dt>
              <dd className="min-w-0 truncate text-right font-mono text-foreground">
                {terrain.model_name ?? "—"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-text-tertiary">Source</dt>
              <dd className="text-right text-foreground">
                {terrain.uses_judge_default ? "Judge model configuration" : "Custom override"}
              </dd>
            </div>
          </dl>
          <p className="mb-3 max-w-full break-words text-body text-text-secondary">
            Attributes each host and action on the terrain map to the agent step that caused it.
            Uses the judge model by default — set an override below only to attribute with a
            different model.
          </p>

          {/* Override — always shown (no collapse: a collapsed section just stranded empty space
              beside the taller Judge card). Fields follow the Judge card's order exactly: API key,
              Base URL, model name, token limit. */}
          <div className="border-t border-border pt-3">
            <span className="mb-2 block text-label uppercase text-text-tertiary">
              Override
            </span>
            <div className="space-y-2">
              <ApiKeyField
                configured={terrain.configured && !terrain.uses_judge_default}
                value={key}
                onChange={setKey}
              />
              <label className="block text-dense">
                <span className="mb-1 block text-text-tertiary">Base URL</span>
                <Input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder={terrain.base_url ?? "https://api.openai.com/v1"}
                />
              </label>
              <label className="block text-dense">
                <span className="mb-1 block text-text-tertiary">Terrain model name</span>
                <Input
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  placeholder={terrain.model_name ?? "gpt-4o-mini"}
                />
              </label>
              <div className="text-dense">
                <span className="mb-1 block text-text-tertiary">
                  Transcript token limit
                </span>
                <TokenLimitSlider
                  value={tokens ?? savedTerrainTokens}
                  onChange={setTokens}
                  disabled={phase === "saving"}
                />
                <span className="mt-1 block max-w-full break-words text-caption text-text-tertiary">
                  Per-call prompt cap for terrain attribution. Batches are shrunk to fit — lower
                  it for a small-context attribution model.
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-3">
                <Button onClick={onSave} disabled={busy}>
                  {phase === "saving" ? "Saving…" : "Save"}
                </Button>
                <Button
                  variant="outline"
                  onClick={testOnly}
                  disabled={busy || !terrain.configured}
                  title={
                    terrain.configured
                      ? "Call the terrain model to re-check the connection"
                      : "Configure a terrain or judge model first"
                  }
                >
                  Test
                </Button>
                <Button
                  variant="outline"
                  onClick={onUseJudgeConfig}
                  disabled={busy}
                  title="Clear the override and fall back to the judge model"
                >
                  Use judge config
                </Button>
                <ConnectionStatus phase={phase} message={message} />
              </div>
            </div>
          </div>
        </>
      )}
      {config.isError && <p className="text-dense text-err">Couldn&apos;t load config.</p>}
    </Card>
  );
}

/* ─────────────────────────── Environment ─────────────────────────── */

const DB_SCHEMA_LABEL: Record<string, { label: string; ok: boolean }> = {
  head: { label: "up to date", ok: true },
  behind: { label: "behind — migration needed", ok: false },
  fresh: { label: "fresh", ok: true },
  unknown: { label: "unknown", ok: false },
};

function EnvironmentCard() {
  const system = useSystem();
  const s = system.data;
  const db = s ? (DB_SCHEMA_LABEL[s.db_schema] ?? DB_SCHEMA_LABEL.unknown) : null;
  return (
    <Card title="Environment" icon={<HardDrive className="size-4" />}>
      {system.isLoading && (
        <div role="status" aria-label="Loading…">
          <SkeletonRows count={3} />
        </div>
      )}
      {s && (
        <div className="space-y-3 text-dense">
          <dl className="space-y-2">
            <Field label="Home">
              {/* §6: a long path is shown compactly (truncated + full value on hover) with a
                  copy button, rather than at the same prominence as the status values. */}
              <span className="flex items-center justify-end gap-1.5">
                <span
                  className="truncate font-mono text-foreground"
                  title={s.home || undefined}
                >
                  {s.home || "—"}
                </span>
                {s.home && (
                  <CopyButton
                    text={s.home}
                    idleLabel=""
                    copiedLabel=""
                    variant="ghost"
                    size="icon"
                    className="size-6 shrink-0"
                    aria-label="Copy home path"
                  />
                )}
              </span>
            </Field>
            <Field label="Role">
              {/* An experimental role is not just another config value — `role list` and the
                  boot warning both say so, and this readout must not be the one place that
                  presents it as ordinary. Role `all` is the complete install and stays plain. */}
              <span className="flex items-center justify-end gap-1.5">
                <Badge variant="muted">{s.role}</Badge>
                {s.role !== "all" && <Badge variant="warn">Experimental</Badge>}
              </span>
            </Field>
            <Field label="Topology">
              <Badge variant={s.topology === "distributed" ? "info" : "muted"}>
                {s.topology}
              </Badge>
            </Field>
            {/* No headscale_url / advertise_host echo here, deliberately. `xorcise up` WRITES
                both for the Headscale it provisions locally, so "a value is set" does not mean
                "distributed" — echoing them read a healthy single-host install as a remote
                deployment. The Modules card already reports where Headscale is AND whether it
                answers, which is the observed fact rather than a config value that cannot tell
                our own control plane from someone else's. */}
            <Field label="Database">
              <span className="flex items-center gap-2">
                {db && <Dot ok={db.ok} />}
                <span className="text-foreground">{db?.label}</span>
              </span>
            </Field>
          </dl>
          {s.db_url && (
            <p className="break-all font-mono text-dense text-text-tertiary">
              {s.db_url}
            </p>
          )}
          {s.db_schema === "behind" && (
            <p className="rounded-md border border-primary/30 bg-primary/10 px-2 py-2 text-dense text-foreground">
              Run <code className="text-primary">xorcise db upgrade</code> to migrate.
            </p>
          )}
        </div>
      )}
      {system.isError && <p className="text-dense text-err">Couldn&apos;t load environment.</p>}
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="whitespace-nowrap text-text-tertiary">{label}</dt>
      <dd className="min-w-0 break-words text-right">{children}</dd>
    </div>
  );
}

/* ─────────────────────────── Modules ─────────────────────────── */

/**
 * Modules, grouped under the SERVICE ROLE that owns them.
 *
 * Roles and modules are the same subject seen at two zoom levels: a role is the unit you
 * deploy (`xorcise serve --role <key>`), a module is the thing you can probe. Listing bare
 * plane keys made those look unrelated. Grouping them means the card doubles as the map of
 * what a distributed XORCISE would look like — the rows do not change when a role moves to
 * another machine, only its addresses do.
 */
function ModulesCard() {
  const system = useSystem();
  const groups = groupByRole(system.data?.planes ?? []);
  const role = system.data?.role;
  return (
    <Card title="Modules" icon={<Server className="size-4" />}>
      {system.isLoading && (
        <div role="status" aria-label="Probing modules…">
          <SkeletonRows count={3} />
        </div>
      )}
      {groups.length > 0 && (
        <>
          <p className="mb-3 max-w-full break-words text-body text-text-secondary">
            {role === "all"
              ? "This host runs every role in one process. Each module is probed independently."
              : `This host runs role “${role}”. Modules it does not run are shown as not deployed.`}
          </p>
          {/* ONE table for every group, not a table per group. Each group used to own its own
              <table>, so each computed column widths independently and the State / Address
              columns landed at a different x in every block — the card read as ragged even
              though the data was fine. A single table with one <colgroup> lines the whole card
              up; the role headings are full-width rows inside it. */}
          <table className="w-full table-fixed text-dense">
            <colgroup>
              <col />
              <col className="w-[7.5rem]" />
              <col className="w-[9.5rem]" />
            </colgroup>
            <tbody>
              {groups.map((group) => (
                <RoleModuleGroup key={group.role} group={group} />
              ))}
            </tbody>
          </table>
        </>
      )}
      {system.isError && <p className="text-dense text-err">Couldn&apos;t probe modules.</p>}
    </Card>
  );
}

/** A role heading row plus its module rows — a fragment, so every row shares the one table. */
function RoleModuleGroup({ group }: { group: RoleGroup }) {
  const children = visibleModules(group);
  // With no visible children the heading IS the module: it carries the state and address, so a
  // role never appears without a verdict just because its only row was redundant.
  const standalone = children.length === 0;
  const address = roleAddress(group);
  const tone =
    group.state === "ok" ? "text-ok" : group.state === "down" ? "text-err" : "text-text-tertiary";
  return (
    <>
      <tr title={roleTooltip(group)} aria-label={roleTooltip(group)}>
        <th scope="row" className="pb-1 pt-4 text-left first:pt-0">
          <span className="flex items-center gap-2">
            <StateDot state={group.state} />
            {/* The role is the heavier line: it is the unit you DEPLOY, and the modules under
                it are what that unit is made of.
                It was set at text-label (10px) — SMALLER than the 12px module names nested
                beneath it, so the parent read as subordinate to its own children and the
                grouping was hard to see. It now matches the modules in size and wins on the
                axes that don't cost vertical space: caps, tracking, weight and heading colour. */}
            <span className="whitespace-nowrap text-dense font-bold uppercase tracking-[0.16em] text-heading">
              {group.label}
            </span>
          </span>
        </th>
        <td className={"pb-1 pt-4 first:pt-0 " + (standalone ? tone : "")}>
          {standalone ? groupStateLabel(group) : ""}
        </td>
        <td className="pb-1 pt-4 text-right font-mono text-caption text-text-secondary first:pt-0">
          {standalone ? address || "—" : ""}
        </td>
      </tr>
      {children.map((p) => (
        <ModuleRow key={p.name} plane={p} />
      ))}
    </>
  );
}

/** The phrase for a whole role's state, used when the heading stands in for its only module. */
function groupStateLabel(group: RoleGroup): string {
  if (group.state === "ok") return "Healthy";
  if (group.state === "not_deployed") return "Not on this host";
  return "Down";
}

/** Three-state presence dot — absent is dimmed, never red (see module-groups.ts). */
function StateDot({ state }: { state: ModuleState }) {
  const cls =
    state === "ok"
      ? "bg-ok"
      : state === "down"
        ? "bg-err"
        : "bg-text-tertiary/40";
  return <span className={`inline-block size-2 rounded-full ${cls}`} aria-hidden />;
}

function ModuleRow({ plane }: { plane: PlaneStatus }) {
  const state = planeState(plane);
  const tone =
    state === "ok" ? "text-ok" : state === "down" ? "text-err" : "text-text-tertiary";
  return (
    <tr className="border-t border-border/50">
      {/* Modules are indented under their role so the nesting is visible without a second box. */}
      <td className="py-1.5 pl-3.5">
        <span className="flex items-center gap-2">
          <StateDot state={state} />
          {/* The human name ("REST API"), matching the words the CLI uses for the same plane;
              the raw key stays in the JSON contract. Regular weight against the role's bold
              caps above it — same size, clearly the child. */}
          <span className="truncate font-normal text-text-secondary">
            {moduleLabel(plane)}
          </span>
        </span>
      </td>
      <td className={"py-1.5 " + tone}>{moduleStateLabel(plane)}</td>
      <td className="py-1.5 text-right font-mono text-caption text-text-secondary">
        {plane.location || "—"}
      </td>
    </tr>
  );
}

/* ─────────────────── Distributed deployment (preview) ─────────────────── */

/**
 * Distributed deployment, as a preview rather than a control.
 *
 * The card that used to sit here offered two fields — Headscale URL and advertise host — for a
 * deployment model that needs far more than two. It read as a finished feature that was really a
 * stub, and filling it in pointed a local-only server at a control plane that was not there,
 * which broke the next `xorcise up`. That is why it was withdrawn, and none of it has stopped
 * being true: this card still sets nothing.
 *
 * What the withdrawal also removed was any sign the capability exists, which teaches an operator
 * that XORCISE is single-host by design. So it returns carrying the SHAPE of the feature and
 * nothing that can be typed into: no inputs, no mutation, nothing to misconfigure. The one real
 * way in — `xorcise config set-network` — is named along with its hazard, because it exists
 * whether or not this page admits it, and an unlabelled CLI path is how the surfaces disagreed
 * last time.
 */
function DistributedCard() {
  return (
    <Card
      title="Distributed deployment"
      icon={<Network className="size-4" />}
      className="lg:col-span-2"
    >
      <ComingSoonPanel
        headline="One XORCISE, many machines."
        steps={[
          { icon: <Server />, label: "Pick a host" },
          { icon: <Network />, label: "Join the tailnet" },
          { icon: <Check />, label: "Run remotely" },
        ]}
      >
        Push the runner, collector and control plane onto hosts of their own — wired into
        a single tailnet automatically, so a mission runs wherever the capacity is.
      </ComingSoonPanel>

      {/* Deliberately UNCAPPED, unlike the panel's prose. A 68ch cap here stacked one
          sentence into three short lines against a 1300px card, which read as a truncated
          column rather than a footnote. A single closing line under a full-width card is the
          exception the measure rule is built to allow: it is one sentence the eye crosses
          once, not a paragraph it has to navigate. `dense` over `caption` for the same
          reason — 12px/500 rather than 11px/400 — still quieter than the panel's body. */}
      <p className="mt-4 break-words text-dense text-text-tertiary">
        Today every module runs on this host — Modules shows each one. The addresses are
        reachable from{" "}
        <code className="text-text-secondary">xorcise config set-network</code>, which is
        experimental and can leave the server unable to start a run.
      </p>
    </Card>
  );
}

/* ─────────────────────────── Catalog (remote library) ─────────────────────────── */

function CatalogCard() {
  const config = useConfig();
  const setConnected = useSetCatalogConnected();
  const catalog = config.data?.catalog;
  const connected = setConnected.isPending
    ? (setConnected.variables as boolean)
    : (catalog?.connected ?? false);

  return (
    <Card title="XORCISE Remote" icon={<BookMarked className="size-4" />}>
      {config.isLoading && (
        <div role="status" aria-label="Loading…">
          <SkeletonRows count={3} />
        </div>
      )}
      {catalog && (
        <>
          <div className="mb-3 flex items-center justify-between gap-3">
            <p className="flex items-center gap-2 text-dense">
              <Dot ok={connected} />
              <span className="font-semibold text-heading">
                {connected ? "Connected" : "Disconnected"}
              </span>
            </p>
            <Switch
              checked={connected}
              onChange={(next) => setConnected.mutate(next)}
              disabled={setConnected.isPending}
              label="Connect XORCISE remote catalog"
            />
          </div>
          <p className="mb-2 max-w-full break-words text-body text-text-secondary">
            The free XORCISE mission library. When connected, remote missions appear in
            the catalog and can be pulled on demand (including at run start).
          </p>
          {catalog.url && (
            <p className="truncate font-mono text-dense text-text-tertiary" title={catalog.url}>
              {catalog.url}
            </p>
          )}
          {setConnected.isError && (
            <p className="mt-2 text-dense text-err">Couldn&apos;t change the connection.</p>
          )}
        </>
      )}
      {config.isError && <p className="text-dense text-err">Couldn&apos;t load catalog config.</p>}
    </Card>
  );
}

/* ─────────────────────────── Live view ─────────────────────────── */

/** Range of the agent-inactivity threshold: 30s (very chatty harnesses) to 10 min (agents that
 *  legitimately sit in long tool calls — nmap sweeps, hashcat, slow scans). */
const STALL_MIN_S = 30;
const STALL_MAX_S = 600;
const STALL_STEP_S = 30;

/** Operator preference for the live-run monitoring surfaces. A DISPLAY-plane setting: it decides
 *  when this browser warns, never how the server runs the run — so it lives in localStorage with
 *  the other shell preferences, not in the server's guarded config surface. */
function LiveViewCard() {
  const threshold = useUiStore((s) => s.stallThresholdSeconds);
  const setThreshold = useUiStore((s) => s.setStallThresholdSeconds);
  return (
    <Card title="Live view" icon={<Activity className="size-4" />}>
      <div className="text-dense">
        <span className="mb-1.5 block text-text-tertiary">Agent inactivity warning</span>
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          <span className="text-body font-medium text-heading">
            {formatDuration(threshold)}
          </span>
          <span className="text-caption text-text-tertiary">
            range {formatDuration(STALL_MIN_S)}–{formatDuration(STALL_MAX_S)}
          </span>
        </div>
        <Slider
          ariaLabel="Agent inactivity warning threshold"
          min={STALL_MIN_S}
          max={STALL_MAX_S}
          step={STALL_STEP_S}
          value={Math.min(STALL_MAX_S, Math.max(STALL_MIN_S, threshold))}
          onChange={setThreshold}
        />
        <span className="mt-1.5 block max-w-full break-words text-caption leading-relaxed text-text-tertiary">
          Warn when a live run stops exporting OTel telemetry for this long — the agent may have
          crashed or disconnected while the run silently burns its budget. Shown on the live run
          page and as a notification from any page. Raise it for agents that sit in long tool
          calls. Stored in this browser.
        </span>
      </div>
    </Card>
  );
}

/* ─────────────────────────── Connection ─────────────────────────── */

/**
 * How this interface reaches the server, and who is allowed to.
 *
 * "XORCISE.CORE API base" named the product twice and never said what the value was FOR. It is
 * the endpoint this UI sends every request to, so it is labelled that. Access mode is the second
 * half of the same subject — the reason no login stands between the two — and the mode that is
 * NOT yet available is listed rather than left to be guessed at: multi-user auth is explicitly
 * out of scope today, so saying so here is
 * cheaper than an operator hunting for a settings page that does not exist.
 */
function ConnectionCard() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return (
    <Card title="Connection" icon={<Plug className="size-4" />}>
      <dl className="space-y-2 text-dense">
        <Field label="API endpoint">
          <span className="flex items-center justify-end gap-1.5">
            <span className="truncate font-mono text-foreground">
              {mounted ? apiBaseUrl() : "…"}
            </span>
            {mounted && (
              <CopyButton
                text={apiBaseUrl()}
                idleLabel=""
                copiedLabel=""
                variant="ghost"
                size="icon"
                className="size-6 shrink-0"
                aria-label="Copy the API endpoint"
              />
            )}
          </span>
        </Field>
        <Field label="Interface">
          <span className="text-foreground">This browser · same host</span>
        </Field>
      </dl>

      <p className="mt-3 max-w-full break-words text-body text-text-secondary">
        Every request on this page goes to that endpoint. The server binds to loopback, so it is
        reachable only from this machine — which is why no sign-in stands in front of it.
      </p>

      <div className="mt-3 space-y-2 border-t border-border pt-3">
        <span className="block text-label uppercase text-text-tertiary">
          Access mode
        </span>
        <div className="flex items-center justify-between gap-3">
          <span className="text-dense text-foreground">Single operator · local trust</span>
          <Badge variant="ok">Active</Badge>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-dense text-text-tertiary">Multi-user · sign-in and roles</span>
          <Badge variant="muted">Coming soon</Badge>
        </div>
      </div>
    </Card>
  );
}

/* ─────────────────────────── Page ─────────────────────────── */

/** A labelled band of settings cards. `aria-label` makes it a landmark region so
 *  the section reads to assistive tech (and lets tests assert card grouping). The
 *  grid stays `lg:grid-cols-2`, and honours `lg:col-span-2` on any card that needs
 *  the full width. */
function SettingsGroup({
  title,
  caption,
  children,
}: {
  title: string;
  caption: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-label={title} className="min-w-0 space-y-3">
      <div className="flex items-baseline gap-3">
        <h2 className="whitespace-nowrap text-label uppercase text-heading">
          {title}
        </h2>
        <span className="whitespace-nowrap text-label uppercase text-text-tertiary">
          {caption}
        </span>
        <div className="h-px flex-1 bg-border" aria-hidden />
      </div>
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">{children}</div>
    </section>
  );
}

export function SettingsView() {
  const [focusJudge, setFocusJudge] = useState(false);
  useEffect(() => {
    setFocusJudge(new URLSearchParams(window.location.search).get("focus") === "judge");
  }, []);

  return (
    <div className="min-w-0 space-y-8 p-6">
      <h1 className="text-lead text-heading">Settings</h1>

      {/* Evaluation — the models that grade and attribute a run (§6). */}
      <SettingsGroup title="Evaluation" caption="how runs are graded">
        <JudgeModelCard highlight={focusJudge} />
        <TerrainModelCard />
      </SettingsGroup>

      {/* Mission Library & Runtime — where missions come from and where
          XORCISE.CORE runs (§6). */}
      <SettingsGroup title="Mission Library & Runtime" caption="catalog & environment">
        <CatalogCard />
        <EnvironmentCard />
      </SettingsGroup>

      {/* Interface — this browser's operator preferences (client-side, localStorage). */}
      <SettingsGroup title="Interface" caption="operator preferences">
        <LiveViewCard />
      </SettingsGroup>

      {/* Diagnostics — read-only system health & connection (§6), then what this host
          CANNOT do yet. Distributed deployment sits last on the page, under Modules and
          Connection, and that ordering is the argument: those two report the topology this
          host actually has, so the preview of the topology it does not have reads as their
          continuation rather than as a control someone forgot to finish. Nothing above it
          is pushed down by a card that cannot be acted on. */}
      <SettingsGroup title="Diagnostics" caption="system health & connection">
        <ModulesCard />
        <ConnectionCard />
        <DistributedCard />
      </SettingsGroup>
    </div>
  );
}
