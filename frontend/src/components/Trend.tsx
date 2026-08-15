import { cn } from "@/lib/format";
import type { Trend as TrendData, TrendDirection } from "@/types/api";

const META: Record<
  TrendDirection,
  { glyph: string; label: string; className: string }
> = {
  rising: { glyph: "↑", label: "Rising", className: "text-positive bg-positive/10" },
  stable: { glyph: "→", label: "Stable", className: "text-muted bg-surface-2" },
  declining: { glyph: "↓", label: "Declining", className: "text-negative bg-negative/10" },
};

export interface TrendProps {
  trend?: TrendData | null;
  showLabel?: boolean;
  className?: string;
}

/**
 * Trend indicator.
 *
 * Renders **nothing** when `direction` is null. The backend only emits a direction
 * when both comparison windows had enough observations, so an absent arrow is the
 * honest output rather than a missing feature.
 *
 * Colour is never the only signal: the arrow glyph and an accessible label carry the
 * meaning too.
 */
export function Trend({ trend, showLabel = false, className }: TrendProps) {
  if (!trend?.direction) return null;

  const meta = META[trend.direction];
  const delta = trend.delta ?? null;
  const deltaText =
    delta !== null ? `${delta > 0 ? "+" : ""}${Math.round(delta * 100)} pts vs earlier` : "";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-chip px-2 py-0.5 text-xs font-medium",
        meta.className,
        // A non-significant trend (attention up, opinion flat) is shown more weakly.
        trend.significant === false && "opacity-70",
        className,
      )}
      title={
        trend.significant === false
          ? `${meta.label} — more people are talking about it, opinion is steady`
          : `${meta.label}${deltaText ? ` · ${deltaText}` : ""}`
      }
    >
      <span aria-hidden>{meta.glyph}</span>
      <span className={showLabel ? undefined : "sr-only"}>{meta.label}</span>
    </span>
  );
}
