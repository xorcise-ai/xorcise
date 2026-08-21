"use client";

import { useState } from "react";
import { Terminal, Radio, Check, ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CopyButton } from "@/components/ui/copy-button";
import { SkeletonRows } from "@/components/ui/skeleton";
import {
  useRunPrompt,
  useRunLaunchProfile,
  type LaunchMode,
} from "@/features/runs/queries";

/** Render the launch-profile env as dotenv KEY=VALUE lines (matches `run launch-profile`). */
function toDotenv(env: Record<string, string>): string {
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

/** Payload blocks WRAP — they never scroll sideways. A launch command is a copy-paste payload
 *  (one line can run 800+ chars), so a horizontal axis buys nothing and steals the wheel from
 *  the column's single scroller. No max-height either: the card body owns the one scroller. */
const PRE_CLASS =
  "whitespace-pre-wrap break-words rounded-md border border-border bg-deepest p-3 font-mono text-dense text-foreground";

/** The launch-mode toggle — only meaningful when a harness supports more than one mode. */
function ModeToggle({
  modes,
  active,
  onSelect,
}: {
  modes: LaunchMode[];
  active: LaunchMode;
  onSelect: (m: LaunchMode) => void;
}) {
  if (modes.length <= 1) return null; // host-only harness (e.g. Claude Code) → no toggle
  return (
    <div className="flex flex-wrap items-center gap-2 text-caption">
      <span className="text-text-secondary">Running the agent on:</span>
      {modes.map((m) => (
        <Button
          key={m}
          type="button"
          variant={active === m ? "outline" : "ghost"}
          size="sm"
          onClick={() => onSelect(m)}
        >
          {m === "host" ? "this host (localhost)" : "a container (host.docker.internal)"}
        </Button>
      ))}
    </div>
  );
}

/** The correlation status line — whether this harness's traces bind to the run. */
function CorrelationNote({ correlation }: { correlation?: string }) {
  if (correlation === "resource-attr") {
    return (
      <p className="flex max-w-[68ch] items-start gap-1.5 text-body text-primary">
        <Check className="mt-1 size-3.5 shrink-0" aria-hidden />
        <span>
          This harness&rsquo;s traces will correlate to <em>this</em> run —{" "}
          <code>OTEL_RESOURCE_ATTRIBUTES</code> is set for you, so the whole trace attaches
          (not just the first batch).
        </span>
      </p>
    );
  }
  return (
    <p className="max-w-[68ch] text-body text-text-secondary">
      Best-effort correlation via the prompt marker only — some trace batches may not attach
      to this run.
    </p>
  );
}

/** A quiet, always-available copy of the run's launch command, for the run header once the
 *  hand-off card has yielded its slot to the live trace. Renders nothing for a harness with
 *  no launch command (prompt-only), so it never shows an empty affordance. */
export function LaunchCommandCopy({ runId }: { runId: string }) {
  const launchProfile = useRunLaunchProfile(runId, "host");
  const text = launchProfile.data?.shell_block || launchProfile.data?.command || "";
  if (!text) return null;
  return (
    <CopyButton
      text={text}
      idleLabel="Copy launch command"
      copiedLabel="Copied"
      variant="ghost"
      size="sm"
    />
  );
}

/**
 * The "start your agent" hand-off, shown while a run is waiting to start.
 *
 * A harness with a launch command (e.g. Claude Code) gets ONE consolidated block — the env
 * `export`s followed by the launch command with the prompt embedded — so the env, prompt, and
 * command aren't repeated three times; the human-readable mission sits behind a collapsible.
 * A prompt-only harness (no launch command) keeps the copyable prompt + telemetry-env blocks.
 *
 * Layout contract: the card is a HEAD (title + the one primary copy action, pinned) over a
 * BODY (the single scroller). The action can never be pushed below the fold by a long payload —
 * the whole point of this surface is to get that command into the operator's terminal.
 * With `fill` the card takes its parent pane's height (it is the Trace pane's pre-flight state).
 */
export function RunPromptCard({
  runId,
  fill = false,
  awaitingEnvironment = false,
}: {
  runId: string;
  fill?: boolean;
  /** The mission environment has not come up yet. The launch payload is still handed over (the
   *  operator can copy it ahead of time), but the headline must not read "Action required" — that
   *  invites launching into an environment whose targets do not exist yet, which is how an agent
   *  ends up joined to the tailnet with nothing to reach. */
  awaitingEnvironment?: boolean;
}) {
  // Default "host": most operators paste this into a terminal (`claude -p`), where the collector
  // is localhost — host.docker.internal (the container address) does NOT resolve on the host.
  const [launchMode, setLaunchMode] = useState<LaunchMode>("host");
  // The prompt tracks the SAME toggle so its run-control Base URL + --add-host note match
  // the launch mode, instead of staying frozen at the container-baked host.
  const prompt = useRunPrompt(runId, launchMode);
  const launchProfile = useRunLaunchProfile(runId, launchMode);
  const env = launchProfile.data?.env ?? {};
  const dotenv = toDotenv(env);
  const correlation = launchProfile.data?.correlation;
  const notes = launchProfile.data?.notes ?? [];
  const command = launchProfile.data?.command;
  const shellBlock = launchProfile.data?.shell_block ?? "";
  const tips = launchProfile.data?.tips ?? [];
  const launchModes: LaunchMode[] = launchProfile.data?.launch_modes ?? ["host", "container"];

  const [showPrompt, setShowPrompt] = useState(false);
  const [showFullCommand, setShowFullCommand] = useState(false);
  // A launch harness (has a copy-paste command) gets the single consolidated block.
  const hasCommand = !!command;
  const block = shellBlock || command || "";
  // A real launch block is ~3.6kB of embedded mission — a copy-paste payload, not a document.
  // Rendered in full it buries the tips and the correlation note under ~1200px of wrapped text,
  // so it is clamped behind an explicit disclosure (clamped, never silently cut: the fade and
  // the toggle both say there is more, and expanding it adds no second scroller).
  const longBlock = block.length > 400;
  // The one payload this surface exists to hand over — pinned to the card head so it is the
  // first thing the operator sees, never something they have to scroll a code block to find.
  const primaryText = hasCommand ? shellBlock || command || "" : (prompt.data?.prompt ?? "");
  const primaryLabel = hasCommand ? "Copy launch command" : "Copy prompt";

  const tipsAndNotes = (
    <>
      {tips.length > 0 && (
        <div className="space-y-2 border-t border-border pt-2">
          <p className="text-caption font-semibold text-text-secondary">How to launch</p>
          <ul className="list-disc space-y-1 pl-4">
            {tips.map((tip) => (
              <li key={tip} className="text-caption text-text-secondary">
                {tip}
              </li>
            ))}
          </ul>
        </div>
      )}
      {notes.map((note) => (
        <p key={note} className="text-caption text-text-secondary">
          <span className="text-label uppercase text-primary">Note</span>{" "}
          {note}
        </p>
      ))}
    </>
  );

  return (
    <Card
      // Left tone accent — the same 2px bar Toast uses, as border utilities rather than the
      // ad hoc 3px inline style it was.
      className={`border-l-2 border-l-primary bg-primary/5${fill ? " flex h-full min-h-0 flex-col overflow-hidden" : ""}`}
    >
      <CardContent
        className={`flex flex-col gap-3 p-4${fill ? " min-h-0 flex-1" : ""}`}
      >
        {/* ── HEAD — pinned: the title and the one action that moves this run forward ── */}
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <Terminal className="size-4 shrink-0 text-primary" />
            <h2 className="text-body font-semibold text-primary">
              {awaitingEnvironment
                ? "Preparing environment — launch once it is ready"
                : "Action required — start your agent"}
            </h2>
          </div>
          {primaryText && (
            <CopyButton
              text={primaryText}
              idleLabel={primaryLabel}
              copiedLabel="Copied"
              size="sm"
            />
          )}
        </div>

        {/* ── BODY — the single scroller in this column (payloads wrap, never scroll) ── */}
        <div
          className={`min-w-0 space-y-2${fill ? " min-h-0 flex-1 overflow-y-auto pr-1" : ""}`}
        >
          {prompt.isLoading && (
            <div role="status" aria-label="Loading prompt…">
              <SkeletonRows count={3} />
            </div>
          )}
          {prompt.isError && (
            <p className="text-body text-err">Couldn’t load the prompt for this run.</p>
          )}

          {hasCommand ? (
            /* ── Launch harness: ONE block (env exports + command), prompt collapsed ── */
            <>
              <ModeToggle modes={launchModes} active={launchMode} onSelect={setLaunchMode} />
              <CorrelationNote correlation={correlation} />
              <p className="max-w-[68ch] text-body text-text-secondary">
                Run this single block — it exports the telemetry env and starts the harness with
                this run’s prompt embedded, so it reports back to <em>this</em> run.
              </p>
              <div className="flex items-center gap-1.5">
                <Radio className="size-3.5 text-primary" />
                <h3 className="text-label uppercase text-primary">
                  Launch command
                </h3>
              </div>
              <div className="relative">
                <pre
                  className={`${PRE_CLASS}${longBlock && !showFullCommand ? " max-h-56 overflow-hidden" : ""}`}
                >
                  {block}
                </pre>
                {longBlock && !showFullCommand && (
                  <span
                    className="pointer-events-none absolute inset-x-px bottom-px h-10 rounded-b-md bg-gradient-to-t from-deepest to-transparent"
                    aria-hidden
                  />
                )}
              </div>
              {longBlock && (
                <button
                  type="button"
                  onClick={() => setShowFullCommand((v) => !v)}
                  aria-expanded={showFullCommand}
                  className="flex items-center gap-1.5 text-caption text-text-secondary hover:text-foreground"
                >
                  {showFullCommand ? (
                    <ChevronDown className="size-3" />
                  ) : (
                    <ChevronRight className="size-3" />
                  )}
                  {showFullCommand ? "Show less" : "Show full command"}
                </button>
              )}

              {prompt.data && (
                <div className="border-t border-border pt-2">
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => setShowPrompt((v) => !v)}
                      aria-expanded={showPrompt}
                      className="flex items-center gap-1.5 text-caption text-text-secondary hover:text-foreground"
                    >
                      {showPrompt ? (
                        <ChevronDown className="size-3" />
                      ) : (
                        <ChevronRight className="size-3" />
                      )}
                      View mission prompt
                    </button>
                    {showPrompt && (
                      <CopyButton
                        text={prompt.data.prompt}
                        idleLabel="Copy prompt"
                        copiedLabel="Copied"
                        variant="ghost"
                        size="sm"
                      />
                    )}
                  </div>
                  {showPrompt && <pre className={`mt-2 ${PRE_CLASS}`}>{prompt.data.prompt}</pre>}
                </div>
              )}

              {tipsAndNotes}
            </>
          ) : (
            /* ── Prompt-only harness: copyable prompt + (optional) telemetry-env block ── */
            <>
              <p className="max-w-[68ch] text-body text-text-secondary">
                Copy this prompt and paste it into your agent to begin the run. It carries the run
                id, so your agent reports back to <em>this</em> run.
              </p>
              {prompt.data && <pre className={PRE_CLASS}>{prompt.data.prompt}</pre>}

              {dotenv && (
                <div className="space-y-2 border-t border-border pt-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <Radio className="size-3.5 shrink-0 text-primary" />
                      <h3 className="text-label uppercase text-primary">
                        Telemetry — harness launch profile
                      </h3>
                    </div>
                    <CopyButton
                      text={dotenv}
                      idleLabel="Copy env"
                      copiedLabel="Copied"
                      variant="ghost"
                      size="sm"
                    />
                  </div>
                  <ModeToggle modes={launchModes} active={launchMode} onSelect={setLaunchMode} />
                  <CorrelationNote correlation={correlation} />
                  <p className="max-w-[68ch] text-body text-text-secondary">
                    Export these before launching your agent (the reference <code>demo.py</code>,
                    or <code>set -a; source launch.env; set +a</code>) so it ships traces to this
                    run’s collector. Not needed for prompt-only agents.
                  </p>
                  <pre className={PRE_CLASS}>{dotenv}</pre>
                  {tipsAndNotes}
                </div>
              )}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
