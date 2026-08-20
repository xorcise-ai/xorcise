import { Check, Minus, X, type LucideIcon } from "lucide-react";
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
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {r.hard_fails.length > 0 && (
        <EvidenceCard
          title="Hard fails"
          items={r.hard_fails}
          tone="err"
          marker={X}
        />
      )}
      {r.major_deductions.length > 0 && (
        <EvidenceCard
          title="Major deductions"
          items={r.major_deductions}
          tone="err"
          marker={Minus}
        />
      )}
      {r.key_evidence.length > 0 && (
        <EvidenceCard
          title="Key evidence"
          items={r.key_evidence}
          tone="ok"
          marker={Check}
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
  marker: Marker,
  className,
}: {
  title: string;
  items: string[];
  tone: "ok" | "err";
  /* A lucide mark, not a unicode glyph: the list marker is an icon, and every icon in the
     console comes from lucide. */
  marker: LucideIcon;
  className?: string;
}) {
  // The accent is the status token itself — the inline style needs a real CSS colour, and
  // var() reaches the same value the `text-ok` / `text-err` utilities do.
  const accent = tone === "ok" ? "var(--color-ok)" : "var(--color-err)";
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
              <Marker
                aria-hidden
                className={cn("mt-0.5 size-3.5 shrink-0", markerCls)}
              />
              <span className="min-w-0 break-words">{it}</span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
