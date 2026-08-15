import { BADGE_META, cn } from "@/lib/format";
import type { BadgeCode, WhyReason } from "@/types/api";

type Tone = "neutral" | "accent" | "positive" | "warning" | "negative";

const TONES: Record<Tone, string> = {
  neutral: "border-border bg-surface-2 text-muted",
  accent: "border-accent/30 bg-accent/10 text-accent",
  positive: "border-positive/30 bg-positive/10 text-positive",
  warning: "border-warning/30 bg-warning/10 text-warning",
  negative: "border-negative/30 bg-negative/10 text-negative",
};

export interface BadgeProps {
  children: React.ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
}

export function Badge({ children, tone = "neutral", title, className }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-chip border px-2.5 py-1 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Ranking badges (best value / hidden gem / most consistent) with their meaning. */
export function RankBadge({ code }: { code: BadgeCode }) {
  const meta = BADGE_META[code];
  if (!meta) return null;

  return (
    <Badge tone="accent" title={meta.hint}>
      <span aria-hidden>{meta.emoji}</span>
      {meta.label}
    </Badge>
  );
}

/**
 * The "Why?" explanation.
 *
 * Labels are rendered exactly as the API sends them. The frontend deliberately does
 * not compose its own wording, so the explanation can never drift from the stored
 * score it describes.
 */
export function WhyChips({ why, className }: { why: WhyReason[]; className?: string }) {
  if (!why?.length) return null;

  return (
    <p
      className={cn("text-sm leading-relaxed text-muted", className)}
      title="Why this is ranked here"
    >
      {why.map((reason, index) => (
        <span key={reason.code}>
          {index > 0 && <span className="mx-1.5 text-subtle">·</span>}
          <span
            className={
              reason.code === "inconsistent" || reason.code === "stale"
                ? "text-warning"
                : undefined
            }
          >
            {reason.label}
          </span>
        </span>
      ))}
    </p>
  );
}

/** Food DNA chip. Emoji plus text so the meaning survives without emoji rendering. */
export function DnaChipView({
  emoji,
  label,
  title,
}: {
  emoji: string;
  label: string;
  title?: string;
}) {
  return (
    <Badge tone="neutral" title={title} className="bg-surface-2/80">
      <span aria-hidden>{emoji}</span>
      {label}
    </Badge>
  );
}

export function AttributeChip({ label, count }: { label: string; count?: number }) {
  return (
    <span className="chip">
      {label}
      {count !== undefined && <span className="text-subtle">{count}</span>}
    </span>
  );
}

/**
 * Explicit empty state for unrankable entries.
 *
 * Used wherever the API returns `status: "insufficient_data"`. Showing this is a
 * product requirement: a fabricated ranking is worse than an honest gap.
 */
export function NotEnoughData({
  detail,
  className,
}: {
  detail?: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-chip border border-dashed border-border",
        "bg-transparent px-2.5 py-1 text-xs text-subtle",
        className,
      )}
      title={detail ?? "There isn't enough evidence to rank this yet"}
    >
      Not enough data
    </span>
  );
}
