import type { HTMLAttributes } from "react";
import { cn } from "./cn";

/** Max stagger delay — a long grid shouldn't keep late cards invisible. */
const MAX_DELAY_MS = 400;

/** Fade-up entrance for content that just resolved (skeleton → data swap).
 *  `delay` staggers grid items (pass `index * 40`); capped so long lists
 *  finish entering promptly. Uses `both` fill (via the utility) so delayed
 *  elements start invisible instead of flashing. */
export function Reveal({
  delay = 0,
  className,
  style,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & { delay?: number }) {
  return (
    <div
      className={cn("animate-fade-up", className)}
      style={
        delay > 0
          ? { animationDelay: `${Math.min(delay, MAX_DELAY_MS)}ms`, ...style }
          : style
      }
      {...props}
    >
      {children}
    </div>
  );
}
