import { Link } from "react-router-dom";

import { Badge } from "@/components/Badge";
import { Card } from "@/components/Card";
import { ScorePill } from "@/components/Score";
import { Trend } from "@/components/Trend";
import { DishCardSkeleton } from "@/components/Skeleton";
import { useTrending } from "@/features/dishes/useTrending";
import { useSearchFilters } from "@/features/search/useSearch";
import { formatCount } from "@/lib/format";
import { Seo } from "@/lib/seo";

/**
 * Trending.
 *
 * Only subjects that cleared the significance gate appear here — the backend emits no
 * direction when either comparison window is too thin, so this page is empty rather
 * than speculative on a fresh dataset.
 */
export default function TrendingPage() {
  const { filters, setFilters } = useSearchFilters();
  const direction = (filters.trend as "rising" | "declining") ?? "rising";

  const { data, isLoading } = useTrending({ direction, limit: 24 });

  return (
    <>
      <Seo
        title="Trending dishes in Kolkata"
        description="Dishes and places whose recent reviews differ from their history, with the size of the shift."
        canonicalPath="/trending"
      />

      <div className="mx-auto max-w-content px-4 py-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-hero text-text">Trending</h1>
            <p className="mt-2 max-w-prose text-muted">
              We compare the last 60 days against the preceding six months. A dish only
              appears once both windows have enough mentions to make the comparison mean
              something.
            </p>
          </div>

          <div
            role="group"
            aria-label="Trend direction"
            className="flex gap-1 rounded-chip border border-border bg-surface-2 p-1"
          >
            {(["rising", "declining"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={direction === option}
                onClick={() => setFilters({ trend: option })}
                className={
                  direction === option
                    ? "rounded-chip bg-accent px-3 py-1.5 text-sm font-medium text-black"
                    : "rounded-chip px-3 py-1.5 text-sm text-muted hover:text-text"
                }
              >
                {option === "rising" ? "↑ Rising" : "↓ Declining"}
              </button>
            ))}
          </div>
        </header>

        <section className="mt-8" aria-labelledby="trending-dishes">
          <h2 id="trending-dishes" className="mb-4 font-display text-title text-text">
            Dishes
          </h2>

          {isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }, (_, index) => (
                <DishCardSkeleton key={index} />
              ))}
            </div>
          ) : data && data.dishes.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.dishes.map((item) =>
                item.dish ? (
                  <Link key={item.dish.slug} to={`/dish/${item.dish.slug}`}>
                    <Card interactive animate className="h-full">
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="font-display text-xl leading-tight text-text">
                          {item.dish.name}
                        </h3>
                        <ScorePill value={item.score} />
                      </div>

                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Trend
                          trend={{
                            direction: item.direction,
                            delta: item.delta,
                            significant: item.significant,
                          }}
                          showLabel
                        />
                        {!item.significant && (
                          <Badge
                            tone="neutral"
                            title="More people are talking about it, but opinion is steady"
                          >
                            attention only
                          </Badge>
                        )}
                      </div>

                      <p className="mt-3 text-sm text-subtle">
                        {formatCount(item.recent_count)} mentions in the recent window
                      </p>
                    </Card>
                  </Link>
                ) : null,
              )}
            </div>
          ) : (
            <Card className="text-center">
              <p className="text-muted">
                Nothing is {direction} with enough evidence to say so yet.
              </p>
              <p className="mt-2 text-sm text-subtle">
                Trends need at least three mentions in both the recent and historical windows.
              </p>
            </Card>
          )}
        </section>

        {data && data.restaurants.length > 0 && (
          <section className="mt-12" aria-labelledby="trending-restaurants">
            <h2 id="trending-restaurants" className="mb-4 font-display text-title text-text">
              Places
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {data.restaurants.map((item) =>
                item.restaurant ? (
                  <Link key={item.restaurant.id} to={`/restaurant/${item.restaurant.id}`}>
                    <Card interactive animate className="h-full">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate font-display text-xl leading-tight text-text">
                            {item.restaurant.name}
                          </h3>
                          {item.restaurant.area && (
                            <p className="text-sm text-subtle">{item.restaurant.area}</p>
                          )}
                        </div>
                        <ScorePill value={item.score} />
                      </div>
                      <div className="mt-3">
                        <Trend
                          trend={{
                            direction: item.direction,
                            delta: item.delta,
                            significant: item.significant,
                          }}
                          showLabel
                        />
                      </div>
                    </Card>
                  </Link>
                ) : null,
              )}
            </div>
          </section>
        )}
      </div>
    </>
  );
}
