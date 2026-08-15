import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Badge, DnaChipView, NotEnoughData, WhyChips } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Score, ScorePill } from "@/components/Score";
import { ListSkeleton } from "@/components/Skeleton";
import { Trend } from "@/components/Trend";
import { BookmarkButton } from "@/features/bookmarks/BookmarkButton";
import { ReviewCard } from "@/features/reviews/ReviewCard";
import { STALE, queryKeys } from "@/lib/queryClient";
import { formatPercent, formatPrice, priceLevelLabel } from "@/lib/format";
import { restaurantJsonLd } from "@/lib/jsonld";
import { Seo } from "@/lib/seo";
import { restaurantsApi } from "@/services/endpoints";

/**
 * Restaurant page.
 *
 * Structured around "what should I order here": Food DNA first, then dishes ranked
 * within this restaurant, then the raw reviews. A restaurant-level score is shown but
 * kept secondary — the dish scores are the useful signal.
 */
export default function RestaurantPage() {
  const { id } = useParams<{ id: string }>();

  const { data: restaurant, isLoading } = useQuery({
    queryKey: queryKeys.restaurant(id ?? ""),
    queryFn: () => restaurantsApi.detail(id as string),
    enabled: Boolean(id),
    staleTime: STALE.restaurant,
  });

  const { data: dishes } = useQuery({
    queryKey: queryKeys.restaurantDishes(id ?? "", 1),
    queryFn: () => restaurantsApi.dishes(id as string, 1),
    enabled: Boolean(id),
    staleTime: STALE.restaurant,
  });

  const { data: reviews, isLoading: reviewsLoading } = useQuery({
    queryKey: queryKeys.restaurantReviews(id ?? "", 1),
    queryFn: () => restaurantsApi.reviews(id as string, 1),
    enabled: Boolean(id),
  });

  if (isLoading || !restaurant) {
    return (
      <div className="mx-auto max-w-content px-4 py-10">
        <ListSkeleton count={4} />
      </div>
    );
  }

  const dna = restaurant.food_dna;
  const rankedDishes = (dishes?.items ?? []).filter((item) => item.status === "ranked");
  const unrankedDishes = (dishes?.items ?? []).filter((item) => item.status !== "ranked");

  return (
    <>
      <Seo
        title={restaurant.name}
        description={`${restaurant.name}${restaurant.area ? ` in ${restaurant.area}` : ""} — dish-level scores, Food DNA and review evidence.`}
        canonicalPath={`/restaurant/${restaurant.id}`}
        jsonLd={restaurantJsonLd({
          name: restaurant.name,
          id: restaurant.id,
          address: restaurant.address,
          lat: restaurant.lat,
          lng: restaurant.lng,
          cuisines: restaurant.cuisines,
          phone: restaurant.phone,
          score: dna?.overall_score,
          reviewCount: restaurant.review_count,
          status: dna?.status,
        })}
      />

      <div className="mx-auto max-w-content space-y-10 px-4 py-8">
        <header className="relative overflow-hidden rounded-card border border-border bg-surface p-6 sm:p-8">
          <div className="hero-glow absolute inset-0" aria-hidden />

          <div className="relative flex flex-wrap items-start justify-between gap-6">
            <div className="min-w-0 flex-1">
              {restaurant.is_closed && (
                <Badge tone="negative" className="mb-3">
                  Reported closed
                </Badge>
              )}

              <h1 className="text-hero font-display text-text">{restaurant.name}</h1>

              <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
                {restaurant.area && <span>{restaurant.area}</span>}
                {restaurant.price_level && (
                  <>
                    <span aria-hidden className="text-subtle">·</span>
                    <span>{priceLevelLabel(restaurant.price_level)}</span>
                  </>
                )}
                {restaurant.cuisines.length > 0 && (
                  <>
                    <span aria-hidden className="text-subtle">·</span>
                    <span>{restaurant.cuisines.join(", ")}</span>
                  </>
                )}
              </p>

              {dna && dna.chips.length > 0 ? (
                <div className="mt-5">
                  <p className="mb-2 text-xs uppercase tracking-wide text-subtle">Food DNA</p>
                  <div className="flex flex-wrap gap-2">
                    {dna.chips.map((chip) => (
                      <DnaChipView
                        key={chip.code}
                        emoji={chip.emoji}
                        label={chip.label}
                        title={`Derived from ${dna.evidence_count} dish mentions`}
                      />
                    ))}
                  </div>
                </div>
              ) : (
                <div className="mt-5">
                  <NotEnoughData detail="Food DNA needs at least 3 dish mentions" />
                  <p className="mt-2 text-sm text-subtle">
                    We build Food DNA from real dish mentions, so it stays empty until there is
                    enough evidence.
                  </p>
                </div>
              )}
            </div>

            <div className="flex shrink-0 items-start gap-4">
              <Score
                value={dna?.status === "ranked" ? dna.overall_score : null}
                size="lg"
                label="Overall"
              />
              <div className="flex flex-col gap-2">
                <BookmarkButton
                  targetType="restaurant"
                  restaurantId={restaurant.id}
                  label={restaurant.name}
                />
                <Trend trend={dna?.trend} />
              </div>
            </div>
          </div>

          <div className="relative mt-6 flex flex-wrap gap-2">
            <Link to={`/review/new?restaurant=${restaurant.id}`}>
              <Button size="sm">Write a review</Button>
            </Link>
            <a
              href={`https://www.openstreetmap.org/directions?to=${restaurant.lat}%2C${restaurant.lng}`}
              target="_blank"
              rel="noreferrer noopener"
            >
              <Button size="sm" variant="secondary">
                Directions
              </Button>
            </a>
            {restaurant.phone && (
              <a href={`tel:${restaurant.phone}`}>
                <Button size="sm" variant="ghost">
                  Call
                </Button>
              </a>
            )}
            {restaurant.website && (
              <a href={restaurant.website} target="_blank" rel="noreferrer noopener">
                <Button size="sm" variant="ghost">
                  Website
                </Button>
              </a>
            )}
          </div>
        </header>

        <section aria-labelledby="dishes-heading">
          <h2 id="dishes-heading" className="mb-1 font-display text-title text-text">
            What to order
          </h2>
          <p className="mb-4 text-sm text-subtle">
            Dishes ranked from this restaurant&apos;s own review evidence.
          </p>

          {rankedDishes.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {rankedDishes.map((item) => (
                <Card key={item.dish.slug} animate interactive>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link
                        to={`/dish/${item.dish.slug}`}
                        className="font-display text-xl leading-tight text-text hover:text-accent"
                      >
                        {item.dish.name}
                      </Link>
                      {item.is_signature && (
                        <Badge tone="accent" className="ml-2 align-middle">
                          Signature
                        </Badge>
                      )}
                    </div>
                    <ScorePill value={item.score} />
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
                    <span>{formatPercent(item.positive_ratio)} positive</span>
                    <span aria-hidden className="text-subtle">·</span>
                    <span>{item.mention_count} mentions</span>
                    {item.price_avg != null && (
                      <>
                        <span aria-hidden className="text-subtle">·</span>
                        <span>~{formatPrice(item.price_avg)}</span>
                      </>
                    )}
                    <Trend trend={item.trend} />
                  </div>

                  <WhyChips why={item.why} className="mt-3 text-xs" />
                </Card>
              ))}
            </div>
          ) : (
            <Card className="text-center">
              <NotEnoughData className="mx-auto" />
              <p className="mt-3 text-muted">
                No dish here has enough mentions to rank yet.
              </p>
            </Card>
          )}

          {unrankedDishes.length > 0 && (
            <p className="mt-3 text-xs text-subtle">
              {unrankedDishes.length} more dish
              {unrankedDishes.length === 1 ? "" : "es"} mentioned, but with too little data to
              rank.
            </p>
          )}
        </section>

        <section aria-labelledby="reviews-heading">
          <h2 id="reviews-heading" className="mb-4 font-display text-title text-text">
            Reviews
            {reviews?.total ? (
              <span className="ml-2 text-base text-subtle">{reviews.total}</span>
            ) : null}
          </h2>

          {reviewsLoading ? (
            <ListSkeleton count={3} />
          ) : reviews && reviews.items.length > 0 ? (
            <div className="space-y-3">
              {reviews.items.map((review) => (
                <ReviewCard key={review.id} review={review} />
              ))}
            </div>
          ) : (
            <Card className="text-center">
              <p className="text-muted">No published reviews yet.</p>
              <Link to={`/review/new?restaurant=${restaurant.id}`} className="mt-4 inline-block">
                <Button size="sm">Be the first</Button>
              </Link>
            </Card>
          )}
        </section>

        <footer className="border-t border-border pt-6">
          <p className="text-xs text-subtle">{restaurant.attribution.join(" · ")}</p>
          {restaurant.address && (
            <p className="mt-1 text-xs text-subtle">{restaurant.address}</p>
          )}
        </footer>
      </div>
    </>
  );
}
