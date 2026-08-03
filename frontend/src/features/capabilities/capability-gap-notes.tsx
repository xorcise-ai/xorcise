"use client";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HARNESSES } from "@/features/agents/harnesses";
import { DISPLAY_GROUPS, groupLevel, groupNote } from "./capability-groups";
import { useCapabilities } from "./use-capabilities";

const UNKNOWN_LINE =
  "Unknown harness — generic adapter; telemetry profile not verified.";

/** The selected harness's honest limitations as VISIBLE text (not tooltips): one line per
 * noted partial/unsupported display group, note verbatim from the adapter profile
 * (single-sourced — same strings the judge disclosure uses). Un-noted structural gaps are
 * omitted here; the matrix above already shows them struck. */
export function CapabilityGapNotes({ selectedKind }: { selectedKind: string }) {
  const { byName, profiles, isError } = useCapabilities();
  const isBuiltIn = HARNESSES.some((h) => h.kind === selectedKind);
  const profile = isBuiltIn ? byName?.get(selectedKind) : undefined;

  if (isError) {
    return (
      <Card className="space-y-2 p-4">
        <h3 className="text-label uppercase text-text-tertiary">What you won&apos;t see</h3>
        <p className="text-dense text-text-secondary">{UNKNOWN_LINE}</p>
      </Card>
    );
  }

  if (!profiles) {
    return (
      <Card className="p-4">
        <Skeleton className="h-4 w-40" />
      </Card>
    );
  }

  const rows = profile
    ? DISPLAY_GROUPS.flatMap((group) => {
        const level = groupLevel(profile, group);
        const note = groupNote(profile, group);
        if (level === "supported" || !note) return [];
        return [{ id: group.id, label: group.label, level, note }];
      })
    : [];

  return (
    <Card className="space-y-2 p-4">
      <h3 className="text-label uppercase text-text-tertiary">What you won&apos;t see</h3>
      {!profile ? (
        <p className="text-dense text-text-secondary">{UNKNOWN_LINE}</p>
      ) : rows.length === 0 ? (
        <p className="text-dense text-text-tertiary">
          No further notes — structural gaps are shown in the matrix above.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.id} className="text-dense text-text-secondary">
              <span className="text-foreground">{r.label}</span>
              {r.level === "partial" && (
                <Badge variant="warn" className="ml-1.5 align-middle">partial</Badge>
              )}
              {" — "}
              {r.note}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
