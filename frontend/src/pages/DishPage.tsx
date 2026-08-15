import { motion } from "framer-motion";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { Badge, NotEnoughData } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { DishPageSkeleton, ListSkeleton } from "@/components/Skeleton";
import { BookmarkButton } from "@/features/bookmarks/BookmarkButton";
import { DishHero } from "@/features/dishes/DishHero";
import { DishHighlights } from "@/features/dishes/DishHighlights";
import { RecentSignals } from "@/features/dishes/RecentSignals";
import { useDish, useDishRestaurants } from "@/features/dishes/useDish";
import { RestaurantCard } from "@/features/restaurants/RestaurantCard";
import { FiltersRail, FiltersSheet } from "@/features/search/Filters";
import { useSearchFilters } from "@/features/search/useSearch";
import { useGeolocation } from "@/hooks/useGeolocation";
import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { staggerContainer } from "@/lib/motion";
import { dishJsonLd } from "@/lib/jsonld";
import { Seo } from "@/lib/seo";
import { ApiError } from "@/services/client";

/**
 * Dish page — priority screen 2.
 *
 * The reading order is deliberate: what the dish is like overall → the named picks →
 * the full ranked list → the honest "not enough data" tail. The map lives on a
 * dedicated route so this page stays fast and text-first.
 */
export default function DishPage() {
  const { slug } = useParams<{ slug: string }>();
  const [searchParams] = useSearchParams();
  const { filters, setFilters, clearFilters, activeFilterCount } = useSearchFilters();
  const geo = useGeolocation();
  const containerVariants = useMotionVariants(staggerContainer);

  const coords = filters.lat && filters.lng ? { lat: filters.lat, lng: filters.lng } : geo.coords;

  const {
    data: detail,
    isLoading: detailLoading,
    error: detailError,
  } = useDish(slug, coords ? { lat: coords.lat, lng: coords.lng } : undefined);

  const { data: list, isLoading: listLoading } = useDishRestaurants(slug, {
    ...(coords ? { lat: coords.lat, lng: coords.lng } : {}),
    radius_m: filters.radius_m,
    min_price: filters.min_price,
    max_price: filters.max_price,
    area: filters.area,
    trend: filters.trend,
    sort: (filters.sort as "score" | "distance" | "price" | "trending") ?? "score",
    page: filters.page ?? 1,
    page_size: 20,
  });

  if (detailError instanceof ApiError && detailError.status === 404) {
    return (
      <div className="mx-auto max-w-content px-4 py-24 text-center">
        <h1 className="font-display text-hero text-text">Dish not found</h1>
        <p className="mt-4 text-muted">
          We don&apos;t have <span className="text-text">{slug}</span> in the taxonomy yet.
        </p>
        <Link to="/" className="mt-8 inline-block">
          <Button>Back to search</Button>
        </Link>
      </div>
    );
  }

  if (detailLoading || !detail) {
    return (
      <div className="mx-auto max-w-content px-4 py-10">
        <DishPageSkeleton />
      </div>
    );
  }

  const ranked = list?.items ?? [];
  const insufficient = list?.insufficient ?? [];
  const mapHref = `/dish/${detail.dish.slug}/map${searchParams.toString() ? `?${searchParams}` : ""}`;

  return (
    <>
      <Seo
        title={`Best ${detail.dish.name} in Kolkata`}
        description={
          detail.summary?.text ??
          `Where to eat ${detail.dish.name} in Kolkata, ranked from review evidence with a reason for every position.`
        }
        canonicalPath={`/dish/${detail.dish.slug}`}
        jsonLd={dishJsonLd({
          dishName: detail.dish.name,
          slug: detail.dish.slug,
          cityName: "Kolkata",
          restaurants: ranked.map((restaurant) => ({
            name: restaurant.name,
            id: restaurant.id,
            score: restaurant.score,
            mentionCount: restaurant.mention_count,
            status: restaurant.status,
          })),
        })}
      />

      <div className="mx-auto max-w-content space-y-10 px-4 py-8">
        <nav aria-label="Breadcrumb" className="text-sm text-subtle">
          <Link to="/" className="hover:text-text">
            Khaabo
          </Link>
          <span aria-hidden className="mx-2">
            /
          </span>
          <span className="text-muted">{detail.dish.name}</span>
        </nav>

        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <DishHero detail={detail} />
          </div>
          <BookmarkButton
            targetType="dish"
            dishId={detail.dish.id}
            label={detail.dish.name}
            className="mt-2 shrink-0"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Link to={mapHref}>
            <Button variant="secondary">See on map</Button>
          </Link>
          <Link to={`/review/new?dish=${detail.dish.slug}`}>
            <Button variant="ghost">Write a review</Button>
          </Link>
          {!coords && (
            <Button variant="ghost" onClick={geo.request} loading={geo.status === "prompting"}>
              Sort by distance
            </Button>
          )}
        </div>

        <DishHighlights highlights={detail.highlights} dishName={detail.dish.name} />

        <div className="grid gap-8 lg:grid-cols-[240px_1fr]">
          <FiltersRail
            filters={filters}
            onChange={setFilters}
            onClear={() => clearFilters()}
            activeCount={activeFilterCount}
            showDistance
          />

          <section aria-labelledby="ranked-heading">
            <div className="mb-4 flex items-end justify-between gap-3">
              <div>
                <h2 id="ranked-heading" className="font-display text-title text-text">
                  Ranked for {detail.dish.name}
                </h2>
                <p aria-live="polite" className="mt-1 text-sm text-subtle">
                  {listLoading
                    ? "Loading places…"
                    : `${list?.total ?? 0} ranked place${(list?.total ?? 0) === 1 ? "" : "s"}`}
                </p>
              </div>
              <div className="lg:hidden">
                <FiltersSheet
                  filters={filters}
                  onChange={setFilters}
                  onClear={() => clearFilters()}
                  activeCount={activeFilterCount}
                  showDistance
                />
              </div>
            </div>

            {listLoading ? (
              <ListSkeleton count={5} />
            ) : ranked.length > 0 ? (
              <motion.div
                variants={containerVariants}
                initial="hidden"
                animate="visible"
                className="space-y-3"
              >
                {ranked.map((restaurant, index) => (
                  <RestaurantCard
                    key={restaurant.id}
                    restaurant={restaurant}
                    dishId={detail.dish.id}
                    dishName={detail.dish.name}
                    rank={index + 1 + ((filters.page ?? 1) - 1) * 20}
                  />
                ))}
              </motion.div>
            ) : (
              <Card className="text-center">
                <NotEnoughData className="mx-auto" />
                <p className="mt-4 text-muted">
                  No place has enough {detail.dish.name} evidence to rank
                  {activeFilterCount > 0 ? " with these filters" : ""} yet.
                </p>
                {activeFilterCount > 0 && (
                  <Button variant="ghost" className="mt-4" onClick={() => clearFilters()}>
                    Clear filters
                  </Button>
                )}
              </Card>
            )}

            {list?.has_more && (
              <div className="mt-6 flex justify-center">
                <Button
                  variant="secondary"
                  onClick={() => setFilters({ page: (filters.page ?? 1) + 1 })}
                >
                  Load more
                </Button>
              </div>
            )}

            {insufficient.length > 0 && (
              <details className="mt-8 rounded-card border border-dashed border-border p-4">
                <summary className="cursor-pointer text-sm text-muted">
                  {insufficient.length} place{insufficient.length === 1 ? "" : "s"} with too
                  little data to rank
                </summary>
                <p className="mt-2 text-xs text-subtle">
                  These serve {detail.dish.name} but have fewer than three dish mentions, so
                  ranking them would be guesswork.
                </p>
                <div className="mt-4 space-y-3">
                  {insufficient.map((restaurant) => (
                    <RestaurantCard
                      key={restaurant.id}
                      restaurant={restaurant}
                      dishId={detail.dish.id}
                      dishName={detail.dish.name}
                      compact
                    />
                  ))}
                </div>
              </details>
            )}
          </section>
        </div>

        <RecentSignals signals={detail.recent_signals} />

        {detail.attribution.length > 0 && (
          <footer className="border-t border-border pt-6">
            <p className="text-xs text-subtle">
              Place data {detail.attribution.join(" · ")}
            </p>
            {detail.summary && (
              <p className="mt-1 text-xs text-subtle">
                Summary derived from {detail.summary.mention_count} stored dish mentions.
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone="neutral">Weights: 35% sentiment · 20% recency · 15% consistency</Badge>
            </div>
          </footer>
        )}
      </div>
    </>
  );
}
