"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { FileText, Scissors } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { pct } from "@/lib/api/format";
import type { CheckVerdict, CriterionScore, GradeResult } from "@/lib/api/types";

// "shared" opens the drawer on just the shared instructions+evidence (used when the judge produced
// no per-criterion breakdown, e.g. it degraded to unavailable); a CriterionScore opens the drawer
// with that criterion's appended line highlighted.
type PromptTarget = CriterionScore | "shared";

// The grading detail: judge-status notes, deterministic checks table, judge criteria, and — on
// demand — the exact prompt fed to the judge for a chosen criterion (one shared drawer, not a box
// per card). Every block self-hides when its data is absent.
export function GradingDetail({ grade: r }: { grade: GradeResult }) {
  const [promptFor, setPromptFor] = useState<PromptTarget | null>(null);
  const [scrollSpan, setScrollSpan] = useState<number | null>(null);
  const criterion = promptFor && promptFor !== "shared" ? promptFor : null;

  // The exact truncated span numbers (derived from the preserved prompt); the count falls back to
  // the recorded field for the rare over-budget path that has no prompt to jump into.
  const truncatedSpans = r.judge_prompt ? truncatedSpanNumbers(r.judge_prompt) : [];
  const truncCount = truncatedSpans.length || (r.spans_truncated ?? 0);
  // Open the prompt drawer on the shared evidence, scrolled to (and ringing) a specific span.
  const openSpan = (n: number) => {
    setScrollSpan(n);
    setPromptFor("shared");
  };
  const openPrompt = (target: PromptTarget) => {
    setScrollSpan(null);
    setPromptFor(target);
  };

  return (
    <>
      {/* ═══ JUDGE STATUS NOTES (verbatim — tests assert these) ═══ */}
      {r.judge_status === "model-not-configured" && (
        <p className="max-w-[68ch] rounded-md border border-primary/30 bg-primary/10 px-3 py-2 text-body text-foreground">
          No LLM judge ran — the judge model isn&apos;t configured.{" "}
          <Link href="/settings?focus=judge" className="text-primary underline">
            Configure it in Settings →
          </Link>
        </p>
      )}
      {r.judge_status === "partial" && (
        <div className="rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-body text-foreground">
          <p className="font-medium">Partial judge result — the displayed score is conservative.</p>
          <p className="mt-1 text-text-secondary">
            {Math.round((r.judge_coverage ?? 0) * 100)}% of rubric weight was scored. The score
            range includes criteria that were unobservable or had invalid judge replies.
          </p>
          {r.judge_detail && (
            <p className="mt-2 break-words text-text-secondary">{r.judge_detail}</p>
          )}
        </div>
      )}
      {r.judge_status !== "ok" &&
        r.judge_status !== "partial" &&
        r.judge_status !== "model-not-configured" && (
        <div className="rounded-md border border-err/30 bg-err/10 px-3 py-2 text-body text-err">
          <p className="font-medium">
            Judge unavailable — the judge half of the score didn’t run.
          </p>
          {r.judge_detail && (
            <p className="mt-2 break-words text-err/90">{r.judge_detail}</p>
          )}
          <Link
            href="/settings?focus=judge"
            className="mt-2 inline-block font-medium text-primary underline underline-offset-2"
          >
            Check the judge model in Settings →
          </Link>
        </div>
      )}

      {/* ═══ SPAN TRUNCATION NOTICE — highlighted call-out with jump-links into the prompt drawer ═══ */}
      {truncCount > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-2 text-body text-text-secondary">
          <Scissors className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
          <p className="max-w-[68ch]">
            <span className="font-medium text-foreground">{truncCount}</span> transcript
            span{truncCount === 1 ? "" : "s"} had a long body shortened (head + tail kept) so the
            evidence fit the judge’s per-span token cap.{" "}
            {truncatedSpans.length > 0 && (
              <>
                Jump to{" "}
                {truncatedSpans.map((sp, idx) => (
                  <Fragment key={sp}>
                    <button
                      type="button"
                      onClick={() => openSpan(sp)}
                      className="font-medium text-primary underline underline-offset-2 hover:text-primary/80"
                    >
                      span #{sp}
                    </button>
                    {idx < truncatedSpans.length - 1 ? ", " : ""}
                  </Fragment>
                ))}
                .{" "}
              </>
            )}
            Every span was still shown to the judge — only the middle of oversized outputs was
            elided.
          </p>
        </div>
      )}

      {/* ═══ CHECKS ═══ */}
      {r.check_breakdown.length > 0 && (
        <section>
          <h2 className="mb-2 text-label uppercase text-text-tertiary">
            Checks
          </h2>
          <Card className="overflow-hidden">
            <Table>
              <THead>
                <TR>
                  <TH>Check</TH>
                  <TH>Op</TH>
                  <TH>Ref</TH>
                  <TH>Value</TH>
                  <TH>Result</TH>
                </TR>
              </THead>
              <TBody>
                {r.check_breakdown.map((c: CheckVerdict) => (
                  <TR key={c.id}>
                    <TD className="font-medium text-foreground">{c.id}</TD>
                    <TD className="font-mono text-text-secondary">{c.op}</TD>
                    <TD className="font-mono text-text-secondary">{c.ref}</TD>
                    <TD className="font-mono text-text-secondary">
                      {String(c.value ?? "—")}
                    </TD>
                    <TD>
                      <Badge variant={c.passed ? "ok" : "err"}>
                        {c.passed ? "pass" : "fail"}
                      </Badge>
                      {c.error && (
                        <p className="mt-1 max-w-[18rem] text-caption text-err">
                          {c.error}
                        </p>
                      )}
                      {(c.blocked_by?.length ?? 0) > 0 && (
                        <p className="mt-1 max-w-[18rem] text-caption text-text-tertiary">
                          Requires: {c.blocked_by?.join(", ")}
                        </p>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Card>
        </section>
      )}

      {/* ═══ JUDGE CRITERIA (each graded in its own call; view its prompt on demand) ═══ */}
      {r.judge_breakdown.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-label uppercase text-text-tertiary">
            Judge criteria
          </h2>
          {r.judge_breakdown.map((c) => {
            const unobservable = c.status === "unobservable" || c.status === "unknown";
            const error = c.status === "error";
            const scored = !unobservable && !error;
            return (
              <Card key={c.criterion_id} className="bg-card p-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-dense text-heading">{c.text}</span>
                  <div className="flex shrink-0 items-center gap-2">
                    {c.weight > 0 && (
                      <span className="text-caption text-text-tertiary">w {c.weight}</span>
                    )}
                    {unobservable ? (
                      <Badge variant="muted">Unobservable</Badge>
                    ) : error ? (
                      <Badge variant="err">Judge error</Badge>
                    ) : (
                      <span className="text-dense text-primary tabular-nums">
                        {pct(c.score)}
                      </span>
                    )}
                  </div>
                </div>
                {scored && (
                  <Progress className="mt-2" value={Math.round((c.score ?? 0) * 100)} />
                )}
                <div className="mt-2 flex items-start justify-between gap-3">
                  {c.reason ? (
                    <p className="text-dense text-text-secondary">
                      {unobservable ? "Platform evidence unavailable: " : error ? "Judge reply error: " : ""}
                      {c.reason}
                    </p>
                  ) : (
                    <span />
                  )}
                  {r.judge_prompt && (
                    <button
                      type="button"
                      onClick={() => openPrompt(c)}
                      className="mt-0.5 flex shrink-0 items-center gap-1 text-caption text-text-tertiary transition-colors hover:text-foreground"
                    >
                      <FileText className="size-3" aria-hidden />
                      view prompt
                    </button>
                  )}
                </div>
              </Card>
            );
          })}
        </section>
      )}

      {/* When the judge produced no criteria (e.g. it degraded) but a prompt was still fed, keep a
          single way in to inspect what was sent. */}
      {r.judge_breakdown.length === 0 && r.judge_prompt && (
        <button
          type="button"
          onClick={() => openPrompt("shared")}
          className="flex items-center gap-1 text-caption text-primary underline-offset-2 hover:underline"
        >
          <FileText className="size-3.5" aria-hidden />
          View the judge prompt
        </button>
      )}

      {/* ═══ ONE shared prompt drawer — the exact input fed to the judge for the chosen criterion ═══ */}
      <Dialog
        open={promptFor !== null}
        onClose={() => {
          setPromptFor(null);
          setScrollSpan(null);
        }}
        size="lg"
        title="Judge prompt"
      >
        {promptFor && <PromptBody grade={r} criterion={criterion} scrollToSpan={scrollSpan} />}
      </Dialog>
    </>
  );
}

function PromptBody({
  grade: r,
  criterion,
  scrollToSpan,
}: {
  grade: GradeResult;
  criterion: CriterionScore | null;
  scrollToSpan?: number | null;
}) {
  const shared = r.judge_prompt ?? "";
  const appended = criterion?.criterion_prompt ?? "";
  const assembled = appended ? `${shared}\n\n### CRITERION\n${appended}` : shared;
  const downloadName = `judge-prompt-${r.run_id}${criterion ? `-${criterion.criterion_id}` : ""}.txt`;
  const truncatedSpans = truncatedSpanNumbers(shared);
  // In-drawer jump target — seeded from the span the drawer was opened at (if any), then driven by
  // the jump-links in the summary so the operator can hop between truncated spans without leaving.
  const [jumpSpan, setJumpSpan] = useState<number | null>(scrollToSpan ?? null);
  return (
    <div className="space-y-3">
      <p className="text-body text-text-secondary">
        Each criterion is graded in its own call: the shared instructions + evidence below, with{" "}
        {criterion ? "this criterion" : "one criterion"} appended as the final message. The shared
        part is identical across every criterion.
      </p>
      {appended && (
        <div className="rounded-md border border-primary/30 bg-primary/10 p-3">
          <p className="mb-2 text-label uppercase text-primary">
            Criterion appended (this call)
          </p>
          <pre className="whitespace-pre-wrap break-words font-mono text-dense text-foreground">
            {appended}
          </pre>
        </div>
      )}
      <div>
        <p className="mb-2 text-label uppercase text-text-tertiary">
          Shared instructions + evidence
        </p>
        {truncatedSpans.length > 0 && (
          <p className="mb-1 text-caption text-primary">
            {truncatedSpans.length} span{truncatedSpans.length === 1 ? "" : "s"} truncated to fit
            the token budget — jump to{" "}
            {truncatedSpans.map((n, idx) => (
              <Fragment key={n}>
                <button
                  type="button"
                  onClick={() => setJumpSpan(n)}
                  aria-current={jumpSpan === n ? "true" : undefined}
                  className={
                    "font-medium underline underline-offset-2 hover:text-primary/80 " +
                    (jumpSpan === n ? "text-foreground" : "text-primary")
                  }
                >
                  #{n}
                </button>
                {idx < truncatedSpans.length - 1 ? ", " : ""}
              </Fragment>
            ))}
            .
          </p>
        )}
        <HighlightedPrompt text={shared} truncated={new Set(truncatedSpans)} scrollTo={jumpSpan} />
      </div>
      <a
        href={`data:text/plain;charset=utf-8,${encodeURIComponent(assembled)}`}
        download={downloadName}
        className="inline-block text-caption text-primary underline-offset-2 hover:underline"
      >
        Download full prompt
      </a>
    </div>
  );
}

// Spans in the preserved prompt are delimited by ⟦span N⟧ markers; a body-capped span carries a
// "[... span body truncated: <n> -> ~<m> tokens ...]" marker (Lever 1). We derive the truncated
// span numbers straight from the prompt text — no backend field or re-grade needed.
//
// The regex requires the marker's colon + digit count, so it matches ONLY a real truncation, never
// the SYSTEM instructions' plain-prose description of the marker ("a span body may be shortened
// with a '[... span body truncated ...]' marker.") — that mention has no colon or digits.
const TRUNC_RE = /span body truncated: \d/i;
const SPAN_OPEN_RE = /⟦span (\d+)⟧/;
const SPAN_CLOSE_RE = /⟦\/span \d+⟧/;

function truncatedSpanNumbers(prompt: string): number[] {
  const found: number[] = [];
  let current: number | null = null;
  for (const line of prompt.split("\n")) {
    const open = line.match(SPAN_OPEN_RE);
    if (open) current = Number(open[1]);
    if (TRUNC_RE.test(line) && current != null && !found.includes(current)) found.push(current);
    if (SPAN_CLOSE_RE.test(line)) current = null;
  }
  return found;
}

/** The shared prompt rendered with truncated spans highlighted: the whole span block gets a soft
 *  tint and the truncation-marker line a stronger one, so the exact elided spans stand out. When
 *  `scrollTo` names a span, its ⟦span N⟧ opener is scrolled into view and ringed. */
function HighlightedPrompt({
  text,
  truncated,
  scrollTo,
}: {
  text: string;
  truncated: Set<number>;
  scrollTo?: number | null;
}) {
  const targetRef = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    // Optional call: jsdom/test envs don't implement scrollIntoView.
    targetRef.current?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  }, [scrollTo, text]);

  let current: number | null = null;
  return (
    <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-deepest p-3 font-mono text-dense text-text-secondary">
      {text.split("\n").map((line, i) => {
        const open = line.match(SPAN_OPEN_RE);
        if (open) current = Number(open[1]);
        const inTruncated = current != null && truncated.has(current);
        const isMarker = inTruncated && TRUNC_RE.test(line);
        const isBoundary = inTruncated && (!!open || SPAN_CLOSE_RE.test(line));
        const isTarget = !!open && current === scrollTo; // opener of the jumped-to span
        // Tint the whole truncated span block; the ⟦span N⟧ boundaries and the truncation marker
        // line get progressively stronger so the elided span reads clearly (not a faint wash).
        const tint = isMarker
          ? "bg-primary/40 font-medium text-foreground"
          : isBoundary
            ? "bg-primary/25 text-foreground"
            : inTruncated
              ? "bg-primary/15"
              : "";
        const className =
          [tint, isTarget ? "rounded-md ring-2 ring-primary" : ""].filter(Boolean).join(" ") ||
          undefined;
        const node = (
          <span key={i} ref={isTarget ? targetRef : undefined} className={className}>
            {line}
            {"\n"}
          </span>
        );
        if (SPAN_CLOSE_RE.test(line)) current = null;
        return node;
      })}
    </pre>
  );
}
