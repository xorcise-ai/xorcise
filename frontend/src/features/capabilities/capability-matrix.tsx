import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/components/ui/cn";
import { CUSTOM_LABEL, HARNESSES, HarnessGlyph } from "@/features/agents/harnesses";
import type { HarnessCapabilityProfile } from "@/lib/api/types";
import { CapabilityDot } from "./capability-dot";
import { DISPLAY_GROUPS, groupLevel, groupNote } from "./capability-groups";
import { useCapabilities } from "./use-capabilities";

/** One column of the matrix: a harness `kind` paired with the adapter profile it renders (the
 * live adapter's own profile for a built-in harness, always the `generic` profile for Custom —
 * a custom kind has no adapter of its own yet, so it inherits generic's declared capabilities). */
interface MatrixColumn {
  kind: string;
  name: string;
  profile: HarnessCapabilityProfile | undefined;
  /** Custom always renders the generic adapter's profile, which is never "verified" against a
   * real harness — the header badge says so rather than implying it was measured. */
  unverified: boolean;
}

/**
 * The harness capability dot-matrix shown beneath the harness picker at agent
 * creation: rows = the display groups, columns = every built-in harness + Custom, so a user
 * can see — before registering an agent — what telemetry that harness's adapter actually
 * declares, honestly (gaps show as an unsupported/partial dot with the adapter's own note,
 * never hidden). `selectedKind` highlights the chosen column with the amber selection token —
 * the one place this matrix uses amber; every dot's own state is shape-coded, never color-coded.
 */
export function CapabilityMatrix({ selectedKind }: { selectedKind: string }) {
  const { profiles, byName, isLoading, isError } = useCapabilities();

  const columns: MatrixColumn[] = [
    ...HARNESSES.map((h) => ({
      kind: h.kind,
      name: h.name,
      profile: byName.get(h.kind),
      unverified: false,
    })),
    {
      kind: "__custom__",
      name: CUSTOM_LABEL,
      profile: byName.get("generic"),
      unverified: true,
    },
  ];

  if (isError) {
    return (
      <Card>
        <CardContent>
          <p className="text-body text-text-secondary">
            Capability info unavailable
          </p>
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !profiles) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Harness capabilities</CardTitle>
        </CardHeader>
        <CardContent role="status" aria-label="Loading capability matrix…">
          <div className="space-y-2">
            <Skeleton variant="row" />
            {DISPLAY_GROUPS.map((g) => (
              <Skeleton key={g.id} variant="row" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  // Custom has no fixed `kind` of its own — it represents "whatever the user typed that ISN'T one
  // of the built-in harnesses" (including the empty string, the picker's unselected default). So
  // it must highlight whenever selectedKind matches NO built-in HARNESSES kind, not only "".
  const isSelected = (col: MatrixColumn) =>
    col.kind === "__custom__"
      ? !HARNESSES.some((h) => h.kind === selectedKind)
      : selectedKind === col.kind;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Harness capabilities</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        {/* The cell primitives (THead/TBody/TR/TH/TD) carry the shared table styling, but
            the <table> element stays raw: the Table primitive renders its own bordered,
            rounded-xl card wrapper, which cannot nest inside the Card this matrix already
            lives in without doubling the border. */}
        <table className="w-full text-left text-dense">
          <THead>
            <TR>
              <TH scope="col">Capability</TH>
              {columns.map((col) => (
                <TH
                  key={col.kind}
                  scope="col"
                  data-selected={isSelected(col) || undefined}
                  className={cn(
                    isSelected(col) && "border-b-2 border-primary bg-primary/10 text-heading",
                  )}
                >
                  <span className="flex items-center gap-1.5 normal-case">
                    <HarnessGlyph kind={col.kind === "__custom__" ? null : col.kind} />
                    {col.name}
                    {col.unverified && (
                      <span className="text-caption normal-case text-text-tertiary">
                        (unverified — generic adapter)
                      </span>
                    )}
                  </span>
                </TH>
              ))}
            </TR>
          </THead>
          <TBody>
            {DISPLAY_GROUPS.map((group) => (
              <TR key={group.id}>
                {/* A ROW header is content, not an uppercase eyebrow, so it overrides TH's
                    label role with body — it keeps TH's padding, which is the part that
                    must stay in step with the cells beside it. */}
                <TH scope="row" className="text-body normal-case text-foreground">
                  {group.label}
                </TH>
                {columns.map((col) => (
                  <TD
                    key={col.kind}
                    className={cn(isSelected(col) && "bg-primary/10")}
                  >
                    {col.profile ? (
                      <CapabilityDot
                        level={groupLevel(col.profile, group)}
                        label={group.label}
                        note={groupNote(col.profile, group)}
                      />
                    ) : null}
                  </TD>
                ))}
              </TR>
            ))}
          </TBody>
        </table>
      </CardContent>
    </Card>
  );
}
