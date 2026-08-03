import type { ReactNode } from "react";
import { cn } from "@/components/ui/cn";

// Inline SVG marks — static-export/CSP safe (no external image loads), which is also why no
// icon here may be a fetched third-party asset. OpenHands, Claude Code and Codex use their
// official vendor marks; CustomMark is ours and follows the brand system's line-icon rules
// (§7.3). Data-driven: adding a harness = one HARNESSES entry (its adapter ships in the
// future adapters Story).

function OpenHandsMark(): ReactNode {
  // Official OpenHands mark. Brand yellow body (fixed) + linework in `currentColor` so it stays
  // legible on either theme (it inherits the button's text color).
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      xmlSpace="preserve"
      viewBox="0 0 512 512"
      className="size-5 shrink-0"
      aria-hidden="true"
    >
      <path
        fill="#ffe165"
        d="M491.8 187.7c-16.4-9.8-27.3 5.2-25.9 25.6l-.1.2c0-21.3-2.9-44.8-12.7-63.8-3.5-6.7-10.5-17.8-24.4-12.6-6.1 2.3-11.7 9.2-8.8 27 0 0 3.2 18.7 2.6 42.1v.3c-4-64.7-19-84.4-40.4-83.1-6.9 1.2-16.2 4.1-13.1 24 0 0 3.4 20.7 4.5 37.3l.1.8h-.1c-10.1-35.7-23.7-36.1-33.5-34.8-8.9 1.3-18.7 10.3-13.7 28.1 15.5 55.8 12.4 122.9 11.3 132.6-3.2-6.6-4.1-11.8-8.5-19-17.6-29-26-31.1-36.3-31.5-10.2-.4-21.3 5.7-20.5 17.4.7 11.7 6.9 13.6 15.5 29.9 6.8 12.7 8.7 29.3 22.3 59.5 11.3 25 40.8 52.4 94.5 49.2 43.5-1.4 108.6-16.2 97.2-113.6-2.8-16.9-.7-31.1.8-45.6 2.3-22.9 5.6-60.2-10.8-70M220 261.4c-10.3.6-18.6 2.9-35.7 32.2-4.2 7.3-5.1 12.5-8.2 19.1-1.3-9.6-5.6-76.7 8.8-132.7 4.6-17.8-5.3-26.7-14.3-27.8-9.9-1.2-23.5-.5-32.9 35.5h-.1l.1-1c.8-16.5 3.8-37.3 3.8-37.3 2.7-20-6.7-22.7-13.5-23.7-21.4-.9-36 18.9-38.8 83.1-1-23.2 1.8-41.7 1.8-41.7 2.5-17.8-3.2-24.6-9.3-26.8-14-5-20.8 6.2-24.2 13-9.4 19.2-12 42.7-11.5 64l-.1-.2c.9-20.4-10.3-35.2-26.4-25.1-16.2 10-12.1 47.3-9.4 69.8 1.8 14.5 4.1 28.6 1.6 45.6-9.4 97.6 55.8 111.2 99.4 111.9 53.8 2.3 82.8-25.7 93.6-50.8 13-30.4 14.7-47.1 21.2-59.8 8.4-16.4 14.4-18.5 15-30.2.5-11.8-10.6-17.8-20.9-17.1"
      />
      <path
        fill="currentColor"
        d="M241.5 261.5c-5.5-5.2-13.6-7.9-22-7.4-13.4.8-22.8 5.5-37.4 28.6-.4-26.4 1.1-66.1 10.1-101 3.4-13.1 0-21.7-3.5-26.5-4-5.7-10.3-9.4-17.2-10.3-6.3-.8-14.5-.8-22.5 5.6v-.2c2.6-18.7-4.1-29.5-19.8-31.9l-.9-.1c-9.7-.4-18 2.7-24.7 9.1-3.6 3.5-6.7 7.9-9.4 13.4-3-4.3-6.9-6.5-10-7.6-18.7-6.7-29.1 7.6-33.6 16.7-5.2 10.6-8.5 22.5-10.3 34.6-.4-.2-.7-.5-1.1-.7-4.1-2.3-12.7-5.1-23.9 1.8-18.9 11.8-16.5 45.9-12.8 76.8.2 1.6.4 3.3.6 4.9 1.6 12.7 3.1 24.7 1 38.8v.4C.4 345.9 8 376 26.8 396.3c18.1 19.5 46.4 29.7 83.9 30.3 2.7.1 5.4.2 8 .1 64.1-.6 87.1-42.3 92.8-55.5 7.3-17.1 11.1-29.8 14.1-40.1 2.3-7.9 4.2-14.2 6.8-19.4 3-5.9 5.6-9.7 7.9-13.1 4-5.8 7.4-10.7 7.8-20 .5-6.6-1.9-12.5-6.6-17.1M114.3 137.8c3.6-3.5 7.8-5 13-4.9 4.6.7 8.7 1.8 6.8 15.4-.1.9-3.1 21.3-3.9 38v.3c-4.2 16.3-7.6 39.7-9.5 74-8.1.5-16.1 1.3-23.9 2.4-2.4-69.4 3.4-111.6 17.5-125.2m-50 18.6c5.7-11.4 10.4-10.9 14.8-9.3 6.1 2.2 5.2 14 4.5 18.9-.1.8-2.8 19.2-1.9 42.6-.7 16.4-.6 35.4.2 57-7.5 1.4-14.6 3.1-21 4.9-3.1-10-16.6-73.4 3.4-114.1M228 290.6c-2.5 3.6-5.5 8-8.9 14.7-3.2 6.4-5.2 13.2-7.8 21.8-2.9 9.9-6.6 22.2-13.6 38.5-5 11.5-25.8 49-86.5 46.3-33.9-.5-57.8-8.8-73.2-25.4-15.9-17.1-22.2-43.5-18.8-78.3 2.3-15.9.6-29.4-1-42.4-.2-1.6-.4-3.2-.6-4.9-1.8-15-6.7-55 6-62.9 3.4-2.1 6.2-2.6 8.3-1.5 3.5 2 7.1 9.1 6.6 20.1 0 .6 0 1.2.2 1.7.6 25.7 5.4 48.1 8.1 56.5-4.4 1.6-8.3 3.3-11.6 5-3.7 1.9-5.1 6.3-3.1 9.9 1.4 2.5 4 3.9 6.7 3.9 1.2 0 2.3-.3 3.5-.9 18.4-9.4 62-19 100.4-18 4.2.1 7.6-3.1 7.7-7.1s-3.2-7.4-7.3-7.5c-2.3-.1-4.6-.1-6.9-.1 3.8-69.1 14.4-91.7 22.6-98.3 3.4-2.8 6.7-3 11.2-2.4 1.2.2 4.4.9 6.6 4 2.4 3.5 2.9 8.6 1.3 14.8-13.9 54.1-10.8 117.8-9.2 133.3-.3.5-.5 1.1-.8 1.6-3.2 5.9-9.3 12.1-16.5 11.7-4.1-.2-7.7 2.8-8 6.8s2.9 7.5 7.1 7.7c12.2.7 23.6-6.5 30.7-19.4.7-1.4 1.4-2.7 2-3.9 0-.1.1-.2.1-.3 1.3-2.9 2.3-5.5 3.1-7.8 1.3-3.7 2.5-6.8 4.7-10.7 16.1-27.7 22.7-28.1 29.6-28.5 4.1-.2 8.1 1 10.5 3.3 1.7 1.6 2.5 3.6 2.3 6.1-.5 5.3-2 7.4-5.5 12.6m281.2 11.2c-2.3-14.1-1-26.1.3-38.8.2-1.7.4-3.3.5-4.9 3.2-31 5-65.1-14.2-76.6-11.3-6.8-19.9-3.8-24-1.4-.4.2-.7.5-1.1.7-2.1-12.1-5.5-23.9-10.9-34.4-4.7-9-15.3-23.1-33.9-16.1-3 1.1-6.9 3.4-9.8 7.7-2.8-5.5-6-9.9-9.7-13.3-6.8-6.3-15.2-9.3-24.9-8.7l-.9.1c-15.7 2.7-22.2 13.6-19.2 32.3v.2c-8.1-6.2-16.4-6.1-22.6-5.2-6.9 1-13.1 4.8-17 10.6-3.3 4.9-6.6 13.5-3 26.6 9.6 34.7 11.9 74.4 12 100.8-15-22.8-24.6-27.4-37.9-28-8.3-.4-16.5 2.6-21.8 7.8-4.7 4.6-6.9 10.6-6.5 17.3.6 9.2 4.1 14.1 8.2 19.8 2.4 3.3 5.1 7.1 8.2 13 2.7 5.1 4.7 11.4 7.2 19.2 3.2 10.2 7.2 22.9 14.9 39.8 5.9 13.1 29.7 54.4 93.7 53.8 2.6 0 5.3-.1 8-.3 37.7-1.2 65.9-11.9 83.6-31.7 18.4-20.6 25.4-50.9 20.9-90zm-82-138.9c-.8-5-2-16.8 4.1-19 4.4-1.7 9.1-2.3 15 9 20.8 40.3 8.5 104 5.6 114.1-6.5-1.7-13.6-3.2-21.1-4.5.4-21.7.1-40.6-.9-57 .5-23.5-2.6-41.8-2.7-42.6m-44.4-32.3c5.3-.2 9.5 1.3 13.1 4.7 14.4 13.4 21 55.4 19.8 125-7.8-1-15.9-1.7-24-2-2.5-34.3-6.4-57.6-10.9-73.8v-.3c-1.1-16.7-4.5-37.1-4.6-37.9-2.1-13.7 2-14.9 6.6-15.7M494.3 304c4 34.8-1.8 61.2-17.4 78.6-15.1 16.9-38.8 25.6-72.9 26.7-60.4 3.7-82-33.4-87.1-44.8-7.3-16.2-11.2-28.4-14.3-38.2-2.7-8.6-4.9-15.3-8.2-21.6-3.5-6.7-6.7-11-9.2-14.6-3.7-5.1-5.2-7.3-5.5-12.4-.2-2.5.6-4.5 2.2-6.2 2.4-2.3 6.4-3.7 10.4-3.5 6.9.3 13.5.6 30.1 28 2.4 3.9 3.5 7 4.9 10.6.9 2.4 1.9 5 3.3 7.8 0 .1.1.2.1.2.6 1.3 1.3 2.5 2 3.9 7.3 12.7 18.9 19.7 31 18.8 4.1-.3 7.2-3.8 6.9-7.8s-3.9-7-8.1-6.7c-7.2.5-13.4-5.5-16.7-11.4-.3-.6-.6-1.1-.8-1.6 1.3-15.4 3.2-79.2-11.7-133.1-1.7-6.2-1.4-11.3 1-14.8 2.2-3.2 5.3-4 6.6-4.2 4.5-.6 7.7-.5 11.2 2.2 8.4 6.5 19.3 28.9 24.5 97.9-2.3 0-4.6.1-6.9.2-4.2.2-7.4 3.6-7.2 7.6s3.6 7.1 7.9 7c38.4-1.7 82.1 7.1 100.7 16.2 1.1.6 2.3.8 3.5.8 2.7 0 5.3-1.5 6.7-4 1.9-3.6.4-8-3.3-9.8q-4.95-2.4-11.7-4.8c2.5-8.4 6.9-30.9 7-56.7.1-.6.2-1.1.1-1.7-.7-11 2.7-18.2 6.2-20.2 2-1.2 4.8-.7 8.3 1.3 12.8 7.7 8.7 47.7 7.2 62.7-.2 1.6-.3 3.2-.5 4.9-1.5 13.3-2.9 26.8-.3 42.7M283.7 163.3c-1.2 0-2.4-.2-3.6-.7-4.2-1.9-6.1-6.8-4.2-10.9 6.3-13.4 15.6-25.7 26.7-35.6 3.4-3.1 8.8-2.9 11.9.5 3.2 3.3 3 8.5-.5 11.6-9.5 8.5-17.4 19-22.8 30.4-1.3 2.9-4.3 4.7-7.5 4.7m-28.5-4.7c-4.4 0-8.1-3.2-8.5-7.5-1.6-19.3-1.7-38.9-.2-58.1.4-4.5 4.4-7.9 9.1-7.5s8.1 4.3 7.8 8.8c-1.5 18.4-1.4 37.1.2 55.6.4 4.5-3.1 8.5-7.7 8.8-.2-.2-.4-.1-.7-.1m-29.1 5.1c-3.8 0-7.3-2.4-8.3-6.2-3.6-13.6-10.4-26.6-19.6-37.4-3-3.5-2.5-8.6 1.1-11.5s8.9-2.4 11.9 1.1c10.8 12.7 18.8 27.9 22.9 43.8 1.2 4.4-1.6 8.8-6.1 10-.6.1-1.2.2-1.9.2"
      />
    </svg>
  );
}

function ClaudeCodeMark(): ReactNode {
  // Anthropic-style sunburst in the brand coral; static-export/CSP-safe (inline, no image load).
  return (
    <svg viewBox="0 0 24 24" className="size-5 shrink-0" aria-hidden="true" fill="none"
         stroke="#d97757" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4" />
    </svg>
  );
}

function CodexMark(): ReactNode {
  // OpenAI Codex CLI — OpenAI's official blossom logomark (single path, viewBox 0 0 16 16), in
  // OpenAI's monochrome off-white, which is how the mark reads on a dark ground (and stays distinct
  // from the amber brand mark and Claude's coral). Fixed fill like the other harness marks, so it
  // keeps its identity wherever it renders. Static-export/CSP-safe (inline).
  return (
    <svg viewBox="0 0 16 16" className="size-5 shrink-0" aria-hidden="true" fill="#ececf1">
      <path d="M14.949 6.547a3.94 3.94 0 0 0-.348-3.273 4.11 4.11 0 0 0-4.4-1.934A4.1 4.1 0 0 0 8.423.2 4.15 4.15 0 0 0 6.305.086a4.1 4.1 0 0 0-1.891.948 4.04 4.04 0 0 0-1.158 1.753 4.1 4.1 0 0 0-1.563.679A4 4 0 0 0 .554 4.72a3.99 3.99 0 0 0 .502 4.731 3.94 3.94 0 0 0 .346 3.274 4.11 4.11 0 0 0 4.402 1.933c.382.425.852.764 1.377.995.526.231 1.095.35 1.67.346 1.78.002 3.358-1.132 3.901-2.804a4.1 4.1 0 0 0 1.563-.68 4 4 0 0 0 1.14-1.253 3.99 3.99 0 0 0-.506-4.716m-6.097 8.406a3.05 3.05 0 0 1-1.945-.694l.096-.054 3.23-1.838a.53.53 0 0 0 .265-.455v-4.49l1.366.778q.02.011.025.035v3.722c-.003 1.653-1.361 2.992-3.037 2.996m-6.53-2.75a2.95 2.95 0 0 1-.36-2.01l.095.057L5.29 12.09a.53.53 0 0 0 .527 0l3.949-2.246v1.555a.05.05 0 0 1-.022.041L6.473 13.3c-1.454.826-3.311.335-4.15-1.098m-.85-6.94A3.02 3.02 0 0 1 3.07 3.949v3.785a.51.51 0 0 0 .262.451l3.93 2.237-1.366.779a.05.05 0 0 1-.048 0L2.585 9.342a2.98 2.98 0 0 1-1.113-4.094zm11.216 2.571L8.747 5.576l1.362-.776a.05.05 0 0 1 .048 0l3.265 1.86a3 3 0 0 1 1.173 1.207 2.96 2.96 0 0 1-.27 3.2 3.05 3.05 0 0 1-1.36.997V8.279a.52.52 0 0 0-.276-.445m1.36-2.015-.097-.057-3.226-1.855a.53.53 0 0 0-.53 0L6.249 6.153V4.598a.04.04 0 0 1 .019-.04L9.533 2.7a3.07 3.07 0 0 1 3.257.139c.474.325.843.778 1.066 1.303.223.526.289 1.103.191 1.664zM5.503 8.575 4.139 7.8a.05.05 0 0 1-.026-.037V4.049c0-.57.166-1.127.476-1.607s.752-.864 1.275-1.105a3.08 3.08 0 0 1 3.234.41l-.096.054-3.23 1.838a.53.53 0 0 0-.265.455zm.742-1.577 1.758-1 1.762 1v2l-1.755 1-1.762-1z" />
    </svg>
  );
}

function CustomMark(): ReactNode {
  // The three marks above are real vendor logos; this one stands for "a CLI agent you brought
  // that we have no logo for". A prompt-in-a-frame says exactly that — every harness XORCISE
  // drives is a command-line agent, so the shell prompt is the honest generic, and the frame
  // gives it the visual mass to sit in a row of logo marks.
  //
  // Brand system §7.3: Lucide family, 24px grid, 1.6px stroke, rounded joins, ONE stroke
  // weight, no fills, no two-tone, monochrome via currentColor (so it inherits amber for an
  // action and dim for chrome). `terminal` is one of the guide's own example icons.
  //
  // Not a robot/AI head — but for licensing and meaning, NOT because the brand bans the shape.
  // §7.2's stock-AI-imagery cliché rule governs IMAGERY (its Don't list bans stock art:
  // people-at-laptops and the like), not the icon set: the same list bans "padlock /
  // shield" while §7.3 ships `shield` as an approved icon. Lucide `Bot` elsewhere in this app
  // is fine for the same reason. What rules a vendor glyph out here is that a third-party mark
  // drags a license into a distribution that ships as one wheel with no external assets.
  return (
    <svg viewBox="0 0 24 24" className="size-5 shrink-0" aria-hidden="true" fill="none"
         stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="m7.5 10 2 2-2 2" />
      <path d="M12.5 14h4" />
    </svg>
  );
}

export interface Harness {
  kind: string; // stored as agent.kind -> run.source_agent -> adapter selection
  name: string;
  Logo: () => ReactNode;
  live?: boolean; // has a rich adapter today (else renders via the generic adapter)
  blurb: string; // one-line descriptor for the harness card (UI copy, not sourced from profile notes)
}

// OpenHands + Claude Code + Codex have live adapters + telemetry profiles.
// Future harnesses append here as their adapters land — no register-page rework.
//
// Display names say which ARTEFACT is evaluated: XORCISE drives the coding CLIs, not the
// models or the IDE products, so "Claude Code CLI" / "Codex CLI" (OpenHands keeps its plain
// product name — it isn't marketed as a CLI). The `kind` slugs are contract values
// (agent.kind → run.source_agent → adapter selection) and MUST NOT follow the display names.
export const HARNESSES: Harness[] = [
  {
    kind: "openhands",
    name: "OpenHands",
    Logo: OpenHandsMark,
    live: true,
    blurb: "Autonomous dev agent — richest telemetry profile.",
  },
  {
    kind: "claude-code",
    name: "Claude Code CLI",
    Logo: ClaudeCodeMark,
    live: true,
    blurb: "Anthropic's coding CLI — no thinking traces exported.",
  },
  {
    kind: "codex",
    name: "Codex CLI",
    Logo: CodexMark,
    live: true,
    blurb: "OpenAI's coding CLI — user prompts only, no agent messages.",
  },
];

export const DEFAULT_KIND = "openhands";
export const CUSTOM_LABEL = "Custom";
export { CustomMark };

/**
 * The harness mark for a `kind` (Codex/OpenAI, Claude Code, OpenHands, …) as a compact identity
 * glyph for dense lists — the Results agent cards and the Run-History cards. `muted` desaturates
 * the brand colours to grey so the mark reads as a subtle identifier rather than a focal point.
 * An unknown / blank kind falls back to the neutral custom mark. Size it from the caller via
 * `className` (e.g. `[&_svg]:!size-4`); otherwise the mark keeps its native size.
 */
export function HarnessGlyph({
  kind,
  muted = false,
  className,
}: {
  kind: string | null | undefined;
  muted?: boolean;
  className?: string;
}) {
  const Logo =
    (kind ? HARNESSES.find((h) => h.kind === kind)?.Logo : undefined) ?? CustomMark;
  return (
    <span
      className={cn("inline-flex shrink-0", muted && "opacity-70 grayscale", className)}
      aria-hidden
    >
      <Logo />
    </span>
  );
}
