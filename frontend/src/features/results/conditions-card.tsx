import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ResultConditions } from "@/lib/api/types";

// Discloses the meaningful conditions a result was produced under (model, judge
// model, budget). A clean key/value grid; empty fields are omitted so the card
// never shows a dangling "—", and the whole card is hidden when nothing
// meaningful remains. Per report §13 the sandbox ref and the agent/mission
// version IDs are intentionally not surfaced here.
export function ConditionsCard({ conditions }: { conditions: ResultConditions }) {
  const rows: { label: string; value: string }[] = [];
  if (conditions.model) rows.push({ label: "Model", value: conditions.model });
  if (conditions.judge_model)
    rows.push({ label: "Judge model", value: conditions.judge_model });
  if (conditions.budget_seconds > 0)
    rows.push({ label: "Budget", value: `${conditions.budget_seconds}s` });
  // §31/§43-UX8: the platform the artifact executed on is result context — an amd64 score
  // produced under emulation on an arm host reads differently from a native one.
  if (conditions.platform) rows.push({ label: "Platform", value: conditions.platform });
  // Assistance: surface intel disclosure per run so an assisted result reads differently from an
  // unassisted one. Shown only when intel was actually disclosed (omitted like the other fields
  // when zero, so unassisted runs stay clean).
  if (conditions.intel_disclosed > 0)
    rows.push({
      label: "Intel disclosed",
      value: `${conditions.intel_disclosed} intel`,
    });

  if (rows.length === 0) return null;

  return (
    <Card className="bg-card">
      <CardHeader>
        <CardTitle className="text-label uppercase text-text-tertiary">
          Conditions
        </CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.label} className="flex flex-col gap-1">
              <dt className="text-label uppercase text-text-tertiary">
                {row.label}
              </dt>
              <dd className="break-words font-mono text-dense text-foreground">
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
