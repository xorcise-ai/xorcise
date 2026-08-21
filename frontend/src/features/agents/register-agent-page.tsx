"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Loader2, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/components/ui/cn";
import { ApiError } from "@/lib/api/client";
import type { AgentEntry, HarnessDescriptor } from "@/lib/api/types";
import { useAgents, useRegisterAgent, useUpdateAgent } from "./queries";
import { HarnessSelector } from "./harness-selector";
import { HarnessProfile, HarnessReviewFacts } from "./harness-profile";
import { HARNESSES } from "./harnesses";
import { useHarnessDescriptors } from "./use-harnesses";

/** Multiline launch fields are edited one item per line; blank lines are ignored. */
function launchLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Shell-quote a disclosed model before previewing the provider-owned CLI flag. */
function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./:@%+=,-]+$/.test(value)
    ? value
    : `'${value.replaceAll("'", "'\"'\"'")}'`;
}

/** Insert a provider-owned model option before the mission, even in a multiline template. */
function withModel(
  launch: HarnessDescriptor["launch"],
  model: string,
): HarnessDescriptor["launch"] {
  const selected = model.trim();
  if (!launch.command_template || !launch.model_flag || !selected) return launch;
  const option = `${launch.model_flag} ${shellQuote(selected)}`;
  const anchor = launch.model_flag_anchor || "{mission}";
  const command = launch.command_template.includes(anchor)
    ? launch.command_template.replace(anchor, `${option} ${anchor}`)
    : `${launch.command_template.trimEnd()} ${option}`;
  return { ...launch, command_template: command };
}

/**
 * Register a new agent, or — when `editName` names an existing one — edit it (CLI parity:
 * `agent update`). Replaces the register/edit dialog: the harness capability matrix no longer
 * fits a modal, so this is a full page.
 *
 * LAYOUT: a guided three-column builder on laptop/desktop widths. Column 1 establishes identity
 * and harness capability; column 2 contains optional model metadata and launch conditions;
 * column 3 is the stable review endpoint. The form
 * itself never scrolls at `lg` — each bounded column can scroll independently only when an
 * operator expands supporting detail. Narrow screens retain one natural document scroller.
 *
 * MODE: `editName=null` → register (POST via `useRegisterAgent`). A name → edit mode, seeded
 * from `useAgents().data` once it loads (a stable-sized skeleton renders meanwhile — never an
 * empty form). PUT via `useUpdateAgent` routes on the agent's CURRENT name; a changed Name
 * renames it (history survives). An `editName` that matches no loaded agent falls back to
 * register mode with a dismissible notice, rather than a dead-end blank edit.
 *
 * SEEDING: the form fields live in `AgentForm`, keyed on `agent?.name ?? "new"` — the key
 * remount (not a post-mount effect) is what seeds them, so the very first paint of an edit
 * target already has the right values (no one-frame blank/generic flash). `agent` itself is
 * FROZEN once a save is in flight (`savedRef`): the mutation's list invalidation is awaited
 * before `mutateAsync` resolves, so a rename can retire the current name out from under this
 * page before `router.push` lands — without freezing, that refetch would flip `agent` to
 * `undefined`, remount `AgentForm` back to a blank register form, and flash the "no agent
 * named…" notice while navigation is still in flight.
 *
 * NAVIGATION is deterministic on save (never `router.back()`, which would land on a stale
 * old-name URL after a rename): register → `/agents`; edit → `/agents/detail?name=<saved
 * entry.name>` — the name the server actually saved, so a rename lands on the new address.
 */
export function RegisterAgentPage({ editName }: { editName: string | null }) {
  const router = useRouter();
  const agents = useAgents();
  const rawAgent = editName ? agents.data?.find((a) => a.name === editName) : undefined;
  const editLoading = !!editName && agents.isLoading;

  // See the docstring's SEEDING note: freeze `agent` at its last known value once a save has
  // started, so an awaited post-save refetch that retires the current name can't flip the page
  // into "unknown agent" while `router.push` is in flight.
  const savedRef = useRef(false);
  const frozenAgentRef = useRef<AgentEntry | undefined>(rawAgent);
  if (!savedRef.current) frozenAgentRef.current = rawAgent;
  const agent = frozenAgentRef.current;

  const editing = !!agent;
  const unknownEdit = !savedRef.current && !!editName && !!agents.data && !agent;
  const [noticeDismissed, setNoticeDismissed] = useState(false);

  // Match the root layout's `%s · XORCISE` title template — edit mode can't rely on route
  // metadata (the name only resolves client-side, from `?agent=`), so it's set here instead.
  useEffect(() => {
    if (editing && agent) document.title = `Edit ${agent.name} · XORCISE`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, agent?.name]);

  if (editLoading) {
    return (
      <div className="flex h-full min-h-0 flex-col gap-3 p-4">
        <div className="shrink-0">
          <h1 className="text-lead text-heading">Edit agent</h1>
          <p className="max-w-[68ch] text-dense text-text-secondary">
            Loading “{editName}”…
          </p>
        </div>
        <div
          role="status"
          aria-label="Loading agent…"
          className="grid grid-cols-1 min-h-0 flex-1 gap-3 xl:grid-cols-3"
        >
          <Skeleton className="h-full min-h-48" />
          <Skeleton className="h-full min-h-48" />
          <Skeleton className="h-full min-h-48" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      <div className="shrink-0">
        <h1 className="text-lead text-heading">
          {editing ? `Edit “${agent?.name}”` : "Register agent"}
        </h1>
        <p className="max-w-[68ch] text-dense text-text-secondary">
          {editing
            ? "Update this agent's declaration. A changed name renames it — its history stays attached."
            : "Name the agent, choose how it is observed and launched, then review the declaration."}
        </p>
        {unknownEdit && !noticeDismissed && (
          <div className="mt-1 flex max-w-[68ch] items-start justify-between gap-2">
            <p className="text-dense text-text-secondary">
              No agent named “{editName}” — registering a new agent instead.
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="Dismiss notice"
              onClick={() => setNoticeDismissed(true)}
            >
              <X className="size-4" />
            </Button>
          </div>
        )}
      </div>

      <AgentForm
        key={agent?.name ?? "new"}
        agent={agent}
        editing={editing}
        onSaved={() => {
          savedRef.current = true;
        }}
        onNavigate={(href) => router.push(href)}
      />
    </div>
  );
}

/** The form itself — remounted (via the parent's `key={agent?.name ?? "new"}`) whenever the
 *  EDIT TARGET changes, which is what seeds `useState` from `agent` correctly on first paint
 *  (no post-mount reseed effect to race). A background refetch that hands back a
 *  structurally-equal-but-referentially-new `agent` for the SAME name does not change the key,
 *  so in-progress typing survives it — only a genuine target change (or the parent freezing
 *  `agent` at "new" going to a real name, or vice versa) remounts. */
function AgentForm({
  agent,
  editing,
  onSaved,
  onNavigate,
}: {
  agent: AgentEntry | undefined;
  editing: boolean;
  onSaved: () => void;
  onNavigate: (href: string) => void;
}) {
  const [name, setName] = useState(agent?.name ?? "");
  // Agent files were never consumed by XORCISE. Preserve a legacy value on edit so removing
  // the unused picker is not destructive, but do not ask new operators for dead metadata.
  const [endpoint] = useState(agent?.endpoint ?? "");
  // Known harnesses derive telemetry from their launch provider. Preserve a legacy saved value
  // on edit so removing the old OTel field from this page is not destructive.
  const [otel] = useState(agent?.otel ?? "");
  const [model, setModel] = useState(agent?.model ?? "");
  // No harness is pre-selected for a NEW agent — the operator picks one deliberately (blank →
  // generic adapter). Only an existing agent seeds its saved kind.
  const [kind, setKind] = useState(agent?.kind ?? "");
  const [launchEditorOpen, setLaunchEditorOpen] = useState(false);
  const [launchCustomized, setLaunchCustomized] = useState(
    agent?.launch_command_template != null ||
      agent?.launch_tips != null ||
      agent?.mission_preamble != null,
  );
  const [launchCommand, setLaunchCommand] = useState<string | null>(
    agent?.launch_command_template ?? null,
  );
  const [launchTips, setLaunchTips] = useState<string | null>(
    agent?.launch_tips?.join("\n") ?? null,
  );
  const [missionPreamble, setMissionPreamble] = useState<string | null>(
    agent?.mission_preamble?.join("\n") ?? null,
  );
  const [launchMode, setLaunchMode] = useState<"host" | "container" | null>(
    agent?.launch_mode ?? null,
  );
  const [draftLaunchCommand, setDraftLaunchCommand] = useState("");
  const [draftLaunchTips, setDraftLaunchTips] = useState("");
  const [draftMissionPreamble, setDraftMissionPreamble] = useState("");
  const [draftLaunchMode, setDraftLaunchMode] = useState<
    "host" | "container" | null
  >(agent?.launch_mode ?? null);
  // Inline validation (§8): flag the required Name field once it's been touched / a submit was
  // attempted, so the message doesn't shout at a pristine form.
  const [nameTouched, setNameTouched] = useState(false);
  const register = useRegisterAgent();
  const update = useUpdateAgent();
  const mutation = editing ? update : register;
  const nameInvalid = !name.trim();
  const showNameError = nameTouched && nameInvalid;
  const harnesses = useHarnessDescriptors();
  const descriptor = harnesses.byKind.get(
    HARNESSES.some((h) => h.kind === kind) ? kind : "generic",
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (nameInvalid) {
      setNameTouched(true);
      return;
    }
    const decl = {
      name: name.trim(),
      endpoint: endpoint.trim() || null,
      otel: otel.trim() || null,
      model: model.trim() || null,
      kind: kind.trim() || null,
      launch_command_template: launchCustomized
        ? (launchCommand ?? descriptor?.launch.command_template ?? null)
        : null,
      launch_tips: launchCustomized
        ? launchLines(launchTips ?? descriptor?.launch.tips.join("\n") ?? "")
        : null,
      mission_preamble: launchCustomized
        ? launchLines(
            missionPreamble ?? descriptor?.launch.mission_preamble.join("\n") ?? "",
          )
        : null,
      launch_mode: launchMode,
    };
    let entry: AgentEntry;
    try {
      // The PUT routes on the agent's CURRENT name; a differing decl.name renames it.
      entry = agent
        ? await update.mutateAsync({ name: agent.name, decl })
        : await register.mutateAsync(decl);
    } catch {
      // Failure is surfaced via mutation.isError below; stay on the page so the
      // operator can retry without re-entering everything.
      return;
    }
    // Latch BEFORE navigating — see RegisterAgentPage's docstring: the list invalidation above
    // already resolved (mutateAsync awaits it), so the parent may already see a stale `agent`
    // lookup for the OLD name on the next render; this tells it to freeze rather than flip.
    onSaved();
    onNavigate(
      editing
        ? `/agents/detail?name=${encodeURIComponent(entry.name)}`
        : "/agents",
    );
  }

  const isBuiltIn = HARNESSES.some((h) => h.kind === kind);
  const harnessName = kind.trim()
    ? (isBuiltIn ? HARNESSES.find((h) => h.kind === kind)!.name : `Custom (${kind.trim()})`)
    : null;

  const modelSuggestions = descriptor?.model_hints ?? [];
  const providerLaunch = descriptor ? withModel(descriptor.launch, model) : undefined;
  const effectiveLaunch = providerLaunch
    ? {
        ...providerLaunch,
        command_template: launchCustomized
          ? (launchCommand ?? providerLaunch.command_template)
          : providerLaunch.command_template,
        tips: launchCustomized
          ? launchLines(launchTips ?? providerLaunch.tips.join("\n"))
          : providerLaunch.tips,
        mission_preamble: launchCustomized
          ? launchLines(
              missionPreamble ?? providerLaunch.mission_preamble.join("\n"),
            )
          : providerLaunch.mission_preamble,
      }
    : undefined;
  const providerLaunchMode = providerLaunch?.launch_modes.includes("container")
    ? "container"
    : "host";
  const effectiveLaunchMode = launchMode ?? providerLaunchMode;
  const hasLaunchOverrides = launchCustomized || launchMode !== null;

  function openLaunchEditor() {
    setDraftLaunchCommand(effectiveLaunch?.command_template ?? "");
    setDraftLaunchTips(effectiveLaunch?.tips.join("\n") ?? "");
    setDraftMissionPreamble(effectiveLaunch?.mission_preamble.join("\n") ?? "");
    setDraftLaunchMode(launchMode);
    setLaunchEditorOpen(true);
  }

  return (
    <>
      <form
        onSubmit={submit}
        className="grid grid-cols-1 min-h-0 flex-1 auto-rows-min gap-3 overflow-y-auto xl:auto-rows-auto xl:grid-cols-3 xl:items-stretch xl:overflow-hidden"
      >
        <section aria-label="Agent identity and harness" className="flex min-h-0 flex-col gap-3 xl:h-full">
          <Step
            step={1}
            title="Name the agent"
            className="shrink-0"
          >
            <Field
              label="Name"
              required
              helpText="A unique handle used to select this agent when creating a run."
            >
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => setNameTouched(true)}
                placeholder="scout"
                autoFocus={!editing}
                aria-invalid={showNameError || undefined}
                aria-describedby={showNameError ? "agent-name-error" : undefined}
              />
              {showNameError && (
                <p id="agent-name-error" role="alert" className="mt-1 text-dense text-err">
                  Name is required.
                </p>
              )}
              {editing && (
                <p className="mt-1 max-w-[68ch] text-dense text-text-tertiary">
                  Renaming keeps the agent’s history — runs stay attached.
                </p>
              )}
            </Field>
          </Step>

          <Step
            step={2}
            title="Choose a harness"
            className="min-h-0 flex-1 xl:overflow-hidden"
            bodyClassName="flex flex-col gap-3 xl:overflow-y-auto xl:px-1"
          >
            <HarnessSelector value={kind} onChange={setKind} compact />
            <div className="min-h-0 flex-1 border-t border-border pt-3">
              <HarnessProfile kind={kind} section="visibility" />
            </div>
          </Step>
        </section>

        <section aria-label="Harness and launch configuration" className="flex min-h-0 flex-col gap-3 xl:h-full">
          <Step
            step={3}
            title="Model config"
            className="shrink-0"
          >
            <Field
              label="Model"
              optional
              helpText={
                descriptor?.launch.model_flag
                  ? `Updates the launch command using ${descriptor.launch.model_flag}.`
                  : "Recorded as disclosed context; this harness does not expose a model flag."
              }
            >
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={
                  modelSuggestions[0] ? `e.g. ${modelSuggestions[0]}` : "Enter the disclosed model"
                }
                list="agent-model-suggestions"
              />
              {modelSuggestions.length > 0 && (
                <>
                  <datalist id="agent-model-suggestions">
                    {modelSuggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
                  </datalist>
                  <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Suggested models">
                    {modelSuggestions.map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => setModel(suggestion)}
                        className="rounded-md border border-border px-2 py-1 text-dense text-text-secondary transition-colors hover:border-border-hover hover:text-foreground"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </Field>
          </Step>

          <Step
            step={4}
            title="Launch config"
            className="min-h-0 flex-1 xl:overflow-hidden"
            bodyClassName="flex min-h-0 flex-col xl:overflow-y-auto xl:pr-1"
          >
            <HarnessProfile
              kind={kind}
              section="launch"
              launchPreview={effectiveLaunch}
              launchCustomized={hasLaunchOverrides}
              launchFooter={
                <div className="flex items-center justify-between gap-3">
                  <p className="text-caption text-text-tertiary">
                    {hasLaunchOverrides
                      ? "Agent-specific launch overrides are active."
                      : "Inheriting the harness provider defaults."}
                  </p>
                  <div className="flex shrink-0 items-center gap-1">
                    {hasLaunchOverrides && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setLaunchCustomized(false);
                          setLaunchCommand(null);
                          setLaunchTips(null);
                          setMissionPreamble(null);
                          setLaunchMode(null);
                        }}
                      >
                        Reset to harness defaults
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={openLaunchEditor}
                    >
                      {hasLaunchOverrides ? "Edit overrides" : "Customize"}
                    </Button>
                  </div>
                </div>
              }
            />
          </Step>
        </section>

        <aside aria-label="Review and register" className="min-h-0 xl:h-full">
          <Step
            step={5}
            title={editing ? "Review & save" : "Review & register"}
            bodyClassName="flex flex-col gap-4 xl:overflow-y-auto xl:pr-1"
            className="xl:h-full xl:overflow-hidden"
          >
            <div>
              <p className="text-label uppercase text-text-tertiary">Agent summary</p>
              <dl className="mt-2 space-y-2 text-dense">
                <ReviewRow label="Name" value={name.trim() || null} />
                <ReviewRow
                  label="Harness"
                  value={harnessName}
                  placeholder="Generic (no harness selected)"
                />
                <ReviewRow
                  label="Model"
                  value={model.trim() || null}
                  placeholder="Not disclosed"
                />
                <ReviewRow
                  label="Launch settings"
                  value={hasLaunchOverrides ? "Agent override" : "Harness defaults"}
                />
                <ReviewRow
                  label="Address context"
                  value={
                    effectiveLaunchMode === "host"
                      ? "Local host · loopback"
                      : "Container · reachable host"
                  }
                />
              </dl>
            </div>

            <dl className="space-y-3 border-t border-border pt-3">
              <HarnessReviewFacts kind={kind} />
            </dl>

            <p className="border-t border-border pt-3 text-caption text-text-tertiary">
              The runnable launch command will be generated when a run is created.
            </p>

            {mutation.isError && (
              <p role="alert" className="text-body text-err">
                {mutation.error instanceof ApiError && mutation.error.status === 409
                  ? "That name is already taken."
                  : editing
                    ? "Couldn’t save changes. Please try again."
                    : "Couldn’t register the agent. Please try again."}
              </p>
            )}

            <Button
              type="submit"
              className="mt-auto w-full"
              disabled={nameInvalid || mutation.isPending}
            >
              {mutation.isPending && (
                <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
              )}
              {mutation.isPending
                ? editing
                  ? "Saving…"
                  : "Registering…"
                : editing
                  ? "Save changes"
                  : "Register agent"}
            </Button>
          </Step>
        </aside>
      </form>

      <Dialog
        open={launchEditorOpen}
        onClose={() => setLaunchEditorOpen(false)}
        title="Customize launch profile"
        size="lg"
      >
        <div className="-m-1 max-h-[70vh] space-y-4 overflow-y-auto p-1">
          <LaunchModeEditor
            value={draftLaunchMode}
            providerDefault={providerLaunchMode}
            onChange={setDraftLaunchMode}
          />
          <LaunchTextarea
            label="Launch command template"
            value={draftLaunchCommand}
            onChange={setDraftLaunchCommand}
            rows={3}
            helpText="Supported placeholders: {mission}, {otlp_traces_endpoint}, {otlp_logs_endpoint}."
          />
          <LaunchTextarea
            label="Agent-specific tips"
            value={draftLaunchTips}
            onChange={setDraftLaunchTips}
            rows={3}
            helpText="One tip per line. Leave empty to suppress provider tips."
          />
          <LaunchTextarea
            label="Extra mission preamble"
            value={draftMissionPreamble}
            onChange={setDraftMissionPreamble}
            rows={4}
            helpText="One instruction per line. Added to every new run mission for this agent."
          />
        </div>
        <div className="mt-4 flex justify-end gap-2 border-t border-border pt-4">
          <Button
            type="button"
            variant="ghost"
            onClick={() => setLaunchEditorOpen(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={() => {
              setLaunchCommand(draftLaunchCommand);
              setLaunchTips(draftLaunchTips);
              setMissionPreamble(draftMissionPreamble);
              setLaunchCustomized(true);
              setLaunchMode(draftLaunchMode);
              setLaunchEditorOpen(false);
            }}
          >
            Apply overrides
          </Button>
        </div>
      </Dialog>
    </>
  );
}

function LaunchModeEditor({
  value,
  providerDefault,
  onChange,
}: {
  value: "host" | "container" | null;
  providerDefault: "host" | "container";
  onChange: (mode: "host" | "container" | null) => void;
}) {
  const effectiveMode = value ?? providerDefault;
  const options: readonly {
    value: "host" | "container" | null;
    label: string;
  }[] = [
    {
      value: null,
      label: `Harness default (${providerDefault})`,
    },
    { value: "host", label: "Local host" },
    { value: "container", label: "Container" },
  ];

  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-label uppercase text-text-tertiary">
          Agent execution location
        </h3>
        <span className="text-caption text-text-tertiary">
          Controls generated run-control and telemetry addresses
        </span>
      </div>
      <div
        role="radiogroup"
        aria-label="Agent execution context"
        className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3"
      >
        {options.map((option) => {
          const selected = option.value === value;
          return (
            <button
              key={option.label}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(option.value)}
              className={cn(
                "rounded-md border px-3 py-2 text-dense transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/30",
                selected
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-input bg-deepest text-text-secondary hover:border-primary/60 hover:text-foreground",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      <p className="mt-2 text-caption text-text-tertiary">
        {effectiveMode === "host"
          ? "Local host uses loopback addresses for an agent command running on the same machine as Xorcise."
          : "Container uses container-reachable addresses for an agent command launched inside a container."}
      </p>
    </section>
  );
}

/** Numbered step header — the visual idiom shared by every step on this page (copied from
 *  `NewRunForm`'s `Step`), so columns read as one rhythm across both pages. The amber chip
 *  carries the step number; the heading text is the title ALONE (no duplicated numeral). */
function StepHeader({ step, title }: { step: number; title: string }) {
  return (
    <div className="flex shrink-0 items-center gap-2">
      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-caption font-semibold text-primary">
        {step}
      </span>
      <h2 className="text-body font-bold text-heading">{title}</h2>
    </div>
  );
}

/** A numbered step as its own Card in the guided setup + review flow. */
function Step({
  step,
  title,
  bodyClassName,
  className,
  children,
}: {
  step: number;
  title: string;
  bodyClassName?: string;
  /** Extra classes on the Card — the review uses sticky positioning on wide screens. */
  className?: string;
  children: ReactNode;
}) {
  return (
    <Card className={cn("flex min-h-0 flex-col p-3", className)}>
      <div className="mb-2">
        <StepHeader step={step} title={title} />
      </div>
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </Card>
  );
}

function Field({
  label,
  required = false,
  optional = false,
  helpText,
  children,
}: {
  label: string;
  required?: boolean;
  optional?: boolean;
  helpText?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="block">
        <span className="mb-1 block text-label uppercase text-text-tertiary">
          {label}
          {required && <span className="text-err"> *</span>}
          {optional && (
            <span className="ml-1 normal-case tracking-normal text-text-tertiary">
              (optional)
            </span>
          )}
        </span>
        {children}
      </label>
      {helpText && (
        <p className="mt-1 max-w-[68ch] text-dense text-text-tertiary">{helpText}</p>
      )}
    </div>
  );
}

function LaunchTextarea({
  label,
  value,
  onChange,
  rows,
  helpText,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows: number;
  helpText: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-label uppercase text-text-tertiary">{label}</span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={rows}
        className="w-full resize-y rounded-md border border-input bg-deepest px-2.5 py-2 font-mono text-dense text-foreground placeholder:text-text-tertiary focus-visible:border-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/30"
      />
      <span className="mt-1 block text-caption text-text-tertiary">{helpText}</span>
    </label>
  );
}

function ReviewRow({
  label,
  value,
  placeholder = "Not selected",
}: {
  label: string;
  value: string | null;
  placeholder?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-text-tertiary">{label}</dt>
      <dd
        className={
          value
            ? "min-w-0 truncate text-right font-medium text-heading"
            : "text-right text-text-tertiary"
        }
        title={value ?? undefined}
      >
        {value ?? placeholder}
      </dd>
    </div>
  );
}
