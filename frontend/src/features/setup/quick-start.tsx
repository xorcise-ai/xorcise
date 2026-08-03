"use client";

import Link from "next/link";
import { Play, Swords, BookOpen, ArrowRight, type LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";

/** Canonical docs site. There is no in-app /docs route, so this is an external
 *  link (opens in a new tab). Kept as a single constant so pointing it at the
 *  final URL is a one-line change. */
export const DOCS_URL = "https://docs.xorcise.ai";

interface QuickCard {
  title: string;
  desc: string;
  icon: LucideIcon;
  href: string;
  external?: boolean;
  primary?: boolean;
}

/**
 * The three "where do I begin?" actions on the welcome landing. `startHref` is
 * computed by the caller from readiness (see useReadiness) so a fresh operator
 * is routed to the first missing prerequisite rather than dead-ending on a run
 * form they cannot submit.
 */
export function QuickStart({ startHref }: { startHref: string }) {
  const cards: QuickCard[] = [
    {
      title: "Start a Run",
      desc: "Create your first evaluation.",
      icon: Play,
      href: startHref,
      primary: true,
    },
    {
      title: "Browse Missions",
      desc: "Explore available targets.",
      icon: Swords,
      href: "/missions",
    },
    {
      title: "Read Documentation",
      desc: "Setup, traces, results.",
      icon: BookOpen,
      href: DOCS_URL,
      external: true,
    },
  ];

  return (
    <section aria-label="Quick start">
      <h2 className="mb-2 text-label uppercase text-text-tertiary">
        Quick start
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        {cards.map((c) => (
          <QuickCardTile key={c.title} card={c} />
        ))}
      </div>
    </section>
  );
}

function QuickCardTile({ card }: { card: QuickCard }) {
  const Icon = card.icon;
  const body = (
    <Card
      className={
        "flex h-full flex-col p-4 transition-colors group-hover:border-[rgba(255,255,255,0.14)] " +
        (card.primary ? "border-primary/40 bg-primary/[0.04]" : "")
      }
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex size-6 items-center justify-center rounded-md border border-border bg-raised text-primary">
          <Icon className="size-3.5" />
        </span>
        <ArrowRight
          className="size-4 text-text-tertiary transition-all group-hover:translate-x-0.5 group-hover:text-primary"
          aria-hidden
        />
      </div>
      <div className="mt-3 text-body font-semibold text-heading">{card.title}</div>
      <p className="mt-2 text-dense text-text-secondary">{card.desc}</p>
    </Card>
  );

  if (card.external) {
    return (
      <a
        href={card.href}
        target="_blank"
        rel="noopener noreferrer"
        className="group block"
      >
        {body}
      </a>
    );
  }
  return (
    <Link href={card.href} className="group block">
      {body}
    </Link>
  );
}
