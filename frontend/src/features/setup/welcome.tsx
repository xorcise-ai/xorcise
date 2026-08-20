"use client";

import Link from "next/link";
import { LayoutDashboard, ArrowRight, Check } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { StatusDot } from "@/components/ui/dot";
import { useRuns } from "@/features/runs/queries";
import { isTerminal } from "@/features/runs/run-state";
import { useServerHealth } from "./queries";
import { useReadiness } from "./readiness";
import { QuickStart } from "./quick-start";
import { ReadinessChecklist } from "./checklist";

/** The four things an operator does to get their first evaluation running, in
 *  order (§5). A real sequence, so a numbered strip is honest structure rather
 *  than decoration. Each step carries the page that performs it — the strip is
 *  the product's start path, so it navigates (this replaces the Dashboard's old
 *  "Start here" panel, which duplicated it without the readiness adaptation). */
const STEPS = [
  { n: 1, href: "/agents", title: "Register an agent", desc: "Point XORCISE at your harness." },
  {
    n: 2,
    href: "/missions",
    title: "Install a mission",
    desc: "Pull from the library or ingest a bundle.",
  },
  { n: 3, href: "/runs/new", title: "Start a run", desc: "Watch it work in real time." },
  { n: 4, href: "/results", title: "Review the result", desc: "Scored assertions + narrative." },
] as const;

/** Compact four-step "how it works" strip — one short line each, no repeated
 *  workflow prose (the hero already explains the product). Replaces the tall
 *  two-column overview card (§5 "simplify the top section"). Adapts to readiness
 *  (§5 "the page should adapt based on readiness"): a completed step shows a green
 *  tick + "Done"; the first still-outstanding step is marked "Next"; the rest keep
 *  their number + one-line description. Every cell is a link to the page that
 *  performs the step, so the strip is both the status and the way in. */
function StepStrip({ done }: { done: boolean[] }) {
  const nextIdx = done.findIndex((d) => !d);
  return (
    <Card>
      <CardContent className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => {
          const isDone = done[i] ?? false;
          const isNext = i === nextIdx;
          return (
            <Link
              key={s.n}
              href={s.href}
              className="group -m-1.5 grid grid-cols-[24px_1fr] gap-2.5 rounded-md border border-transparent p-1.5 transition-colors hover:border-border-hover hover:bg-raised"
            >
              <span
                className={
                  "flex size-6 items-center justify-center rounded-full border text-caption font-semibold tabular-nums " +
                  (isDone
                    ? "border-ok/40 bg-ok/10 text-ok"
                    : "border-primary/30 bg-primary/8 text-primary")
                }
              >
                {isDone ? <Check className="size-3.5" /> : s.n}
              </span>
              <span className="min-w-0 text-body text-foreground">
                <span className="flex items-center gap-1.5">
                  {s.title}
                  <ArrowRight className="size-3.5 shrink-0 text-text-tertiary transition-all group-hover:translate-x-0.5 group-hover:text-primary" />
                </span>
                <span className="mt-2 block text-caption text-text-tertiary">
                  {isDone ? (
                    <span className="text-ok">Done</span>
                  ) : isNext ? (
                    <span className="text-primary">Next · {s.desc}</span>
                  ) : (
                    s.desc
                  )}
                </span>
              </span>
            </Link>
          );
        })}
      </CardContent>
    </Card>
  );
}

/**
 * The "Get started" landing: what XORCISE is, how it works, where to begin, and
 * the readiness checklist — the first-run home. Rendered at /setup, and at / for
 * a fresh operator (see the Home router). Once runs exist, offers a jump to the
 * operational Dashboard.
 */
export function Welcome() {
  const health = useServerHealth();
  const readiness = useReadiness();
  const runs = useRuns();
  const serverDown = health.isError;
  const runCount = runs.data?.length ?? 0;

  // Adaptive step progress (§5): agent registered · mission available · a run started ·
  // a result to review. Derived from the same queries the checklist uses — no new sources.
  const stepDone = [
    readiness.agentOk,
    readiness.missionOk,
    runCount > 0,
    (runs.data ?? []).some(isTerminal),
  ];

  return (
    <div className="page-measure space-y-8 p-6">
      <header className="space-y-3">
        <span className="inline-flex items-center gap-2 rounded-full border border-border px-2.5 py-1 text-caption text-text-secondary">
          {/* The DS dot primitive; the pulse stays because it is what says "live", and a dot
              never stands alone here — the phrase beside it names the state. */}
          <StatusDot
            tone={serverDown ? "err" : "ok"}
            size="lg"
            className={serverDown ? undefined : "motion-safe:animate-pulse"}
          />
          {serverDown ? "Backend unreachable" : "Backend running"}
        </span>
        <h1 className="text-lead text-heading">Get started with XORCISE.AI</h1>
        {/* `prose-block` IS the 68ch measure (globals.css) — it was being re-typed here. */}
        <p className="prose-block text-body text-text-secondary">
          Evaluate how well cyber AI agents perform on real cybersecurity missions — declare
          an agent, pull a mission, and watch it work end to end.
        </p>
      </header>

      <StepStrip done={stepDone} />

      <section aria-label="Setup checklist" className="space-y-2">
        <h2 className="text-label uppercase text-text-tertiary">
          {readiness.ready ? "Readiness" : "Finish setup"}
        </h2>
        <ReadinessChecklist startHref={readiness.startHref} />
      </section>

      <QuickStart startHref={readiness.startHref} />

      {runCount > 0 && readiness.ready && (
        <Link href="/" className="group block">
          {/* The card shape comes from the DS Card, same as the quick-start tiles; the link
              keeps the hover it shipped with. */}
          <Card className="flex items-center justify-between gap-3 px-4 py-3 text-body text-text-secondary transition-colors group-hover:border-border-hover">
            <span className="inline-flex items-center gap-2">
              <LayoutDashboard className="size-4 text-text-tertiary" />
              Already up and running?{" "}
              <span className="text-heading">
                {runCount} run{runCount === 1 ? "" : "s"}
              </span>
            </span>
            <span className="inline-flex items-center gap-1 text-primary">
              Go to dashboard <ArrowRight className="size-3.5" />
            </span>
          </Card>
        </Link>
      )}
    </div>
  );
}
