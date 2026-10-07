"use client";

import { AlertTriangle, CheckCircle2, Info, Wrench, X, type LucideIcon } from "lucide-react";
import { cn } from "@/components/ui/cn";
import { Button } from "@/components/ui/button";
import { renderAnnouncementMarkdown } from "./markdown";
import type { Announcement } from "@/lib/api/types";

/**
 * Tone is carried by THREE signals at once: a glyph, a word, and a brightness.
 *
 * This design system's rule is "brightness + glyph", never hue alone — roughly one man in
 * twelve cannot separate the incident red from the resolved green, and in a dark UI the two
 * tokens sit at a similar lightness. So every tone ships a lucide icon and a visible,
 * spelled-out label. The label is real text rather than an `aria-label` on the icon because a
 * sighted reader with a colour vision deficiency needs it as much as a screen-reader user
 * does, and only visible text serves both.
 */
const TONES: Record<
  Announcement["tone"],
  { icon: LucideIcon; label: string; classes: string }
> = {
  information: {
    icon: Info,
    label: "Information",
    classes: "border-info/30 bg-info/[0.06] text-info",
  },
  maintenance: {
    icon: Wrench,
    label: "Maintenance",
    classes: "border-warning/30 bg-warning/[0.06] text-warning",
  },
  incident: {
    icon: AlertTriangle,
    label: "Incident",
    classes: "border-err/30 bg-err/[0.06] text-err",
  },
  resolved: {
    icon: CheckCircle2,
    label: "Resolved",
    classes: "border-ok/30 bg-ok/[0.06] text-ok",
  },
};

export interface AnnouncementBannerProps {
  announcement: Announcement;
  /**
   * `shell` spans the full width directly under the app header; `inline` sits inside a tab
   * panel and is a card. The difference is only chrome — border on one edge versus all four,
   * and the page gutter versus the panel's own.
   */
  variant: "shell" | "inline";
  onDismiss?: () => void;
}

export function AnnouncementBanner({
  announcement,
  variant,
  onDismiss,
}: AnnouncementBannerProps) {
  // Fall back rather than throw. `tone` is a closed union in the generated type, but the value
  // came off the wire from a service that ships independently of this build — a tone added
  // upstream would make a bare `TONES[tone]` lookup return undefined and throw on `.icon`,
  // inside the root layout, taking down every route. A banner in the wrong colour beats an app
  // that will not load, and `information` is the right guess for an unrecognised tone: it is
  // the quietest one, so a mis-toned banner under-claims rather than crying incident.
  const tone = TONES[announcement.tone] ?? TONES.information;
  const Icon = tone.icon;

  // The invariant is enforced HERE, not only in the remote parser that currently also enforces
  // it. There is no "I have read it" for an active outage, and a close control would let the
  // reader hide it and then be surprised by it — so the rule belongs to the component that
  // renders the control, where it holds for every Announcement however it was built. Relying
  // on the parser alone means any future path that constructs one (a test double, a cached
  // row, a second source) can reintroduce a dismissible incident with nothing to catch it.
  const dismissible = announcement.dismissible && announcement.tone !== "incident";

  return (
    <div
      // role="alert" ONLY for an incident. `alert` is an assertive live region: it interrupts
      // a screen-reader user mid-sentence, wherever they are on the page. That is correct for
      // "the service is down" and wrong for everything else, because these banners appear on
      // page load and then stay on screen — a maintenance notice that seized the reader's
      // attention every time they opened a page would be a reason to stop using the app.
      // `status` announces politely, at the next pause. run-live.tsx records the same
      // reasoning for its inactivity banner.
      role={announcement.tone === "incident" ? "alert" : "status"}
      data-testid={`announcement-banner-${announcement.placement}`}
      className={cn(
        "flex items-start gap-3",
        tone.classes,
        variant === "shell"
          ? // Matches ServerUnreachable: full bleed, one bottom border, the page gutter. In
            // normal flow below the header, never overlaying the content.
            "border-b px-6 py-2"
          : // Matches the card-style banners in mission-detail.tsx.
            "rounded-md border p-3",
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-label uppercase">{tone.label}</span>
        {/* min-w-0 so this may shrink below its content inside the flex row, and break-words
            so a long unbroken token (a URL an author pasted) wraps instead of pushing the
            whole banner — and with it the page — into horizontal overflow. */}
        <div className="min-w-0 break-words prose-block text-body">
          {renderAnnouncementMarkdown(announcement.body_md)}
        </div>
      </div>
      {/* Only when the publisher marked it dismissible AND it is not an incident — see the
          `dismissible` binding above for why this component owns that second condition. */}
      {dismissible && (
        <Button
          variant="ghost"
          size="icon"
          aria-label="Dismiss announcement"
          onClick={onDismiss}
        >
          <X className="size-4" />
        </Button>
      )}
    </div>
  );
}
