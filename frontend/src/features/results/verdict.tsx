import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/components/ui/cn";
import type { GradeResult } from "@/lib/api/types";

// Hard fails / major deductions / key evidence — the evidence-anchored verdict.
// Renders nothing when the grade carries none of the three.
export function Verdict({ grade: r }: { grade: GradeResult }) {
  if (
    r.hard_fails.length === 0 &&
    r.key_evidence.length === 0 &&
    r.major_deductions.length === 0
  )
    return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {r.hard_fails.length > 0 && (
        <EvidenceCard
          title="Hard fails"
          items={r.hard_fails}
          tone="err"
          marker="✕"
        />
      )}
      {r.major_deductions.length > 0 && (
        <EvidenceCard
          title="Major deductions"
          items={r.major_deductions}
          tone="err"
          marker="−"
        />
      )}
      {r.key_evidence.length > 0 && (
        <EvidenceCard
          title="Key evidence"
          items={r.key_evidence}
          tone="ok"
          marker="✓"
          className={
            r.hard_fails.length === 0 && r.major_deductions.length === 0
              ? "sm:col-span-2"
              : undefined
          }
        />
      )}
    </div>
  );
}

function EvidenceCard({
  title,
  items,
  tone,
  marker,
  className,
}: {
  title: string;
  items: string[];
  tone: "ok" | "err";
  marker: string;
  className?: string;
}) {
  const accent = tone === "ok" ? "#6ee7a8" : "#ff5f57";
  const markerCls = tone === "ok" ? "text-ok" : "text-err";
  return (
    <Card
      className={cn("bg-raised", className)}
      style={{ borderLeftWidth: 3, borderLeftColor: accent }}
    >
      <CardContent className="p-4">
        <h2 className="mb-2 text-label uppercase text-text-tertiary">
          {title}
        </h2>
        <ul className="space-y-2">
          {items.map((it, i) => (
            <li key={i} className="flex gap-2 text-dense text-foreground">
              <span aria-hidden className={cn("shrink-0", markerCls)}>
                {marker}
              </span>
              <span className="min-w-0 break-words">{it}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
