/**
 * Reduced-motion aware variant helper.
 *
 * Framer's own `useReducedMotion` returns the preference; this maps it onto our
 * variant sets so every component gets the same treatment (opacity only, no
 * transforms) without repeating the logic.
 */
import { useReducedMotion } from "framer-motion";
import { useMemo } from "react";
import type { Variants } from "framer-motion";

import { toReducedMotion } from "@/lib/motion";

export function usePrefersReducedMotion(): boolean {
  return useReducedMotion() ?? false;
}

export function useMotionVariants(variants: Variants): Variants {
  const reduced = usePrefersReducedMotion();
  return useMemo(() => (reduced ? toReducedMotion(variants) : variants), [reduced, variants]);
}
