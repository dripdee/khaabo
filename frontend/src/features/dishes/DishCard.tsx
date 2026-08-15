import { Link } from "react-router-dom";

import { Badge, NotEnoughData } from "@/components/Badge";
import { Card } from "@/components/Card";
import { ScorePill } from "@/components/Score";
import { Trend } from "@/components/Trend";
import { formatCount, formatPriceRange } from "@/lib/format";
import type { DishCard as DishCardData } from "@/types/api";

/**
 * Dish result card.
 *
 * Leads with the dish, not a restaurant — the whole product is organised around
 * "what should I eat" first.
 */
export function DishCard({ dish }: { dish: DishCardData }) {
  const isRanked = dish.status === "ranked";

  return (
    <Link to={`/dish/${dish.slug}`} className="block focus-visible:rounded-card">
      <Card interactive animate className="group">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-display text-2xl leading-tight text-text transition-colors group-hover:text-accent">
              {dish.name}
            </h3>
            <p className="mt-1 flex items-center gap-2 text-sm text-muted">
              {dish.cuisine && <span>{dish.cuisine}</span>}
              {dish.is_veg === true && (
                <Badge tone="positive" className="px-1.5 py-0">
                  Veg
                </Badge>
              )}
              {dish.is_veg === false && (
                <Badge tone="negative" className="px-1.5 py-0">
                  Non-veg
                </Badge>
              )}
            </p>
          </div>

          <div className="flex shrink-0 flex-col items-end gap-2">
            {isRanked ? <ScorePill value={dish.score} /> : <NotEnoughData />}
            <Trend trend={dish.trend} />
          </div>
        </div>

        <dl className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          {isRanked && (
            <div>
              <dt className="sr-only">Places ranked</dt>
              <dd className="text-muted">
                <span className="font-medium text-text">{dish.restaurant_count}</span> places
              </dd>
            </div>
          )}
          {dish.mention_count > 0 && (
            <div>
              <dt className="sr-only">Dish mentions</dt>
              <dd className="text-muted">
                <span className="font-medium text-text">{formatCount(dish.mention_count)}</span>{" "}
                mentions
              </dd>
            </div>
          )}
          {dish.price_range && (dish.price_range.min || dish.price_range.max) && (
            <div>
              <dt className="sr-only">Typical price</dt>
              <dd className="text-muted">
                {formatPriceRange(dish.price_range.min, dish.price_range.max)}
              </dd>
            </div>
          )}
        </dl>
      </Card>
    </Link>
  );
}
