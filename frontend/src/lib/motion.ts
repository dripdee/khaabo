/**
 * Motion primitives.
 *
 * One easing curve and three durations across the whole app, so transitions feel
 * like one system. Every variant here is safe to hand to Framer Motion directly;
 * `useReducedMotionVariants` strips transforms when the OS asks for less motion.
 */
import type { Transition, Variants } from "framer-motion";

export const EASE = [0.22, 1, 0.36, 1] as const;

export const DURATION = {
  fast: 0.12,
  base: 0.22,
  slow: 0.38,
} as const;

export const transition: Transition = { duration: DURATION.base, ease: EASE };
export const springy: Transition = { type: "spring", stiffness: 320, damping: 30, mass: 0.8 };

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition },
  exit: { opacity: 0, transition: { duration: DURATION.fast, ease: EASE } },
};

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0, transition },
  exit: { opacity: 0, y: -8, transition: { duration: DURATION.fast, ease: EASE } },
};

export const scaleIn: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  visible: { opacity: 1, scale: 1, transition },
  exit: { opacity: 0, scale: 0.98, transition: { duration: DURATION.fast, ease: EASE } },
};

/** Parent for staggered lists. 30 ms reads as one motion, not a queue. */
export const staggerContainer: Variants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.03, delayChildren: 0.02 },
  },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition },
};

export const drawerUp: Variants = {
  hidden: { y: "100%" },
  visible: { y: 0, transition: springy },
  exit: { y: "100%", transition: { duration: DURATION.base, ease: EASE } },
};

export const drawerRight: Variants = {
  hidden: { x: "100%" },
  visible: { x: 0, transition: springy },
  exit: { x: "100%", transition: { duration: DURATION.base, ease: EASE } },
};

/** Hover lift used on cards. Kept to 2px — premium, not bouncy. */
export const hoverLift = {
  whileHover: { y: -2, transition: { duration: DURATION.fast, ease: EASE } },
  whileTap: { y: 0, scale: 0.995 },
};

/**
 * Strip transforms from a variant set, keeping opacity only.
 *
 * Reduced motion means no movement — but content must still appear, so opacity
 * transitions are retained rather than disabling animation entirely.
 */
export function toReducedMotion(variants: Variants): Variants {
  const result: Variants = {};
  for (const [state, value] of Object.entries(variants)) {
    if (typeof value !== "object" || value === null) {
      result[state] = value;
      continue;
    }
    const record = value as Record<string, unknown>;
    const opacity = record.opacity;
    result[state] = {
      ...(typeof opacity === "number" ? { opacity } : {}),
      transition: (record.transition as Transition) ?? { duration: DURATION.fast },
    };
  }
  return result;
}
