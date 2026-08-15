import { motion } from "framer-motion";
import { useSearchParams } from "react-router-dom";

import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { ListSkeleton } from "@/components/Skeleton";
import { DishCard } from "@/features/dishes/DishCard";
import { RestaurantCard } from "@/features/restaurants/RestaurantCard";
import { FiltersRail, FiltersSheet } from "@/features/search/Filters";
import { SearchBox } from "@/features/search/SearchBox";
import { useSearch, useSearchFilters } from "@/features/search/useSearch";
import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { formatPrice } from "@/lib/format";
import { staggerContainer } from "@/lib/motion";
import { Seo } from "@/lib/seo";

/**
 * Search results.
 *
 * When the query resolves to a dish, the primary result is the ranked restaurant list
 * for that dish — not a list of restaurants matching the words. The parsed-intent
 * summary is shown so the user can see (and undo) what we inferred from their text.
 */
export default function SearchPage() {
  const [searchParams] = useSearchParams();
  const { filters, setFilters, clearFilters, activeFilterCount } = useSearchFilters();
  const containerVariants = useMotionVariants(staggerContainer);

  const query = searchParams.get("q") ?? "";
  const { data, isLoading, isPlaceholderData } = useSearch(filters);

  const parsed = data?.parsed;
  const dishIntent = data?.intent === "dish";
  const dish = data?.dishes?.[0];

  return (
    <>
      <Seo
        title={query ? `${query} · Search` : "Search"}
        description={`Dish-first search results${query ? ` for “${query}”` : ""} in Kolkata.`}
        canonicalPath="/search"
        noIndex
      />

      <div className="mx-auto max-w-content px-4 py-8">
        <SearchBox initialValue={query} size="md" />

        {parsed && (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-subtle">We read that as:</span>
            {parsed.dish_terms.map((term) => (
              <span key={term} className="chip border-accent/30 text-accent">
                dish: {term}
              </span>
            ))}
            {parsed.area && <span className="chip">area: {parsed.area}</span>}
            {parsed.max_price != null && (
              <span className="chip">under {formatPrice(parsed.max_price)}</span>
            )}
            {parsed.dietary && <span className="chip">{parsed.dietary.replace("_", "-")}</span>}
            {parsed.mood && <span className="chip">for {parsed.mood.replace("_", " ")}</span>}
            {parsed.near_me && <span className="chip">near me</span>}
          </div>
        )}

        <div className="mt-8 grid gap-8 lg:grid-cols-[240px_1fr]">
          <FiltersRail
            filters={filters}
            onChange={setFilters}
            onClear={() => clearFilters()}
            activeCount={activeFilterCount}
          />

          <div>
            <div className="mb-4 flex items-end justify-between gap-3">
              <p aria-live="polite" className="text-sm text-subtle">
                {isLoading
                  ? "Searching…"
                  : dishIntent && dish
                    ? `${data?.total ?? 0} place${(data?.total ?? 0) === 1 ? "" : "s"} ranked for ${dish.name}`
                    : `${data?.total ?? 0} dish${(data?.total ?? 0) === 1 ? "" : "es"} found`}
              </p>
              <div className="lg:hidden">
                <FiltersSheet
                  filters={filters}
                  onChange={setFilters}
                  onClear={() => clearFilters()}
                  activeCount={activeFilterCount}
                />
              </div>
            </div>

            {isLoading && !isPlaceholderData ? (
              <ListSkeleton count={5} variant={dishIntent ? "restaurant" : "dish"} />
            ) : !query ? (
              <Card className="text-center">
                <p className="text-muted">
                  Start with a dish — try “best chicken momo” or “biryani under ₹300”.
                </p>
              </Card>
            ) : dishIntent && data && data.restaurants.length > 0 ? (
              <motion.div
                variants={containerVariants}
                initial="hidden"
                animate="visible"
                className={isPlaceholderData ? "space-y-3 opacity-60" : "space-y-3"}
              >
                {data.restaurants.map((restaurant, index) => (
                  <RestaurantCard
                    key={restaurant.id}
                    restaurant={restaurant}
                    dishId={dish?.id}
                    dishName={dish?.name}
                    rank={restaurant.status === "ranked" ? index + 1 : undefined}
                  />
                ))}
              </motion.div>
            ) : data && data.dishes.length > 0 ? (
              <motion.div
                variants={containerVariants}
                initial="hidden"
                animate="visible"
                className="grid gap-3 sm:grid-cols-2"
              >
                {data.dishes.map((item) => (
                  <DishCard key={item.slug} dish={item} />
                ))}
              </motion.div>
            ) : (
              <Card className="text-center">
                <p className="text-muted">
                  Nothing matched “{query}”
                  {activeFilterCount > 0 ? " with these filters" : ""}.
                </p>
                <p className="mt-2 text-sm text-subtle">
                  Either we haven&apos;t collected evidence for it yet, or the dish is not in the
                  taxonomy.
                </p>
                {activeFilterCount > 0 && (
                  <Button variant="ghost" className="mt-4" onClick={() => clearFilters()}>
                    Clear filters
                  </Button>
                )}
              </Card>
            )}

            {data?.has_more && (
              <div className="mt-6 flex justify-center">
                <Button
                  variant="secondary"
                  onClick={() => setFilters({ page: (filters.page ?? 1) + 1 })}
                >
                  Load more
                </Button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
