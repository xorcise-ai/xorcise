"use client";

import Link from "next/link";
import { Boxes, Download, FileText, FlaskConical, FolderOpen, Loader2, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import { Progress } from "@/components/ui/progress";
import type { CatalogEntry } from "@/lib/api/types";
import { errorDetail } from "@/lib/api/client";
import {
  formatBytes,
  formatEta,
  pullPhaseLabel,
  usePullMission,
  useUpdateMission,
  type PullMissionState,
} from "./queries";
import { environmentLabel, titleCase } from "./labels";
import { DifficultyBadge } from "./difficulty-badge";

const MAX_TECH = 3;

/** Compact tag text for a validated platform: "linux/amd64" → "amd64" (the os is noise in a
 *  catalog that is linux-only by contract; the full value rides the tooltip). */
export function platformLabel(p: string): string {
  return p.replace(/^linux\//, "");
}

export type PlatformTag = {
  platform: string;
  installed: boolean;
  /** Emulation verdict for the INSTALLED tag only (null elsewhere, or when unknown). */
  emulated: boolean | null;
};

/**
 * The platform tags to render for a mission, marking which one is the installed image.
 *
 * The installed platform is unioned into the validated list (and de-duplicated) so an install
 * still names its architecture even against a pre-contract catalog that offers no list — the
 * one thing a lone "installed" badge could never tell you: WHICH arch you actually have.
 */
export function platformTags(c: CatalogEntry): PlatformTag[] {
  const all =
    c.platform && !c.platforms.includes(c.platform)
      ? [...c.platforms, c.platform]
      : c.platforms;
  return all.map((platform) => {
    const isInstalled = c.installed === true && c.platform === platform;
    return {
      platform,
      installed: isInstalled,
      emulated: isInstalled ? (c.emulated ?? null) : null,
    };
  });
}

const PLATFORMS_TOOLTIP =
  "Validated platforms — verified by the remote build, never a creator claim. " +
  "Individual missions may support fewer platforms than the mission-base.";

const EMULATED_TOOLTIP =
  "This install is not native to this host's architecture; it runs through Docker's " +
  "emulation layer. Functional, but slower and not validated natively.";

/**
 * The secondary "why" line under the single Update available status (§35/UX1): which of the
 * two versions moved — mission, base, or both. Undefined when the catalog didn't say (the
 * digest alone flagged the update) so the badge still renders, just without a tooltip.
 */
export function updateReason(c: CatalogEntry): string | undefined {
  const parts: string[] = [];
  if (c.current_mission_version && c.current_mission_version !== c.mission_version) {
    parts.push(`Mission ${c.mission_version ?? "?"} → ${c.current_mission_version}`);
  }
  if (
    c.current_mission_base_version &&
    c.current_mission_base_version !== c.mission_base_version
  ) {
    parts.push(`Mission base ${c.mission_base_version ?? "?"} → ${c.current_mission_base_version}`);
  }
  return parts.length ? parts.join(" · ") : undefined;
}

/**
 * §35's one action as a button — shared by the card, the list row and the detail banner so
 * every surface that can SAY "update available" can also DO the update. Self-contained
 * feedback (spinner → "Updated." / "Already up to date." / the error detail) in the same
 * caption idiom the pull block uses.
 */
export function UpdateMissionButton({ mission: c }: { mission: CatalogEntry }) {
  const update = useUpdateMission();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        aria-busy={update.isPending}
        disabled={update.isPending}
        onClick={() => update.mutate(c.mission_id)}
      >
        {update.isPending ? (
          <>
            <Loader2 className="size-3.5 motion-safe:animate-spin" />
            Updating…
          </>
        ) : (
          <>
            <RefreshCw className="size-3.5" />
            Update
          </>
        )}
      </Button>
      {update.isSuccess && (
        <span className="text-caption text-ok">
          {update.data.updated ? "Updated to the current release." : "Already up to date."}
        </span>
      )}
      {update.isError && (
        <span className="text-caption text-err">
          {errorDetail(update.error)
            ? `Update failed — ${errorDetail(update.error)}`
            : "Update failed — try again."}
        </span>
      )}
    </span>
  );
}

const ENV_TOOLTIP: Record<string, string> = {
  lab: "Lab — a live sandbox environment spins up for the run.",
  static: "Static — attachment-only; no environment is started.",
};

/**
 * Lab / Static — the environment, given a distinct icon so it's identifiable at a glance.
 *
 * Rendered from `CatalogEntry.type` alone: purely a display of what the catalog row
 * declares, never inferred from the name, the image ref or anything else. A row that
 * doesn't declare a type renders no badge rather than a guess.
 */
function EnvironmentBadge({ type }: { type: string }) {
  const t = type.trim().toLowerCase();
  const Icon = t === "lab" ? FlaskConical : t === "static" ? FileText : Boxes;
  return (
    <Badge variant="muted" className="gap-1" title={ENV_TOOLTIP[t]}>
      <Icon className="size-3" aria-hidden />
      {environmentLabel(type)}
    </Badge>
  );
}

/**
 * What this mission will cost to download, shown while it is still a decision.
 *
 * The card shows the bare figure; the CEILING caveat lives in the tooltip. This is the
 * mission's full compressed size, but missions share base layers — 17 of 19 labs sit on
 * one base image — so the first pull transfers this much and later pulls transfer roughly
 * half. Hedging the label ("up to …") bought accuracy at the cost of reading as vagueness
 * on a first pull, where the figure is exact; the tooltip carries the nuance instead.
 *
 * Renders nothing when the size is unknown (an installed mission, or a catalog that
 * predates the field) rather than a confident "0 B".
 */
function DownloadSize({ mission: c, className }: { mission: CatalogEntry; className?: string }) {
  if (c.download_size_bytes == null) return null;
  // Only worth breaking down when the total actually decomposes. A mission with no
  // attachments would otherwise read "280.5 MB — Image 280.5 MB", stating one number twice.
  const known = [
    c.image_size_bytes != null ? `Image ${formatBytes(c.image_size_bytes)}` : null,
    c.attachments_size_bytes != null
      ? `Attachments ${formatBytes(c.attachments_size_bytes)}`
      : null,
  ].filter(Boolean);
  const parts = known.length > 1 ? known : [];
  // The layer caveat is about the IMAGE. A static mission is an attachment bundle with no
  // layers at all, so its figure is exact and the caveat would describe something that
  // cannot happen. With no breakdown and no caveat there is nothing left to say — omit
  // the tooltip entirely rather than attach an empty one.
  const caveat = c.image_size_bytes != null
    ? "Shared layers already on disk are not re-downloaded, so a later pull transfers less."
    : "";
  const tooltip = [parts.join(" · "), caveat].filter(Boolean).join(". ");
  // No icon: this sits directly beside the Pull button, which already carries the download
  // glyph. A second identical icon read as two separate affordances rather than one action
  // and its cost.
  return (
    <span
      data-testid="mission-download-size"
      title={tooltip || undefined}
      className={cn("whitespace-nowrap text-caption text-text-tertiary", className)}
    >
      {formatBytes(c.download_size_bytes)}
    </span>
  );
}

/**
 * Determinate-when-sized progress + phase/bytes/ETA caption for an in-flight pull.
 * Shared by the catalog card and the detail page so both render the same live job.
 * Falls back to an indeterminate bar while docker hasn't sized the download yet
 * (totals also GROW as layers are discovered, so percent may step backwards).
 */
export function PullProgressBlock({ pull }: { pull: PullMissionState }) {
  return (
    <div className="w-full space-y-1">
      {pull.percent != null ? (
        <Progress value={pull.percent} className="h-1" aria-label="Downloading mission" />
      ) : (
        <Progress indeterminate className="h-1" aria-label="Downloading mission" />
      )}
      <p className="text-caption text-text-tertiary">
        {pullPhaseLabel(pull.phase)}
        {pull.bytesTotal > 0 &&
          ` · ${formatBytes(pull.bytesCurrent)} / ${formatBytes(pull.bytesTotal)}`}
        {pull.etaSeconds != null &&
          pull.etaSeconds > 0 &&
          ` · ~${formatEta(pull.etaSeconds)} left`}
      </p>
    </div>
  );
}

export function MissionCard({ mission: c }: { mission: CatalogEntry }) {
  const pull = usePullMission(c.mission_id);
  // Treat a just-pulled entry as installed immediately, before the catalog refetch lands.
  const installed = c.installed || pull.isSuccess;
  const detailHref = `/missions/detail?id=${encodeURIComponent(c.mission_id)}`;

  const tech = c.technologies.filter(Boolean);
  const shownTech = tech.slice(0, MAX_TECH);
  const extraTech = tech.length - shownTech.length;

  return (
    <Card
      className={cn(
        // `isolate` keeps the card's internal z-layering (stretched link z-10, action z-20) inside
        // its own stacking context — otherwise that z-20 leaks to the root and paints over the
        // sticky filter bar as the card scrolls under it.
        "relative isolate flex h-full flex-col overflow-hidden border-l-2 transition-colors",
        "hover:bg-raised focus-within:ring-2 focus-within:ring-ring",
        installed ? "border-l-ok bg-ok/[0.03]" : "border-l-primary",
      )}
    >
      {/* Whole-card click target (stretched link). Real controls sit above it at z-20 so
         they stay independently clickable — no invalid nested anchors. */}
      <Link
        href={detailHref}
        aria-label={`Open ${c.name}`}
        className="absolute inset-0 z-10 focus-visible:outline-none"
      />

      <div className="flex h-full flex-col gap-3 p-4">
        {/* Provenance + environment + status. CUSTOM marks locally-ingested missions
           only. The environment sits here — not down in the classification row — because
           lab-vs-static is a decision input BEFORE you pull, not a detail you discover
           after installing. */}
        <div className="flex flex-wrap items-center gap-1.5">
          {c.source === "your_own" && <Badge variant="muted">custom</Badge>}
          {c.type && <EnvironmentBadge type={c.type} />}
          <span className="ml-auto flex items-center gap-1.5">
            {/* Base-generation mismatch — surfaced HERE so it is seen before a run is attempted,
                not discovered as a failed run-create. The hint (reinstall the mission, or update
                XORCISE) rides the tooltip; the detail page spells it out. */}
            {c.compatible === false && (
              <Badge variant="warn" title={c.compat_hint ?? undefined}>
                update required
              </Badge>
            )}
            {/* §34/§35: digest-driven, ONE primary status. Suppressed while the row is
                incompatible — "update required" already demands the same single action. */}
            {c.compatible !== false && c.update_available === true && (
              <Badge variant="muted" title={updateReason(c)}>
                update available
              </Badge>
            )}
            {c.emulated === true && (
              <Badge variant="warn" title={EMULATED_TOOLTIP}>
                emulated
              </Badge>
            )}
            {installed ? (
              <Badge variant="ok">installed</Badge>
            ) : (
              <Badge variant="muted">available</Badge>
            )}
          </span>
        </div>

        {/* Name + description (clamped to two lines).
            Spacing sits on the brand's 4/8/12/16/24 ladder (Module 07 §7.1). The title
            and its summary are one group, so they take 8px — not the 4px "tight" step,
            which read as the summary being crammed under the name. The title also drops
            its ad-hoc leading: a two-word card title needs no compression, and neither
            1.25 nor 1.375 is a rung on the §5.3 line-height ladder — text-body's 1.6 is. */}
        <div>
          <p className="break-words text-body font-semibold text-heading">
            {c.name}
          </p>
          {c.summary && (
            <p className="mt-2 line-clamp-2 text-dense text-text-secondary">
              {c.summary}
            </p>
          )}
        </div>

        {/* Classification — consistent order; omit anything missing (no empty badges).
            Platform tags sit here (PV5/AS2): validated architectures are a decision input
            like difficulty — an arm64 operator picks differently when there is no native
            image. Absent on a pre-contract catalog. */}
        {(c.specialty || c.proficiency || c.platforms.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {c.specialty && <Badge variant="info">{titleCase(c.specialty)}</Badge>}
            {c.proficiency && <DifficultyBadge proficiency={c.proficiency} />}
            {c.platforms.map((p) => (
              <Badge key={p} variant="muted" className="font-mono" title={PLATFORMS_TOOLTIP}>
                {platformLabel(p)}
              </Badge>
            ))}
          </div>
        )}

        {/* Technology tags — a few, then +N */}
        {shownTech.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {shownTech.map((t) => (
              <span
                key={t}
                className="whitespace-nowrap rounded border border-border bg-raised px-1.5 py-0.5 font-mono text-caption text-text-tertiary"
              >
                {t}
              </span>
            ))}
            {extraTech > 0 && (
              <span className="text-caption text-text-tertiary">+{extraTech}</span>
            )}
          </div>
        )}

        {/* Action — bottom aligned, above the stretched link (z-20). Compact by design.
           A pull runs as a background job; while it's live the shared job poll drives a
           determinate bar (bytes + ETA), falling back to indeterminate until docker reports
           a total. */}
        <div className="relative z-20 mt-auto flex flex-col items-start gap-2 border-t border-border pt-3">
          {installed ? (
            <span className="flex flex-wrap items-center gap-2">
              <Link href={detailHref} className={cn(buttonVariants({ size: "sm" }))}>
                <FolderOpen className="size-3.5" />
                Open
              </Link>
              {/* The badge above SAYS it; this DOES it — §35's one update action, in place. */}
              {c.update_available === true && <UpdateMissionButton mission={c} />}
            </span>
          ) : pull.isPulling ? (
            <div className="w-full space-y-2">
              <div className="flex items-center gap-1.5">
                <Button size="sm" disabled aria-busy>
                  <Loader2 className="size-3.5 motion-safe:animate-spin" />
                  Pulling…
                </Button>
                {pull.canCancel && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => pull.cancel()}
                    disabled={pull.isCancelling}
                  >
                    {pull.isCancelling ? "Cancelling…" : "Cancel"}
                  </Button>
                )}
              </div>
              <PullProgressBlock pull={pull} />
            </div>
          ) : (
            // Size sits immediately beside the button, not flush right: it is the cost OF
            // this action, and pushing it to the far edge separated the two enough that it
            // read as unrelated card metadata rather than a caption on the button.
            <div className="flex w-full flex-wrap items-center gap-x-2 gap-y-1">
              <Button size="sm" onClick={() => pull.start()}>
                <Download className="size-3.5" />
                Pull
              </Button>
              <DownloadSize mission={c} />
            </div>
          )}
          {pull.isSuccess && (
            <span className="text-caption text-ok">Installed — ready to run.</span>
          )}
          {pull.isCancelled && (
            <span className="text-caption text-text-tertiary">Pull cancelled.</span>
          )}
          {pull.isError && (
            <span className="text-caption text-err">
              {pull.errorDetail
                ? `Pull failed — ${pull.errorDetail}`
                : "Pull failed — try again."}
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}

/**
 * Compact one-line list row — the catalog's alternative to the card grid. Same data and same
 * pull machinery as {@link MissionCard}, laid out as a dense row: status dot · name · (specialty
 * / proficiency / environment, on ≥md) · install status · Open/Pull action. The pull progress
 * drops under the row while a job is in flight, mirroring the card.
 */
export function MissionRow({ mission: c }: { mission: CatalogEntry }) {
  const pull = usePullMission(c.mission_id);
  const installed = c.installed || pull.isSuccess;
  const detailHref = `/missions/detail?id=${encodeURIComponent(c.mission_id)}`;
  return (
    <li>
      <div
        className={cn(
          "group relative isolate flex items-center gap-3 rounded-md border border-l-2 border-border px-3 py-2 transition-colors hover:bg-raised focus-within:ring-2 focus-within:ring-ring",
          installed ? "border-l-ok" : "border-l-primary",
        )}
      >
        {/* Whole-row click target; real controls sit above it at z-20. */}
        <Link
          href={detailHref}
          aria-label={`Open ${c.name}`}
          className="absolute inset-0 z-10 focus-visible:outline-none"
        />
        <span
          className={cn("size-1.5 shrink-0 rounded-full", installed ? "bg-ok" : "bg-primary")}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate text-row font-medium text-heading">
          {c.name}
        </span>
        <div className="hidden shrink-0 items-center gap-1.5 md:flex">
          {c.specialty && (
            <Badge variant="info" className="capitalize">
              {titleCase(c.specialty)}
            </Badge>
          )}
          {c.proficiency && <DifficultyBadge proficiency={c.proficiency} />}
          {c.type && <EnvironmentBadge type={c.type} />}
        </div>
        {/* Same status badges as the card — the two layouts must not disagree about one
            mission. Compat first (the stronger claim), then the update state, then emulation. */}
        {c.compatible === false && (
          <Badge variant="warn" className="hidden shrink-0 sm:inline-flex" title={c.compat_hint ?? undefined}>
            update required
          </Badge>
        )}
        {c.compatible !== false && c.update_available === true && (
          <Badge variant="muted" className="hidden shrink-0 sm:inline-flex" title={updateReason(c)}>
            update available
          </Badge>
        )}
        {c.emulated === true && (
          <Badge variant="warn" className="hidden shrink-0 sm:inline-flex" title={EMULATED_TOOLTIP}>
            emulated
          </Badge>
        )}
        {!installed && !pull.isPulling && (
          <span className="hidden shrink-0 lg:inline-flex">
            <DownloadSize mission={c} />
          </span>
        )}
        <Badge variant={installed ? "ok" : "muted"} className="hidden shrink-0 sm:inline-flex">
          {installed ? "installed" : "available"}
        </Badge>
        <div className="relative z-20 flex shrink-0 items-center gap-2">
          {installed ? (
            <>
              {c.update_available === true && <UpdateMissionButton mission={c} />}
              <Link href={detailHref} className={cn(buttonVariants({ size: "sm" }))}>
                <FolderOpen className="size-3.5" />
                Open
              </Link>
            </>
          ) : pull.isPulling ? (
            <Button size="sm" disabled aria-busy>
              <Loader2 className="size-3.5 motion-safe:animate-spin" />
              Pulling…
            </Button>
          ) : (
            <Button size="sm" onClick={() => pull.start()}>
              <Download className="size-3.5" />
              Pull
            </Button>
          )}
        </div>
      </div>
      {(pull.isPulling || pull.isError) && (
        <div className="px-3 pb-2 pt-1">
          {pull.isError ? (
            <p className="text-caption text-err">
              {pull.errorDetail ? `Pull failed — ${pull.errorDetail}` : "Pull failed — try again."}
            </p>
          ) : (
            <PullProgressBlock pull={pull} />
          )}
        </div>
      )}
    </li>
  );
}
