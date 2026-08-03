import { useId } from "react";
import { cn } from "@/components/ui/cn";
import type { GroupLevel } from "./capability-groups";

export interface CapabilityDotProps {
  level: GroupLevel;
  label: string;
  /** The adapter's own gap sentence (from `groupNote`) — rendered verbatim, never paraphrased. */
  note?: string;
  className?: string;
}

const LEVEL_TEXT: Record<GroupLevel, string> = {
  supported: "supported",
  partial: "partial",
  unsupported: "not supported",
};

/**
 * A single capability glyph. State is carried by the dot's SHAPE, never by hue alone (guide's
 * rule: color is never the sole signal, and never brand amber for a state) —
 *
 *   supported   → hollow circle
 *   partial     → half-filled circle
 *   unsupported → hollow circle, struck through
 *
 * each distinguishable even in grayscale or under color-blindness. The SVG is `aria-hidden`;
 * the full state sentence — level plus the adapter's own gap note, verbatim — is exposed only
 * via the visually-hidden text, so it never depends on being able to see or hover the glyph.
 */
export function CapabilityDot({
  level,
  label,
  note,
  className,
}: CapabilityDotProps) {
  const clipId = useId();
  const text = `${label}: ${LEVEL_TEXT[level]}${note ? `, ${note}` : ""}`;

  return (
    <span className="inline-flex items-center" title={text}>
      <svg
        viewBox="0 0 16 16"
        width={12}
        height={12}
        data-dot
        data-level={level}
        aria-hidden="true"
        className={cn("shrink-0 text-foreground", className)}
      >
        {level === "supported" && (
          <circle
            cx={8}
            cy={8}
            r={6}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
          />
        )}
        {level === "partial" && (
          <>
            <clipPath id={clipId}>
              <rect x={2} y={2} width={6} height={12} />
            </clipPath>
            <circle
              cx={8}
              cy={8}
              r={6}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
            />
            <circle
              cx={8}
              cy={8}
              r={6}
              fill="currentColor"
              clipPath={`url(#${clipId})`}
            />
          </>
        )}
        {level === "unsupported" && (
          <>
            <circle
              cx={8}
              cy={8}
              r={6}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
            />
            <line
              x1={4}
              y1={12}
              x2={12}
              y2={4}
              stroke="currentColor"
              strokeWidth={1.5}
            />
          </>
        )}
      </svg>
      <span className="sr-only">{text}</span>
    </span>
  );
}
