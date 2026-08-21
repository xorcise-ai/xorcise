import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "./cn";

/**
 * Shape carries the kind, colour only confirms it.
 *
 * `agent` is the one variant with a 6px radius while `self` and `other` stay circular,
 * and that is the whole point of the component: in a replay transcript a human and a
 * model appear in the same column, and the reader has to tell them apart at a glance and
 * at a distance. A circle is a person; a rounded square is a machine. The purple only
 * agrees with the shape — §12's rule is that meaning is never hue alone, and an avatar
 * has no glyph to lean on, so the silhouette does that work instead.
 */
const avatarVariants = cva(
  "inline-flex shrink-0 items-center justify-center border font-bold uppercase tracking-[var(--text-label--letter-spacing)]",
  {
    variants: {
      kind: {
        other: "rounded-full border-border bg-raised text-text-secondary",
        self: "rounded-full border-primary/45 bg-primary/10 text-primary",
        agent: "rounded-md border-model/40 bg-model/12 text-model",
      },
      size: {
        /* The Figma board's size. */
        default: "size-8 text-caption",
        /* Dense rows — a leaderboard or a run list, where a 32px avatar would set the
           row height instead of the type doing it. */
        sm: "size-6 text-label",
      },
    },
    defaultVariants: { kind: "other", size: "default" },
  },
);

export interface AvatarProps
  extends Omit<HTMLAttributes<HTMLSpanElement>, "children">,
    VariantProps<typeof avatarVariants> {
  /** Full name or identifier — initials are derived from it. */
  name: string;
  /** Override the derived initials (e.g. a model's short code, "O5"). */
  initials?: string;
}

/**
 * Two characters, chosen the way a person would: the first letter of the first two words,
 * or the first two characters when there is only one word. Separators common in this
 * app's identifiers (`-`, `_`, `.`, `/`) count as word breaks, so `operation-tessera`
 * reads OT and `claude` reads CL.
 */
export function initialsFrom(name: string): string {
  const words = name.trim().split(/[\s\-_./]+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2);
  return (words[0][0] ?? "") + (words[1][0] ?? "");
}

export function Avatar({
  className,
  name,
  initials,
  kind,
  size,
  ...props
}: AvatarProps) {
  return (
    <span
      title={name}
      role="img"
      aria-label={name}
      className={cn(avatarVariants({ kind, size }), className)}
      {...props}
    >
      {initials ?? initialsFrom(name)}
    </span>
  );
}

export { avatarVariants };
