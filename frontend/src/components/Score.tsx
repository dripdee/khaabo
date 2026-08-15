import { motion } from "framer-motion";

import { usePrefersReducedMotion } from "@/hooks/usePrefersReducedMotion";
import { cn, SCORE_BAND_COLORS, scoreBand } from "@/lib/format";
import { DURATION, EASE } from "@/lib/motion";

type Size = "sm" | "md" | "lg";

const GEOMETRY: Record<Size, { box: number; stroke: number; font: string }> = {
  sm: { box: 40, stroke: 3, font: "text-xs" },
  md: { box: 56, stroke: 4, font: "text-base" },
  lg: { box: 84, stroke: 5, font: "text-2xl" },
};

export interface ScoreProps {
  /** 0–100, or null when the backend reports insufficient data. */
  value?: number | null;
  size?: Size;
  label?: string;
  className?: string;
}

/**
 * Score ring.
 *
 * A null value renders an explicit em dash and "Not enough data" — never a zero and
 * never an empty ring, because a 0 would read as a genuine bad score.
 */
export function Score({ value, size = "md", label, className }: ScoreProps) {
  const reduced = usePrefersReducedMotion();
  const geometry = GEOMETRY[size];
  const band = scoreBand(value);
  const color = SCORE_BAND_COLORS[band];

  const radius = (geometry.box - geometry.stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const hasValue = value !== null && value !== undefined;
  const progress = hasValue ? Math.max(0, Math.min(100, value)) / 100 : 0;

  return (
    <div className={cn("inline-flex flex-col items-center gap-1", className)}>
      <div
        className="relative grid place-items-center"
        style={{ width: geometry.box, height: geometry.box }}
        role="img"
        aria-label={
          hasValue
            ? `Score ${Math.round(value)} out of 100, rated ${band}`
            : "Not enough data to score this yet"
        }
        title={hasValue ? undefined : "Not enough data"}
      >
        <svg width={geometry.box} height={geometry.box} className="-rotate-90">
          <circle
            cx={geometry.box / 2}
            cy={geometry.box / 2}
            r={radius}
            fill="none"
            stroke="rgb(var(--border))"
            strokeWidth={geometry.stroke}
          />
          {hasValue && (
            <motion.circle
              cx={geometry.box / 2}
              cy={geometry.box / 2}
              r={radius}
              fill="none"
              stroke={color}
              strokeWidth={geometry.stroke}
              strokeLinecap="round"
              strokeDasharray={circumference}
              initial={{ strokeDashoffset: reduced ? circumference * (1 - progress) : circumference }}
              animate={{ strokeDashoffset: circumference * (1 - progress) }}
              transition={{ duration: reduced ? 0 : DURATION.slow, ease: EASE }}
            />
          )}
        </svg>
        <span
          className={cn("absolute font-semibold tabular-nums", geometry.font)}
          style={{ color: hasValue ? color : "rgb(var(--subtle))" }}
        >
          {hasValue ? Math.round(value) : "—"}
        </span>
      </div>
      {label && <span className="text-[11px] uppercase tracking-wide text-subtle">{label}</span>}
    </div>
  );
}

/** Compact inline variant for dense lists where a ring would be too heavy. */
export function ScorePill({ value, className }: { value?: number | null; className?: string }) {
  const band = scoreBand(value);
  const hasValue = value !== null && value !== undefined;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-chip px-2 py-0.5 text-xs font-semibold tabular-nums",
        className,
      )}
      style={{
        color: SCORE_BAND_COLORS[band],
        backgroundColor: hasValue ? `${SCORE_BAND_COLORS[band]}1f` : "rgb(var(--surface-2))",
      }}
      title={hasValue ? `Dish score ${Math.round(value)}/100` : "Not enough data"}
    >
      {hasValue ? Math.round(value) : "Not enough data"}
    </span>
  );
}
