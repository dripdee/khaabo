import { motion } from "framer-motion";

import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { formatMonth, formatPercent } from "@/lib/format";
import { DURATION, EASE } from "@/lib/motion";
import type { RecentSignal } from "@/types/api";

/**
 * Sentiment over the last few months.
 *
 * Deliberately a sparse bar chart rather than a smoothed line: months with few
 * mentions are shown as short bars with their count, so a "90% positive" month built
 * on two reviews is visibly weaker than one built on forty.
 */
export function RecentSignals({ signals }: { signals: RecentSignal[] }) {
  const variants = useMotionVariants({
    hidden: { opacity: 0 },
    visible: { opacity: 1 },
  });

  if (signals.length < 2) return null;

  const maxMentions = Math.max(...signals.map((signal) => signal.mentions), 1);

  return (
    <section aria-labelledby="signals-heading">
      <h2 id="signals-heading" className="mb-1 font-display text-title text-text">
        Recent signals
      </h2>
      <p className="mb-4 text-sm text-subtle">
        Positive share per month. Bar height shows how much evidence that month carried.
      </p>

      <motion.ul
        variants={variants}
        initial="hidden"
        animate="visible"
        className="flex items-end gap-2 sm:gap-3"
      >
        {signals.map((signal, index) => {
          const height = Math.max(8, (signal.mentions / maxMentions) * 100);
          const positive = signal.positive_ratio;
          const color =
            positive >= 0.8
              ? "rgb(var(--positive))"
              : positive >= 0.6
                ? "rgb(var(--accent-2))"
                : positive >= 0.4
                  ? "rgb(var(--warning))"
                  : "rgb(var(--negative))";

          return (
            <li key={signal.period} className="flex flex-1 flex-col items-center gap-2">
              <span className="text-[11px] tabular-nums text-muted">
                {formatPercent(positive)}
              </span>
              <div className="flex h-24 w-full items-end">
                <motion.div
                  initial={{ height: 0 }}
                  animate={{ height: `${height}%` }}
                  transition={{ duration: DURATION.slow, ease: EASE, delay: index * 0.04 }}
                  className="w-full rounded-t-md"
                  style={{ backgroundColor: color, opacity: 0.85 }}
                  title={`${signal.mentions} mention${signal.mentions === 1 ? "" : "s"} · ${formatPercent(positive)} positive`}
                />
              </div>
              <span className="text-[10px] uppercase tracking-wide text-subtle">
                {formatMonth(signal.period)}
              </span>
            </li>
          );
        })}
      </motion.ul>
    </section>
  );
}
