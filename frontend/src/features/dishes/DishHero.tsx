import { motion } from "framer-motion";

import { Badge, NotEnoughData } from "@/components/Badge";
import { Score } from "@/components/Score";
import { Trend } from "@/components/Trend";
import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { formatCount, formatPriceRange } from "@/lib/format";
import { fadeUp } from "@/lib/motion";
import type { DishDetail } from "@/types/api";

/**
 * Dish page hero.
 *
 * Answers "is this dish worth seeking out here" before the reader scrolls: score,
 * trend, how much evidence exists, price band, and an evidence-derived summary.
 */
export function DishHero({ detail }: { detail: DishDetail }) {
  const variants = useMotionVariants(fadeUp);
  const isRanked = detail.status === "ranked";

  return (
    <motion.header
      variants={variants}
      initial="hidden"
      animate="visible"
      className="relative overflow-hidden rounded-card border border-border bg-surface"
    >
      <div className="hero-glow absolute inset-0" aria-hidden />

      <div className="relative p-6 sm:p-8">
        <p className="text-xs uppercase tracking-[0.18em] text-subtle">
          {detail.dish.cuisine ? `${detail.dish.cuisine} · ` : ""}
          Best in {detail.city_slug.charAt(0).toUpperCase() + detail.city_slug.slice(1)}
        </p>

        <div className="mt-3 flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0 flex-1">
            <h1 className="text-hero font-display text-text">{detail.dish.name}</h1>

            <div className="mt-4 flex flex-wrap items-center gap-2">
              {detail.dish.is_veg === true && <Badge tone="positive">Veg</Badge>}
              {detail.dish.is_veg === false && <Badge tone="negative">Non-veg</Badge>}
              <Trend trend={detail.trend} showLabel />
              {detail.price_range && (detail.price_range.min || detail.price_range.max) && (
                <Badge tone="neutral" title="Typical price range across places">
                  {formatPriceRange(detail.price_range.min, detail.price_range.max)}
                </Badge>
              )}
              {isRanked ? (
                <Badge tone="neutral">
                  {detail.restaurant_count} ranked place
                  {detail.restaurant_count === 1 ? "" : "s"}
                </Badge>
              ) : (
                <NotEnoughData detail="We need at least 3 dish mentions to rank places" />
              )}
            </div>

            {detail.summary ? (
              <p className="mt-5 max-w-prose text-base leading-relaxed text-muted">
                {detail.summary.text}
                <span className="ml-2 text-xs text-subtle">
                  ({detail.summary.generated_by === "template" ? "from stored evidence" : "summarised from evidence"})
                </span>
              </p>
            ) : (
              <p className="mt-5 max-w-prose text-base text-subtle">
                We haven&apos;t collected enough reviews for {detail.dish.name} yet. Add one and it
                will start shaping the rankings.
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-6">
            <Score value={isRanked ? detail.score : null} size="lg" label="Dish score" />
            <div className="text-sm">
              <p className="text-subtle">Evidence</p>
              <p className="font-display text-2xl text-text">
                {formatCount(detail.mention_count)}
              </p>
              <p className="text-xs text-subtle">dish mentions</p>
            </div>
          </div>
        </div>

        {(detail.positive_attributes.length > 0 || detail.negative_attributes.length > 0) && (
          <div className="mt-6 flex flex-wrap gap-6">
            {detail.positive_attributes.length > 0 && (
              <div>
                <p className="mb-2 text-xs uppercase tracking-wide text-subtle">
                  Often described as
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {detail.positive_attributes.slice(0, 6).map((attribute) => (
                    <Badge key={attribute.label} tone="positive">
                      {attribute.label}
                      <span className="text-[10px] opacity-70">{attribute.count}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {detail.negative_attributes.length > 0 && (
              <div>
                <p className="mb-2 text-xs uppercase tracking-wide text-subtle">
                  Common complaints
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {detail.negative_attributes.slice(0, 4).map((attribute) => (
                    <Badge key={attribute.label} tone="warning">
                      {attribute.label}
                      <span className="text-[10px] opacity-70">{attribute.count}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </motion.header>
  );
}
