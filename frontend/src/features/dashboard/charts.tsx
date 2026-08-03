"use client";

/**
 * Compact trend sparkline (area + line) from 0..1 scores, oldest→newest. Inherits `currentColor`
 * so the caller sets the tone; stretches to fill its box (a wide, short row cell). Falls back to a
 * flat baseline when there's nothing to plot, and marks a lone point so a single run still reads.
 */
export function Sparkline({
  values,
  className,
}: {
  values: number[];
  className?: string;
}) {
  const w = 64;
  const h = 20;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));

  if (values.length === 0) {
    return (
      <svg
        viewBox={`0 0 ${w} ${h}`}
        className={className}
        preserveAspectRatio="none"
        aria-hidden
      >
        <line
          x1="0"
          y1={h - 1}
          x2={w}
          y2={h - 1}
          stroke="currentColor"
          strokeOpacity="0.25"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    );
  }

  const n = values.length;
  const pts = values.map((v, i): [number, number] => [
    n === 1 ? w / 2 : (i / (n - 1)) * w,
    h - clamp(v) * (h - 3) - 1.5,
  ]);
  const line = pts
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  const area = `${line} L${pts[n - 1][0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className={className}
      preserveAspectRatio="none"
      aria-hidden
    >
      <path d={area} fill="currentColor" fillOpacity="0.12" />
      <path
        d={line}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      {n === 1 && (
        <circle
          cx={pts[0][0]}
          cy={pts[0][1]}
          r="1.5"
          fill="currentColor"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
