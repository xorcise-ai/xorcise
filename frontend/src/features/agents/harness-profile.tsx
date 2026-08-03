"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  CheckCircle2,
  CircleHelp,
  Info,
  Terminal,
  XCircle,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/components/ui/cn";
import {
  groupLevel,
  type GroupLevel,
  type DisplayGroup,
} from "@/features/capabilities/capability-groups";
import type { HarnessDescriptor } from "@/lib/api/types";
import { HARNESSES, HarnessGlyph } from "./harnesses";
import { useHarnessDescriptors } from "./use-harnesses";

const REGISTRATION_GROUPS: readonly DisplayGroup[] = [
  { id: "user-messages", label: "User messages", kinds: ["message"] },
  { id: "agent-messages", label: "Agent messages", kinds: ["message"] },
  { id: "thinking", label: "Thinking / CoT", kinds: ["thinking"] },
  { id: "terminal", label: "Terminal", kinds: ["terminal_command", "terminal_output"] },
  { id: "files", label: "File edits", kinds: ["file_edit", "file_read"] },
  { id: "tools", label: "Tool calls", kinds: ["tool_call", "tool_result"] },
  { id: "mcp", label: "MCP", kinds: ["mcp_call", "mcp_result"] },
  { id: "health", label: "Status / errors / metrics", kinds: ["status", "error", "metric"] },
] as const;

function registrationLevel(
  profile: HarnessDescriptor["capabilities"],
  group: DisplayGroup,
): GroupLevel {
  if (group.id === "user-messages") {
    return profile.message_roles?.user ?? groupLevel(profile, group);
  }
  if (group.id === "agent-messages") {
    return profile.message_roles?.agent ?? groupLevel(profile, group);
  }
  return groupLevel(profile, group);
}

function selectedDescriptor(
  kind: string,
  byKind: ReturnType<typeof useHarnessDescriptors>["byKind"],
) {
  return byKind.get(HARNESSES.some((h) => h.kind === kind) ? kind : "generic");
}

export function capabilitySummary(
  profile: HarnessDescriptor["capabilities"],
): Record<GroupLevel, number> {
  const summary: Record<GroupLevel, number> = {
    supported: 0,
    partial: 0,
    unsupported: 0,
  };
  for (const group of REGISTRATION_GROUPS) summary[registrationLevel(profile, group)] += 1;
  return summary;
}

/** Selected-harness-only visibility and launch preview used during registration. */
export function HarnessProfile({
  kind,
  section = "all",
  launchPreview,
  launchCustomized = false,
  launchFooter,
}: {
  kind: string;
  section?: "all" | "visibility" | "launch";
  launchPreview?: HarnessDescriptor["launch"];
  launchCustomized?: boolean;
  launchFooter?: ReactNode;
}) {
  const { byKind, descriptors, isLoading, isError } = useHarnessDescriptors();
  const descriptor = selectedDescriptor(kind, byKind);

  if (isLoading || !descriptors) {
    return (
      <div
        role="status"
        aria-label="Loading harness profile…"
        className={section === "all" ? "grid gap-3 xl:grid-cols-2" : undefined}
      >
        {section !== "launch" && <Skeleton className="h-48" />}
        {section !== "visibility" && <Skeleton className="h-48" />}
      </div>
    );
  }

  if (isError || !descriptor) {
    return (
      <Card className="flex items-start gap-2 p-4 text-dense text-text-secondary">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        Harness details are unavailable. You can still register this agent.
      </Card>
    );
  }

  const isGeneric = descriptor.kind === "generic";
  const summary = capabilitySummary(descriptor.capabilities);
  const effectiveLaunch = launchPreview ?? descriptor.launch;

  const visibility = (
    <Card className={cn("p-3", section === "visibility" && "h-full")}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <HarnessGlyph kind={isGeneric ? null : descriptor.kind} />
            <div className="min-w-0">
              <h3 className="text-body font-semibold text-heading">Export span support</h3>
              <p className="truncate text-dense text-text-tertiary">
                {isGeneric ? "Unknown until the custom harness is verified" : descriptor.display_name}
              </p>
            </div>
          </div>
          <span className="shrink-0 text-caption text-text-tertiary">
            {isGeneric
              ? `${REGISTRATION_GROUPS.length} unknown`
              : `${summary.supported} supported${
                  summary.partial ? ` · ${summary.partial} partial` : ""
                }`}
          </span>
        </div>

        <ul className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-2">
          {REGISTRATION_GROUPS.map((group) => {
            const level = registrationLevel(descriptor.capabilities, group);
            const supported = level !== "unsupported";
            return (
              <li key={group.id} className="flex items-center gap-2 text-dense text-text-secondary">
                {isGeneric ? (
                  <CircleHelp
                    className="size-4 shrink-0 text-primary"
                    aria-label={`${group.label}: unknown`}
                  />
                ) : supported ? (
                  <CheckCircle2
                    className="size-4 shrink-0 text-ok"
                    aria-label={`${group.label}: supported`}
                  />
                ) : (
                  <XCircle
                    className="size-4 shrink-0 text-err"
                    aria-label={`${group.label}: not supported`}
                  />
                )}
                <span>{group.label}</span>
              </li>
            );
          })}
        </ul>
    </Card>
  );

  const launch = (
    <Card
      className={cn(
        "p-3",
        section === "launch" &&
          "flex min-h-full flex-col rounded-none border-0 bg-transparent p-0",
      )}
    >
        <div className="flex items-start gap-2">
          <Terminal className="mt-0.5 size-4 shrink-0 text-text-secondary" aria-hidden />
          <div className="min-w-0">
            <h3 className="text-body font-semibold text-heading">How it launches</h3>
            <p className="text-dense text-text-tertiary">
              {effectiveLaunch.command_template
                ? "Command generated per run"
                : "Operator supplied command"}
            </p>
          </div>
          {launchCustomized && (
            <span className="ml-auto shrink-0 rounded border border-primary/40 px-1.5 py-0.5 text-caption text-primary">
              Agent override
            </span>
          )}
        </div>

        {effectiveLaunch.command_template ? (
          <CommandPreview template={effectiveLaunch.command_template} />
        ) : (
          <p className="mt-3 text-dense text-text-secondary">
            Custom harnesses do not have a verified launch template.
          </p>
        )}
        <p className="mt-2 text-caption text-text-tertiary">
          Preview only. The runnable command is generated after a run has its mission,
          endpoints, and credentials.
        </p>

        <div className="mt-3 space-y-3 border-t border-border pt-3">
          <LaunchTextPreview
            title="Harness tips"
            lines={effectiveLaunch.tips}
            emptyText="No additional harness guidance."
          />
          <LaunchTextPreview
            title="Additional mission preamble"
            lines={effectiveLaunch.mission_preamble}
            emptyText="No additional instructions are added to the mission."
          />
        </div>

        {launchFooter && (
          <div className="sticky bottom-0 z-10 mt-auto bg-card pt-3">
            <div className="border-t border-border pt-3">{launchFooter}</div>
          </div>
        )}
    </Card>
  );

  if (section === "visibility") return visibility;
  if (section === "launch") return launch;

  return (
    <div className="grid items-start gap-3 xl:grid-cols-2">
      {visibility}
      {launch}
    </div>
  );
}

/** Compact read-only facts for the sticky review panel. */
export function HarnessReviewFacts({ kind }: { kind: string }) {
  const { byKind } = useHarnessDescriptors();
  const descriptor = selectedDescriptor(kind, byKind);
  if (!descriptor) return null;
  const isGeneric = descriptor.kind === "generic";
  const summary = capabilitySummary(descriptor.capabilities);
  return (
    <>
      <ReviewFact
        label="Launch"
        value={
          descriptor.launch.command_template
            ? "Command generated per run"
            : "Operator supplied"
        }
      />
      <ReviewFact
        label="Visibility"
        value={
          isGeneric
            ? "Unverified · capabilities unknown"
            : `${summary.supported} supported${
                summary.partial ? ` · ${summary.partial} partial` : ""
              } · ${summary.unsupported} unavailable`
        }
      />
    </>
  );
}

/** Wrapping launch-command preview. The command must stay fully readable at ANY column width
 *  (the page is three-up from `xl`, so a column can be ~380px), so the text wraps rather than
 *  truncates, and a custom multi-line template renders verbatim. Long commands clamp to three
 *  wrapped lines with an expand toggle; whether content is actually hidden is MEASURED (not a
 *  length heuristic), because the same command overflows at one width and fits at another. */
function CommandPreview({ template }: { template: string }) {
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);
  const preRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    const el = preRef.current;
    if (!el || expanded) return;
    const measure = () => setClipped(el.scrollHeight > el.clientHeight + 1);
    measure();
    // jsdom has no ResizeObserver (and no layout, so nothing ever measures as clipped there).
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [template, expanded]);

  return (
    <div className="mt-3">
      <div className="relative">
        <pre
          ref={preRef}
          className={cn(
            // `wrap-anywhere` (not `break-words`): an unbroken token — a `-c key='{"…"}'` blob,
            // a URL — must also shrink the pre's MIN-CONTENT width, or it widens the grid
            // column past the viewport on phones instead of wrapping.
            "whitespace-pre-wrap wrap-anywhere rounded-md border border-border bg-background px-3 py-2 font-mono text-dense text-foreground",
            // Reserve three wrapped command lines plus the block's vertical padding. `lh`
            // tracks the pre's own line-height if the typography scale changes.
            !expanded && "max-h-[calc(3lh+1rem)] overflow-hidden",
          )}
        >
          {template}
        </pre>
        {clipped && !expanded && (
          <div
            className="pointer-events-none absolute inset-x-px bottom-px h-6 rounded-b-md bg-gradient-to-t from-background to-transparent"
            aria-hidden
          />
        )}
      </div>
      {(clipped || expanded) && (
        <div className="mt-1 flex justify-end">
          <button
            type="button"
            className="text-caption text-primary hover:text-primary/80"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Show less" : "Show full command"}
          </button>
        </div>
      )}
    </div>
  );
}

function LaunchTextPreview({
  title,
  lines,
  emptyText,
}: {
  title: string;
  lines: readonly string[];
  emptyText: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasContent = lines.length > 0;

  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-label uppercase text-text-tertiary">{title}</h4>
        {hasContent && (
          <button
            type="button"
            className="shrink-0 text-caption text-primary hover:text-primary/80"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Show less" : "Show more"}
          </button>
        )}
      </div>
      <div
        className={cn(
          "relative mt-1 text-dense text-text-secondary",
          // Reserve roughly three complete dense-text lines plus list spacing. Using `lh`
          // keeps the preview correct if the typography scale changes.
          !expanded && "h-[calc(3lh+0.5rem)] overflow-hidden",
        )}
      >
        {hasContent ? (
          <ul className="list-disc space-y-1 pl-4">
            {lines.map((line) => <li key={line}>{line}</li>)}
          </ul>
        ) : (
          <p className="text-text-tertiary">{emptyText}</p>
        )}
        {hasContent && !expanded && (
          <div
            className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-card to-transparent"
            aria-hidden
          />
        )}
      </div>
    </section>
  );
}

function ReviewFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-label uppercase text-text-tertiary">{label}</dt>
      <dd className="mt-1 text-dense text-text-secondary">{value}</dd>
    </div>
  );
}
