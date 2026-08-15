/** Small shared helpers. Kept dependency-free and individually testable. */

/** Conditional className joiner. Avoids pulling in clsx for this one job. */
export function cn(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

export function formatPrice(value?: number | null): string {
  if (value === null || value === undefined) return "—";
  return INR.format(Math.round(value));
}

export function formatPriceRange(min?: number | null, max?: number | null): string {
  if (!min && !max) return "—";
  if (min && max && Math.round(min) !== Math.round(max)) {
    return `${INR.format(Math.round(min))}–${INR.format(Math.round(max))}`;
  }
  return INR.format(Math.round((min ?? max) as number));
}

/** Distance for humans: metres under a km, one decimal above. */
export function formatDistance(metres?: number | null): string {
  if (metres === null || metres === undefined) return "";
  if (metres < 1000) return `${Math.round(metres)} m`;
  return `${(metres / 1000).toFixed(1)} km`;
}

export function formatCount(value: number): string {
  if (value < 1000) return String(value);
  if (value < 100000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)}k`;
  return `${(value / 100000).toFixed(1)}L`;
}

export function formatPercent(ratio?: number | null): string {
  if (ratio === null || ratio === undefined) return "—";
  return `${Math.round(ratio * 100)}%`;
}

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const seconds = Math.floor((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";

  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31536000],
    ["month", 2592000],
    ["week", 604800],
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];

  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, secondsInUnit] of units) {
    const amount = Math.floor(seconds / secondsInUnit);
    if (amount >= 1) return formatter.format(-amount, unit);
  }
  return "just now";
}

export function formatMonth(period: string): string {
  const [year, month] = period.split("-");
  if (!year || !month) return period;
  const date = new Date(Number(year), Number(month) - 1, 1);
  return date.toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
}

/** Attribute codes come from the API as snake_case; render them as words. */
export function humanizeAttribute(value: string): string {
  return value.replace(/_/g, " ");
}

export function priceLevelLabel(level?: number | null): string {
  if (!level) return "";
  return "₹".repeat(Math.max(1, Math.min(4, level)));
}

/** Score band drives colour. Thresholds are shared by cards, rings and markers. */
export type ScoreBand = "excellent" | "good" | "mixed" | "poor" | "unknown";

export function scoreBand(score?: number | null): ScoreBand {
  if (score === null || score === undefined) return "unknown";
  if (score >= 80) return "excellent";
  if (score >= 65) return "good";
  if (score >= 45) return "mixed";
  return "poor";
}

export const SCORE_BAND_COLORS: Record<ScoreBand, string> = {
  excellent: "rgb(var(--positive))",
  good: "rgb(var(--accent-2))",
  mixed: "rgb(var(--warning))",
  poor: "rgb(var(--negative))",
  unknown: "rgb(var(--subtle))",
};

export function sentimentLabel(sentiment: number): "positive" | "neutral" | "negative" {
  if (sentiment > 0.15) return "positive";
  if (sentiment < -0.15) return "negative";
  return "neutral";
}

export const SOURCE_LABELS: Record<string, string> = {
  user: "Khaabo user",
  reddit: "Reddit",
  youtube: "YouTube",
  osm: "OpenStreetMap",
  manual: "Editorial",
};

export const BADGE_META: Record<string, { label: string; emoji: string; hint: string }> = {
  best_value: {
    label: "Best value",
    emoji: "💰",
    hint: "Highest quality per rupee among places with a price signal",
  },
  hidden_gem: {
    label: "Hidden gem",
    emoji: "💎",
    hint: "Excellent and consistent, but less talked about than its peers",
  },
  most_consistent: {
    label: "Most consistent",
    emoji: "🎯",
    hint: "Lowest variation across reviews, with a real sample size",
  },
};

/** Stable pastel-on-dark colour from a string, for avatar fallbacks. */
export function stringToHue(value: string): number {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash) % 360;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase() ?? "").join("") || "?";
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}
