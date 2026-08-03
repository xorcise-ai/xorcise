import { cn } from "@/components/ui/cn";
import { Badge } from "@/components/ui/badge";
import { proficiencyLevel, titleCase } from "./labels";

const TIERS = [1, 2, 3, 4, 5] as const;

/**
 * Difficulty as a dot-matrix pip meter — the brand's visual language for a value
 * (monochrome amber, value = struck dots). The tier
 * is carried by the COUNT of filled amber pips, not by hue, so Novice ●○○○○ … Expert ●●●●● are all
 * distinguishable across the five-level ladder. An unrecognised proficiency falls back to a plain
 * muted label.
 */
export function DifficultyBadge({ proficiency }: { proficiency: string }) {
  const level = proficiencyLevel(proficiency);
  const label = titleCase(proficiency);
  if (level == null) {
    return (
      <Badge variant="muted" className="capitalize">
        {label}
      </Badge>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-primary/25 bg-primary/[0.08] px-2 py-0.5 text-label uppercase text-primary"
      title={`Difficulty ${level} of 5`}
    >
      <span className="flex items-center gap-0.5" aria-hidden>
        {TIERS.map((i) => (
          <span
            key={i}
            className={cn(
              "size-1.5 rounded-full",
              i <= level ? "bg-primary" : "bg-primary/20",
            )}
          />
        ))}
      </span>
      {label}
      <span className="sr-only">— difficulty {level} of 5</span>
    </span>
  );
}
