"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Boxes,
  Cpu,
  Crosshair,
  Download,
  FileText,
  FlaskConical,
  Flag,
  Lightbulb,
  Loader2,
  ListChecks,
  Map as MapIcon,
  Package,
  Paperclip,
  Plus,
  Server,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import { Skeleton, SkeletonRows } from "@/components/ui/skeleton";
import { Reveal } from "@/components/ui/reveal";
import { NotFoundState } from "@/components/layout/not-found-state";
import type { CatalogEntry, MissionManifest } from "@/lib/api/types";
import { PullProgressBlock } from "./mission-card";
import {
  useMission,
  useMissionManifest,
  useDeleteMission,
  usePullMission,
} from "./queries";
import {
  describeCheck,
  environmentLabel,
  isLab,
  titleCase,
} from "./labels";
import { DifficultyBadge } from "./difficulty-badge";

/** Trim a numeric weight to a tidy string (integer stays integer, else 2 dp). */
function fmtWeight(w: number): string {
  return Number.isInteger(w) ? String(w) : String(Number(w.toFixed(2)));
}

/** The page's primary call-to-action. This page is dedicated to the mission itself, so for a
 *  not-yet-installed entry it leads with the pull — "Pull & Create Run" (starting a run pulls the
 *  mission first) — and only an already-installed mission reads the plain "Create Run". */
function CreateRunButton({
  id,
  installed,
  className,
}: {
  id: string;
  installed: boolean;
  className?: string;
}) {
  return (
    <Link
      href={`/runs/new?mission=${encodeURIComponent(id)}`}
      className={cn(buttonVariants({ size: "default" }), className)}
    >
      {installed ? <Plus className="size-4" /> : <Download className="size-4" />}
      {installed ? "Create Run" : "Pull & Create Run"}
    </Link>
  );
}

export function MissionDetail({ id }: { id: string | null }) {
  const router = useRouter();
  const mission = useMission(id ?? "");
  const manifest = useMissionManifest(id ?? "");
  // Keyed by mission_id: the catalog card's pull and this button share ONE live job,
  // so starting a pull in either view disables both and both render its progress.
  const pull = usePullMission(id ?? "");
  const del = useDeleteMission();
  const [confirming, setConfirming] = useState(false);

  if (!id)
    return (
      <NotFoundState
        title="Mission not found"
        message="No mission specified."
        backHref="/missions"
        backLabel="Back to catalog"
      />
    );
  if (mission.isLoading)
    return (
      <div role="status" aria-label="Loading mission…" className="min-w-0 space-y-4 p-6">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="text" className="w-full" />
        <Skeleton variant="text" className="w-2/3" />
        <SkeletonRows count={4} className="mt-6" />
      </div>
    );
  if (!mission.data)
    return (
      <NotFoundState
        title="Mission not found"
        message={`No mission “${id}”.`}
        backHref="/missions"
        backLabel="Back to catalog"
      />
    );

  const c = mission.data;
  const m = manifest.data;
  const isLibraryPreview = c.source === "library" && !c.installed;
  const envType = c.type ?? m?.metadata.type ?? null;
  // Skills — fall back to the full manifest (same as envType) so a not-yet-pulled library
  // mission lists its security skills too, not just installed ones whose catalog row carries them.
  const skills = c.skills.length > 0 ? c.skills : (m?.metadata.skills ?? []);

  async function confirmDelete() {
    await del.mutateAsync(c.mission_id);
    router.push("/missions");
  }

  return (
    <div className="min-w-0 space-y-6 p-6">
      {/* ── Header ── */}
      <header className="flex items-start gap-4">
        <Link
          href="/missions"
          aria-label="Back to catalog"
          className="mt-1 rounded p-1 text-text-tertiary transition-colors hover:bg-raised hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-label uppercase text-text-tertiary">
            Mission
          </p>

          {/* Title + primary/secondary actions */}
          <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
            <h1 className="text-lead text-heading">
              {c.name}
            </h1>
            <div className="flex shrink-0 items-center gap-2">
              <CreateRunButton id={c.mission_id} installed={c.installed} />
              {c.installed ? (
                confirming ? (
                  <>
                    <span className="text-dense text-text-secondary">
                      Delete this mission?
                    </span>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={confirmDelete}
                      disabled={del.isPending}
                    >
                      {del.isPending ? "Deleting…" : "Yes, delete"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setConfirming(false)}
                    >
                      Cancel
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setConfirming(true)}
                  >
                    <Trash2 className="size-3.5" />
                    Delete
                  </Button>
                )
              ) : (
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    aria-busy={pull.isPulling}
                    onClick={() => {
                      if (pull.isPulling) return;
                      pull.start();
                    }}
                    disabled={pull.isPulling}
                  >
                    {pull.isPulling ? (
                      <>
                        <Loader2 className="size-3.5 motion-safe:animate-spin" />
                        Pulling…
                      </>
                    ) : (
                      "Pull"
                    )}
                  </Button>
                  {pull.canCancel && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => pull.cancel()}
                      disabled={pull.isCancelling}
                    >
                      {pull.isCancelling ? "Cancelling…" : "Cancel"}
                    </Button>
                  )}
                </div>
              )}
            </div>
          </div>

          {del.isError && (
            <p className="mt-1 text-body text-err">Delete failed — try again.</p>
          )}
          {pull.isCancelled && (
            <p className="mt-1 text-body text-text-tertiary">Pull cancelled.</p>
          )}
          {pull.isError && (
            <p className="mt-1 text-body text-err">
              {pull.errorDetail
                ? `Pull failed — ${pull.errorDetail}`
                : "Pull failed — try again."}
            </p>
          )}
          {pull.isPulling && (
            <div className="mt-2 max-w-sm">
              <PullProgressBlock pull={pull} />
            </div>
          )}

          {/* Classification badges — only what exists */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {envType && (
              <Badge variant="default" className="gap-1">
                {isLab(envType) ? (
                  <FlaskConical className="size-3" />
                ) : (
                  <FileText className="size-3" />
                )}
                {environmentLabel(envType)}
              </Badge>
            )}
            {c.proficiency && <DifficultyBadge proficiency={c.proficiency} />}
            {c.specialty && <Badge variant="info">{titleCase(c.specialty)}</Badge>}
            {c.installed ? (
              <Badge variant="ok">installed</Badge>
            ) : (
              <Badge variant="muted">available</Badge>
            )}
            <Badge variant="muted">
              {c.source === "library" ? "xorcise remote" : "your own"}
            </Badge>
            <span className="whitespace-nowrap font-mono text-dense text-text-tertiary">
              {c.mission_id}
            </span>
          </div>
        </div>
      </header>

      {/* Preview banner — un-installed library entries */}
      {isLibraryPreview && (
        <Card className="min-w-0 border-primary/30 bg-primary/[0.06]">
          <CardContent className="flex min-w-0 items-start gap-3">
            <Package className="mt-0.5 size-5 shrink-0 text-primary" />
            <div className="min-w-0 space-y-2">
              <p className="text-body font-semibold text-heading">
                Preview — not yet installed
              </p>
              <p className="max-w-full break-words text-body text-text-secondary">
                This mission lives in the XORCISE remote library. Pull it to install
                locally, or just start a run — it’s pulled automatically on start.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Objective (agent mission, from the manifest) */}
      {m?.metadata.objective && (
        <Section
          icon={Crosshair}
          title="Objective"
          helpText="Provided to the agent during the mission run."
        >
          <p className="max-w-full break-words text-body leading-[1.7] text-foreground">
            {m.metadata.objective}
          </p>
        </Section>
      )}

      {/* Description (user-facing summary) */}
      {c.summary && (
        <Section
          icon={FileText}
          title="Description"
          helpText="Human-readable context only. This is not provided to the agent during the mission run."
        >
          <p className="max-w-full break-words text-body leading-[1.7] text-foreground">{c.summary}</p>
        </Section>
      )}

      {/* Skills and Details — precedence: SPECIALTY (the mission's professional domain, the
          headline) → Security skills → Technologies. Difficulty + Environment ride alongside the
          specialty as supporting badges rather than a separate metadata block. */}
      <Section icon={Boxes} title="Skills and Details">
        <div className="space-y-4">
          {/* 1 — Specialty: the headline. "AI Security" is what the mission IS about. */}
          {c.specialty && (
            <div className="space-y-2">
              <SubLabel>Specialty</SubLabel>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-base font-semibold leading-snug text-heading">
                  {titleCase(c.specialty)}
                </span>
                {c.proficiency && <DifficultyBadge proficiency={c.proficiency} />}
                {envType && (
                  <Badge variant="muted" className="gap-1">
                    {isLab(envType) ? (
                      <FlaskConical className="size-3" />
                    ) : (
                      <FileText className="size-3" />
                    )}
                    {environmentLabel(envType)}
                  </Badge>
                )}
              </div>
            </div>
          )}

          {/* 2 — Security skills: the amber treatment */}
          <div className="space-y-2">
            <SubLabel>Security skills</SubLabel>
            {skills.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {skills.map((s) => (
                  <span
                    key={s}
                    className="rounded-full border border-primary/25 bg-primary/10 px-2.5 py-0.5 text-caption text-primary"
                  >
                    {titleCase(s)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-dense text-text-tertiary">No skills listed.</p>
            )}
          </div>

          {/* 3 — Technologies: distinct mono chips, the secondary detail */}
          <div className="space-y-2">
            <SubLabel>Technologies</SubLabel>
            {c.technologies.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {c.technologies.map((t) => (
                  <span
                    key={t}
                    className="rounded border border-border bg-raised px-1.5 py-0.5 font-mono text-caption text-text-secondary"
                  >
                    {t}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-dense text-text-tertiary">None listed.</p>
            )}
          </div>
        </div>
      </Section>

      {/* Rich manifest sections (fetched from the endpoint / installed copy) */}
      {manifest.isLoading && (
        <div role="status" aria-label="Loading mission data…" className="px-1">
          <SkeletonRows count={3} />
        </div>
      )}
      {m && (
        <Reveal className="space-y-6">
          <ManifestSections m={m} envType={envType} />
        </Reveal>
      )}

      {/* Create a run — prominent CTA at the bottom too */}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-5">
        <CreateRunButton id={c.mission_id} installed={c.installed} />
        {!c.installed && (
          <span className="min-w-0 max-w-full flex-1 break-words text-body text-text-tertiary">
            XORCISE pulls the mission first, then starts the run — or use Pull
            above to install it without running.
          </span>
        )}
      </div>
    </div>
  );
}

function ManifestSections({
  m,
  envType,
}: {
  m: MissionManifest;
  envType: string | null;
}) {
  const rubricHasWeights = m.rubric.some((r) => r.weight != null);
  const totalWeight = m.rubric.reduce((s, r) => s + (r.weight ?? 0), 0);
  const checkTitles = new Map(
    m.checks.map((check) => [check.id, describeCheck(check).title]),
  );

  return (
    <>
      {/* Environment — user-facing only (compose file + entry networks are hidden) */}
      {envType && (
        <Section icon={Server} title="Environment">
          <div className="flex items-center gap-2">
            {isLab(envType) ? (
              <FlaskConical className="size-4 text-primary" />
            ) : (
              <FileText className="size-4 text-primary" />
            )}
            <span className="text-body font-medium text-heading">
              {environmentLabel(envType)}
            </span>
          </div>
          <p className="mt-2 max-w-full break-words text-body text-text-secondary">
            {isLab(envType)
              ? "Runs a live, containerized lab environment the agent connects to over the network."
              : "No live environment — the agent works from the provided attachments only."}
          </p>
        </Section>
      )}

      {m.artifacts.length > 0 && (
        <Section icon={ListChecks} title="Expected Artifacts">
          <div className="space-y-2">
            {m.artifacts.map((a) => (
              <div
                key={a.name}
                className={cn(
                  "rounded-lg border px-3 py-2",
                  a.required
                    ? "border-primary/25 bg-primary/[0.04]"
                    : "border-border bg-raised/40",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 break-all font-mono text-dense text-heading">
                    {a.name}
                  </span>
                  <Badge variant={a.required ? "default" : "muted"}>
                    {a.required ? "Required" : "Optional"}
                  </Badge>
                </div>
                {a.description && (
                  <p className="mt-2 max-w-full break-words text-dense text-text-secondary">
                    {a.description}
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {m.rubric.length > 0 && (
        <Section icon={ListChecks} title="Rubric">
          <ol className="space-y-2">
            {m.rubric.map((r, i) => (
              <li
                key={r.id}
                className="flex items-start gap-3 rounded-lg border border-border bg-raised/40 px-3 py-2"
              >
                <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 font-mono text-caption text-primary">
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 break-words text-dense text-foreground">
                  {r.text}
                </span>
                {r.weight != null && (
                  <span className="shrink-0 text-right">
                    <span className="block text-label uppercase text-text-tertiary">
                      Weight
                    </span>
                    <span className="font-mono text-dense text-heading">
                      {fmtWeight(r.weight)}
                    </span>
                  </span>
                )}
              </li>
            ))}
          </ol>
          {rubricHasWeights && (
            <div className="mt-3 flex items-center justify-end gap-1 border-t border-border pt-2 text-dense">
              <span className="text-text-tertiary">Total weight:</span>
              <span className="font-mono text-heading">
                {fmtWeight(totalWeight)}
              </span>
            </div>
          )}
        </Section>
      )}

      {m.checks.length > 0 && (
        <Section icon={ShieldCheck} title="Deterministic Checks">
          <div className="space-y-2">
            {m.checks.map((ck) => {
              const d = describeCheck(ck);
              // `?? []` keeps the detail page compatible with manifests returned by an older
              // remote while clients and the catalog roll out the additive `requires` field.
              const requirements = (ck.requires ?? []).map((id) => ({
                id,
                title: checkTitles.get(id) ?? titleCase(id),
              }));
              return (
                <article
                  key={ck.id}
                  aria-label={`${d.title} deterministic check`}
                  className="rounded-lg border border-border bg-raised/40 px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <ShieldCheck className="size-3.5 shrink-0 text-primary" />
                    <span className="text-dense font-semibold text-heading">
                      {d.title}
                    </span>
                    {ck.weight != null && (
                      <span className="ml-auto font-mono text-caption text-text-tertiary">
                        Weight {fmtWeight(ck.weight)}
                      </span>
                    )}
                  </div>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-dense">
                    <dt className="text-text-tertiary">Rule</dt>
                    <dd className="min-w-0 break-all font-mono text-text-secondary">{d.rule}</dd>
                    <dt className="text-text-tertiary">Source</dt>
                    <dd className="min-w-0 break-words text-text-secondary">{d.source}</dd>
                    {requirements.length > 0 && (
                      <>
                        <dt className="text-text-tertiary">Requires</dt>
                        <dd className="flex min-w-0 flex-wrap gap-1.5">
                          {requirements.map((requirement) => (
                            <span
                              key={requirement.id}
                              className="inline-flex items-center gap-1 rounded border border-primary/25 bg-primary/[0.06] px-1.5 py-0.5 font-mono text-caption text-heading"
                            >
                              <ShieldCheck className="size-3 shrink-0 text-primary" />
                              {requirement.title}
                            </span>
                          ))}
                        </dd>
                      </>
                    )}
                  </dl>
                </article>
              );
            })}
          </div>
        </Section>
      )}

      {m.intel.length > 0 && (
        <Section icon={Lightbulb} title="Intel">
          <ol className="space-y-2">
            {m.intel.map((h, i) => (
              <li key={h.id}>
                <details className="group rounded-lg border border-border bg-raised/40">
                  <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-dense [&::-webkit-details-marker]:hidden">
                    <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 font-mono text-caption text-primary">
                      {i + 1}
                    </span>
                    <span className="font-medium text-heading">
                      Reveal intel {i + 1}
                    </span>
                    <span className="ml-auto text-caption text-text-tertiary group-open:hidden">
                      Show
                    </span>
                    <span className="ml-auto hidden text-caption text-text-tertiary group-open:inline">
                      Hide
                    </span>
                  </summary>
                  <p className="break-words border-t border-border px-3 py-2 pl-10 text-body text-text-secondary">
                    {h.text}
                  </p>
                </details>
              </li>
            ))}
          </ol>
        </Section>
      )}

      {m.attachments.length > 0 && (
        <Section icon={Paperclip} title="Attachments">
          <ul className="space-y-2">
            {m.attachments.map((a) => (
              <li
                key={a.name}
                className="flex items-center gap-2 rounded-lg border border-border bg-raised/40 px-3 py-2"
              >
                <Paperclip className="size-3.5 shrink-0 text-text-tertiary" />
                <span className="min-w-0 flex-1 break-all font-mono text-dense text-heading">
                  {a.name}
                </span>
                {a.media_type && <Badge variant="muted">{a.media_type}</Badge>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {m.terrain && (
        <Section icon={MapIcon} title="Terrain">
          <TerrainPreview terrain={m.terrain} />
        </Section>
      )}
    </>
  );
}

/* ── Terrain preview ── */

type FlowStep = {
  id: string;
  label: string;
  sub: string;
  objective: boolean;
  isAgent: boolean;
  edgeFull?: string;
  edgeCaption?: string;
};

const str = (x: unknown): string => (typeof x === "string" ? x : "");

/** A short "PROTO :port" caption pulled from a free-text edge label (ports aren't structured). */
function edgeCaption(label: string): string {
  const port = label.match(/:\d{2,5}\b/)?.[0] ?? "";
  const proto =
    label.match(
      /\b(HTTPS?|SSH|FTP|SMB|RDP|DNS|TCP|UDP|SSRF|SQLi?|LDAP|SMTP|gRPC|WS|API)\b/i,
    )?.[0] ?? "";
  return [proto, port].filter(Boolean).join(" ");
}

/**
 * Best-effort linear flow (Agent → Service → …) from the loosely-typed terrain graph.
 * Returns null when there aren't enough connected edges to draw one — the caller then
 * falls back to the summary text.
 */
function buildFlow(terrain: NonNullable<MissionManifest["terrain"]>): FlowStep[] | null {
  const nodes = new Map<string, { label: string; sub: string; objective: boolean }>();
  for (const n of terrain.nodes ?? []) {
    const id = str((n as Record<string, unknown>).id);
    if (!id) continue;
    const type = str((n as Record<string, unknown>).type);
    nodes.set(id, {
      label: type ? titleCase(type) : titleCase(id),
      sub: id,
      objective: (n as Record<string, unknown>).objective === true,
    });
  }

  const edges = (terrain.edges ?? [])
    .map((e) => {
      const r = e as Record<string, unknown>;
      return { src: str(r.src), dst: str(r.dst), label: str(r.label) };
    })
    .filter((e) => e.src && e.dst);
  if (edges.length === 0) return null;

  const out = new Map<string, { dst: string; label: string }[]>();
  const incoming = new Set<string>();
  for (const e of edges) {
    if (!out.has(e.src)) out.set(e.src, []);
    out.get(e.src)!.push({ dst: e.dst, label: e.label });
    incoming.add(e.dst);
  }

  const start =
    (out.has("agent") && "agent") ||
    [...out.keys()].find((k) => !incoming.has(k)) ||
    edges[0].src;

  const steps: FlowStep[] = [];
  const seen = new Set<string>();
  let cur: string | undefined = start;
  let edgeFull: string | undefined;
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const meta = nodes.get(cur);
    const isAgent = cur === "agent";
    steps.push({
      id: cur,
      label: meta?.label ?? (isAgent ? "Agent" : titleCase(cur)),
      sub: meta?.sub ?? cur,
      objective: meta?.objective ?? false,
      isAgent,
      edgeFull,
      edgeCaption: edgeFull ? edgeCaption(edgeFull) : undefined,
    });
    const nexts = out.get(cur);
    if (!nexts || nexts.length === 0) break;
    const nxt = nexts.find((n) => !seen.has(n.dst)) ?? nexts[0];
    edgeFull = nxt.label || undefined;
    cur = nxt.dst;
  }

  return steps.length >= 2 ? steps : null;
}

function TerrainPreview({
  terrain,
}: {
  terrain: NonNullable<MissionManifest["terrain"]>;
}) {
  const flow = buildFlow(terrain);
  const summary = terrain.summary?.trim();

  if (!flow && !summary) {
    return (
      <p className="text-dense text-text-tertiary">
        No terrain map available for this mission.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {flow && (
        <div className="flex flex-wrap items-stretch gap-2 rounded-lg border border-border bg-background/60 p-3">
          {flow.map((s, i) => (
            <Fragment key={s.id}>
              {i > 0 && (
                <div
                  className="flex flex-col items-center justify-center gap-1 self-center"
                  title={s.edgeFull}
                >
                  <ArrowRight className="size-4 text-text-tertiary" />
                  {s.edgeCaption && (
                    <span className="font-mono text-caption text-text-tertiary">
                      {s.edgeCaption}
                    </span>
                  )}
                </div>
              )}
              <div
                className={cn(
                  "flex min-w-[92px] flex-col items-center gap-1 rounded-lg border px-3 py-2 text-center",
                  s.objective
                    ? "border-primary/40 bg-primary/[0.08]"
                    : s.isAgent
                      ? "border-tool/40 bg-tool/[0.08]"
                      : "border-border bg-raised",
                )}
              >
                {s.isAgent ? (
                  <Bot className="size-4 text-tool" />
                ) : s.objective ? (
                  <Flag className="size-4 text-primary" />
                ) : (
                  <Server className="size-4 text-text-secondary" />
                )}
                <span className="text-dense text-heading">{s.label}</span>
                {s.sub && s.sub.toLowerCase() !== s.label.toLowerCase() && (
                  <span className="font-mono text-caption text-text-tertiary">
                    {s.sub}
                  </span>
                )}
              </div>
            </Fragment>
          ))}
        </div>
      )}
      {summary && (
        <p className="max-w-full break-words text-body text-text-secondary">{summary}</p>
      )}
    </div>
  );
}

/* ── small layout helpers ── */

function Section({
  icon: Icon,
  title,
  helpText,
  children,
}: {
  icon: typeof Cpu;
  title: string;
  helpText?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="min-w-0">
      <CardContent className="min-w-0 space-y-3">
        <div>
          <div className="flex items-center gap-2">
            <Icon className="size-4 text-primary" />
            <h2 className="text-body font-semibold text-heading">{title}</h2>
          </div>
          {helpText && (
            <p className="mt-2 max-w-full break-words text-body text-text-tertiary">
              {helpText}
            </p>
          )}
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function SubLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-label uppercase text-text-tertiary">
      {children}
    </p>
  );
}
