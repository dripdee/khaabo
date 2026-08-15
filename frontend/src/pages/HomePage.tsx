import { motion } from "framer-motion";
import { Link } from "react-router-dom";

import { Card } from "@/components/Card";
import { ScorePill } from "@/components/Score";
import { Trend } from "@/components/Trend";
import { DishCardSkeleton } from "@/components/Skeleton";
import { SearchBox } from "@/features/search/SearchBox";
import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { formatCount } from "@/lib/format";
import { fadeUp, staggerContainer, staggerItem } from "@/lib/motion";
import { Seo } from "@/lib/seo";
import { useTrending } from "@/features/dishes/useTrending";

const BROWSE = [
  { slug: "chicken-momo", label: "Chicken Momo", emoji: "🥟" },
  { slug: "chicken-biryani", label: "Biryani", emoji: "🍛" },
  { slug: "kathi-roll", label: "Kathi Roll", emoji: "🌯" },
  { slug: "kosha-mangsho", label: "Kosha Mangsho", emoji: "🍲" },
  { slug: "puchka", label: "Puchka", emoji: "🥔" },
  { slug: "ramen", label: "Ramen", emoji: "🍜" },
  { slug: "cold-coffee", label: "Cold Coffee", emoji: "☕" },
  { slug: "mishti-doi", label: "Mishti Doi", emoji: "🍮" },
];

/**
 * Home / Search — priority screen 1.
 *
 * The page states the product's premise in one line and puts the dish search box
 * immediately below it. Everything else is a shortcut into the same loop.
 */
export default function HomePage() {
  const heroVariants = useMotionVariants(fadeUp);
  const containerVariants = useMotionVariants(staggerContainer);
  const itemVariants = useMotionVariants(staggerItem);

  const { data: trending, isLoading } = useTrending({ limit: 6 });

  return (
    <>
      <Seo
        title="Khaabo — What should I eat, and where?"
        description="Dish-first food discovery for Kolkata. Search a dish, see the best places for it, and read the evidence behind every ranking."
        canonicalPath="/"
      />

      <section className="relative overflow-hidden">
        <div className="hero-glow absolute inset-x-0 top-0 h-[420px]" aria-hidden />

        <motion.div
          variants={heroVariants}
          initial="hidden"
          animate="visible"
          className="relative mx-auto max-w-3xl px-4 pb-10 pt-16 text-center sm:pt-24"
        >
          <p className="text-xs uppercase tracking-[0.22em] text-subtle">
            Kolkata · dish-first discovery
          </p>

          <h1 className="mt-5 text-display font-display text-text">
            What should I eat,
            <br />
            <span className="italic text-accent">and where?</span>
          </h1>

          <p className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-muted">
            Search a dish, not a restaurant. We rank places for that exact dish and show
            you the evidence behind every position.
          </p>

          <div className="mt-8">
            <SearchBox size="lg" autoFocus showExamples />
          </div>
        </motion.div>
      </section>

      <div className="mx-auto max-w-content space-y-14 px-4 pb-20">
        <motion.section
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          aria-labelledby="browse-heading"
        >
          <h2 id="browse-heading" className="mb-4 font-display text-title text-text">
            Start with a dish
          </h2>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {BROWSE.map((dish) => (
              <motion.div key={dish.slug} variants={itemVariants}>
                <Link
                  to={`/dish/${dish.slug}`}
                  className="group flex h-full flex-col justify-between rounded-card border border-border
                    bg-surface p-4 transition-all duration-base hover:-translate-y-0.5
                    hover:border-accent/40 hover:shadow-lift"
                >
                  <span aria-hidden className="text-2xl">
                    {dish.emoji}
                  </span>
                  <span className="mt-3 font-display text-lg leading-tight text-text transition-colors group-hover:text-accent">
                    {dish.label}
                  </span>
                </Link>
              </motion.div>
            ))}
          </div>
        </motion.section>

        <section aria-labelledby="trending-heading">
          <div className="mb-4 flex items-end justify-between">
            <div>
              <h2 id="trending-heading" className="font-display text-title text-text">
                Rising right now
              </h2>
              <p className="mt-1 text-sm text-subtle">
                Dishes whose recent reviews are outpacing their history.
              </p>
            </div>
            <Link to="/trending" className="text-sm text-accent hover:underline">
              See all
            </Link>
          </div>

          {isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }, (_, index) => (
                <DishCardSkeleton key={index} />
              ))}
            </div>
          ) : trending && trending.dishes.length > 0 ? (
            <motion.div
              variants={containerVariants}
              initial="hidden"
              animate="visible"
              className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
            >
              {trending.dishes.slice(0, 6).map((item) =>
                item.dish ? (
                  <motion.div key={item.dish.slug} variants={itemVariants}>
                    <Link to={`/dish/${item.dish.slug}`}>
                      <Card interactive className="h-full">
                        <div className="flex items-start justify-between gap-3">
                          <h3 className="font-display text-xl leading-tight text-text">
                            {item.dish.name}
                          </h3>
                          <ScorePill value={item.score} />
                        </div>
                        <div className="mt-3 flex items-center gap-2">
                          <Trend
                            trend={{
                              direction: item.direction,
                              delta: item.delta,
                              significant: item.significant,
                            }}
                            showLabel
                          />
                          <span className="text-xs text-subtle">
                            {formatCount(item.recent_count)} recent mentions
                          </span>
                        </div>
                      </Card>
                    </Link>
                  </motion.div>
                ) : null,
              )}
            </motion.div>
          ) : (
            /* Honest empty state: a fresh install has no trend data yet, and inventing
               "trending" dishes would undermine the whole premise. */
            <Card className="text-center">
              <p className="text-muted">
                No trends yet — we need a few months of reviews in both comparison windows
                before we can claim something is rising.
              </p>
            </Card>
          )}
        </section>

        <section className="grid gap-4 sm:grid-cols-3" aria-labelledby="how-heading">
          <h2 id="how-heading" className="sr-only">
            How Khaabo ranks
          </h2>

          {[
            {
              title: "Dish-specific",
              body: "A place can make outstanding momo and forgettable biryani. We score each dish separately instead of averaging a restaurant into one number.",
            },
            {
              title: "Evidence, not vibes",
              body: "Every rank carries a short reason built from stored review data — sentiment, recency, consistency and how many mentions it rests on.",
            },
            {
              title: "Silence over guessing",
              body: "Fewer than three dish mentions and we say “not enough data”. We would rather show a gap than invent a ranking.",
            },
          ].map((item) => (
            <Card key={item.title} className="bg-surface/60">
              <h3 className="font-display text-xl text-text">{item.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{item.body}</p>
            </Card>
          ))}
        </section>
      </div>
    </>
  );
}
