import { Link } from "react-router-dom";

import { Badge, NotEnoughData, RankBadge, WhyChips } from "@/components/Badge";
import { Card } from "@/components/Card";
import { Score } from "@/components/Score";
import { Trend } from "@/components/Trend";
import { BookmarkButton } from "@/features/bookmarks/BookmarkButton";
import {
  SOURCE_LABELS,
  formatDistance,
  formatPercent,
  formatPrice,
  humanizeAttribute,
  priceLevelLabel,
} from "@/lib/format";
import type { DishRestaurant } from "@/types/api";

export interface RestaurantCardProps {
  restaurant: DishRestaurant;
  /** Present when this card is scoped to a dish, which enables dish-level bookmarks. */
  dishId?: string;
  dishName?: string;
  rank?: number;
  compact?: boolean;
}

/**
 * A restaurant, ranked for one dish.
 *
 * Every ranked card shows: the dish-specific score, a "Why?" line composed by the
 * server, and at most one verbatim quote with its source attributed. Unranked rows
 * say "Not enough data" rather than showing a placeholder position.
 */
export function RestaurantCard({
  restaurant,
  dishId,
  dishName,
  rank,
  compact = false,
}: RestaurantCardProps) {
  const isRanked = restaurant.status === "ranked";
  const snippet = restaurant.snippets?.[0];

  return (
    <Card animate interactive={false} className="relative">
      <div className="flex items-start gap-4">
        {rank !== undefined && isRanked && (
          <span
            className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full border border-border
              bg-surface-2 text-xs font-semibold tabular-nums text-muted"
            aria-label={`Rank ${rank}`}
          >
            {rank}
          </span>
        )}

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Link
                to={`/restaurant/${restaurant.id}`}
                className="font-display text-xl leading-tight text-text transition-colors hover:text-accent"
              >
                {restaurant.name}
              </Link>

              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
                {restaurant.area && <span>{restaurant.area}</span>}
                {restaurant.distance_m != null && (
                  <>
                    <span aria-hidden className="text-subtle">
                      ·
                    </span>
                    <span>{formatDistance(restaurant.distance_m)}</span>
                  </>
                )}
                {restaurant.price_level && (
                  <>
                    <span aria-hidden className="text-subtle">
                      ·
                    </span>
                    <span title={`Price level ${restaurant.price_level} of 4`}>
                      {priceLevelLabel(restaurant.price_level)}
                    </span>
                  </>
                )}
              </p>
            </div>

            <div className="flex shrink-0 items-start gap-2">
              {isRanked ? (
                <Score value={restaurant.score} size={compact ? "sm" : "md"} />
              ) : (
                <NotEnoughData detail="Fewer than 3 dish mentions so far" />
              )}
              {dishId && (
                <BookmarkButton
                  targetType="dish_restaurant"
                  dishId={dishId}
                  restaurantId={restaurant.id}
                  label={dishName ? `${dishName} at ${restaurant.name}` : restaurant.name}
                />
              )}
            </div>
          </div>

          {isRanked && (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {restaurant.badges.map((badge) => (
                  <RankBadge key={badge} code={badge} />
                ))}
                <Trend trend={restaurant.trend} showLabel />
                {restaurant.price_avg != null && (
                  <Badge tone="neutral" title="Average price mentioned for this dish">
                    ~{formatPrice(restaurant.price_avg)}
                  </Badge>
                )}
              </div>

              <WhyChips why={restaurant.why} className="mt-3" />

              {!compact && restaurant.top_attributes.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {restaurant.top_attributes.slice(0, 4).map((attribute) => (
                    <span key={attribute} className="chip py-0.5 text-[11px]">
                      {humanizeAttribute(attribute)}
                    </span>
                  ))}
                </div>
              )}

              {!compact && snippet && (
                <blockquote
                  className="mt-4 border-l-2 border-accent/40 pl-3 text-sm italic text-muted"
                  cite={SOURCE_LABELS[snippet.source] ?? snippet.source}
                >
                  “{snippet.text}”
                  <footer className="mt-1 not-italic text-xs text-subtle">
                    {SOURCE_LABELS[snippet.source] ?? snippet.source}
                  </footer>
                </blockquote>
              )}
            </>
          )}

          {!isRanked && (
            <p className="mt-3 text-sm text-subtle">
              {restaurant.mention_count > 0
                ? `Only ${restaurant.mention_count} mention${restaurant.mention_count === 1 ? "" : "s"} so far — not enough to rank fairly.`
                : "No dish evidence collected yet."}
            </p>
          )}

          {isRanked && !compact && (
            <dl className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t border-border pt-3 text-xs text-subtle">
              <div className="flex gap-1">
                <dt>Positive</dt>
                <dd className="font-medium text-muted">
                  {formatPercent(restaurant.positive_ratio)}
                </dd>
              </div>
              <div className="flex gap-1">
                <dt>Mentions</dt>
                <dd className="font-medium text-muted">{restaurant.mention_count}</dd>
              </div>
              <div className="flex gap-1">
                <dt>Consistency</dt>
                <dd className="font-medium text-muted">
                  {formatPercent(restaurant.consistency)}
                </dd>
              </div>
            </dl>
          )}
        </div>
      </div>
    </Card>
  );
}
