"use client";

import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/components/ui/cn";

/**
 * The one page skeleton for the operational surfaces (Runs, Results, Dashboard,
 * Agents). The shell already gives every page a fixed frame — header 48px,
 * status bar 32px, sidebar 208px — so a page is three bands inside that pane:
 *
 *   <Page>          flex column at exactly the pane's height, 16px frame
 *     <PageHead>    shrink-0 — eyebrow / title / primary action / filters
 *     <PageBody>    min-h-0 flex-1 — THE ONE unbounded region
 *
 * The core principle: the page shell must not scroll. `PageBody` is the only
 * region allowed to, so the head (the chrome that controls the content) stays
 * put. `h-full` resolves because <main> in the app shell is a stretched flex
 * item with a definite height — a page laid out this way never makes the shell
 * scroll, and never clips, because the residual height goes to the body.
 *
 * Spacing ladder (4px base, three tiers, between-group >= 1.5x within-group):
 *   16px  page frame + head→body gap
 *   12px  between cards / sections inside a region
 *    8px  between rows inside a card
 *    6px  label→value, chips, icon+text
 *
 * OPERATE mode: no page-level max-width. Cap the MEASURE on prose, never the
 * pane — a centred column inside an app shell strands the width the operator
 * paid for.
 */
export function Page({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex h-full min-h-0 flex-col gap-4 p-4", className)}
      {...props}
    />
  );
}

/** The fixed band: title block on the left, primary action on the right. */
export function PageHead({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex shrink-0 flex-wrap items-start justify-between gap-x-4 gap-y-3",
        className,
      )}
      {...props}
    />
  );
}

/** Eyebrow + heading (+ optional one-line caption) — the left half of a PageHead. */
export function PageTitle({
  eyebrow,
  subtitle,
  children,
}: {
  eyebrow?: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      {eyebrow && (
        <p className="text-label uppercase text-text-tertiary">
          {eyebrow}
        </p>
      )}
      <h1 className="text-lead text-heading">{children}</h1>
      {subtitle && (
        <p className="mt-2 max-w-[70ch] text-body text-text-secondary">
          {subtitle}
        </p>
      )}
    </div>
  );
}

interface PageBodyProps extends HTMLAttributes<HTMLDivElement> {
  /** `false` when a CHILD owns the scroller (the body becomes a flex column and
   *  hands its residual height down) — e.g. a list that fills the pane. */
  scroll?: boolean;
}

/** The unbounded region. Everything that can grow lives here, and only here. */
export function PageBody({ className, scroll = true, ...props }: PageBodyProps) {
  return (
    <div
      className={cn(
        "min-h-0 flex-1",
        scroll ? "overflow-y-auto pr-1" : "flex flex-col",
        className,
      )}
      {...props}
    />
  );
}
