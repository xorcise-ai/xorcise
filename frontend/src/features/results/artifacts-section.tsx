import { Paperclip } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useRunArtifacts } from "./queries";

// The artifacts an agent submitted during the run, with full content for review.
// The grade result carries only artifact names (thin refs); this fetches the payloads from
// GET /runs/{id}/artifacts and renders each in a collapsible block with a download link.
// Renders nothing when the run submitted no artifacts.
export function ArtifactsSection({ runId }: { runId: string }) {
  const artifacts = useRunArtifacts(runId);
  const items = artifacts.data ?? [];
  if (items.length === 0) return null;
  return (
    <section>
      <h2 className="mb-2 text-label uppercase text-text-tertiary">
        Artifacts
      </h2>
      <Card className="bg-card">
        <CardContent className="space-y-2 p-4">
          {items.map((a) => (
            <details
              key={a.seq}
              className="rounded-md border border-border bg-background"
            >
              <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-dense">
                <Paperclip className="size-3 shrink-0 text-text-tertiary" />
                <span className="min-w-0 break-all font-mono text-foreground">
                  {a.name}
                </span>
                <Badge
                  variant={a.kind === "flag" ? "ok" : "muted"}
                  className="ml-auto uppercase"
                >
                  {a.kind}
                </Badge>
              </summary>
              <div className="space-y-2 border-t border-border px-3 py-2">
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-dense text-text-secondary">
                  {a.payload}
                </pre>
                <a
                  href={`data:text/plain;charset=utf-8,${encodeURIComponent(a.payload)}`}
                  download={a.name || "artifact.txt"}
                  className="inline-block text-caption text-primary underline-offset-2 hover:underline"
                >
                  Download
                </a>
              </div>
            </details>
          ))}
        </CardContent>
      </Card>
    </section>
  );
}
