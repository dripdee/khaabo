import { Link } from "react-router-dom";

import { Card, CardMeta, CardTitle } from "@/components/Card";
import { ScorePill } from "@/components/Score";
import { WhyChips } from "@/components/Badge";
import { formatPrice } from "@/lib/format";
import type { DishHighlights as Highlights } from "@/types/api";

const SLOTS = [
  { key: "top", label: "Top pick", emoji: "🏆", hint: "Highest dish score overall" },
  {
    key: "best_value",
    label: "Best value",
    emoji: "💰",
    hint: "Best quality for the price among places with a price signal",
  },
  {
    key: "hidden_gem",
    label: "Hidden gem",
    emoji: "💎",
    hint: "Excellent and consistent, but less talked about",
  },
  {
    key: "most_consistent",
    label: "Most consistent",
    emoji: "🎯",
    hint: "Least variation across reviews, with a real sample",
  },
] as const;

/**
 * Named picks for a dish.
 *
 * Slots come from persisted badges, so they always agree with the ranked list.
 * A slot with no qualifying place is omitted rather than filled with a runner-up —
 * "best value" only means something when a price signal actually exists.
 */
export function DishHighlights({
  highlights,
  dishName,
}: {
  highlights: Highlights;
  dishName: string;
}) {
  const available = SLOTS.filter((slot) => highlights[slot.key]);
  if (available.length === 0) return null;

  return (
    <section aria-labelledby="highlights-heading">
      <h2 id="highlights-heading" className="mb-3 font-display text-title text-text">
        Where to go
      </h2>

      <div className="grid gap-3 sm:grid-cols-2">
        {available.map((slot) => {
          const restaurant = highlights[slot.key]!;
          return (
            <Card key={slot.key} animate interactive className="flex flex-col">
              <div className="flex items-start justify-between gap-3">
                <span
                  className="chip border-accent/30 bg-accent/10 text-accent"
                  title={slot.hint}
                >
                  <span aria-hidden>{slot.emoji}</span>
                  {slot.label}
                </span>
                <ScorePill value={restaurant.score} />
              </div>

              <Link to={`/restaurant/${restaurant.id}`} className="mt-3 block">
                <CardTitle className="transition-colors hover:text-accent">
                  {restaurant.name}
                </CardTitle>
              </Link>

              <CardMeta className="mt-1">
                {[restaurant.area, restaurant.price_avg ? `~${formatPrice(restaurant.price_avg)}` : null]
                  .filter(Boolean)
                  .join(" · ")}
              </CardMeta>

              <WhyChips why={restaurant.why} className="mt-3 text-xs" />

              <span className="sr-only">
                {slot.label} for {dishName}
              </span>
            </Card>
          );
        })}
      </div>
    </section>
  );
}
