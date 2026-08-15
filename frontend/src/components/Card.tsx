import { motion } from "framer-motion";
import type { HTMLAttributes, ReactNode } from "react";

import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { cn } from "@/lib/format";
import { DURATION, EASE, staggerItem } from "@/lib/motion";

/**
 * React's native drag/animation handlers collide with Framer Motion's gesture props
 * of the same names, so they are omitted rather than cast away.
 */
type NativeDivProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  "onDrag" | "onDragStart" | "onDragEnd" | "onAnimationStart" | "onAnimationEnd"
>;

export interface CardProps extends NativeDivProps {
  /** Enables the 2px hover lift. Off for static/informational cards. */
  interactive?: boolean;
  /** Participates in a parent's stagger sequence. */
  animate?: boolean;
  padded?: boolean;
  children: ReactNode;
}

export function Card({
  interactive = false,
  animate = false,
  padded = true,
  className,
  children,
  ...rest
}: CardProps) {
  const variants = useMotionVariants(staggerItem);

  return (
    <motion.div
      variants={animate ? variants : undefined}
      whileHover={interactive ? { y: -2 } : undefined}
      transition={{ duration: DURATION.fast, ease: EASE }}
      className={cn(
        "card overflow-hidden",
        padded && "p-5",
        interactive && "cursor-pointer transition-shadow duration-base hover:shadow-lift",
        className,
      )}
      {...rest}
    >
      {children}
    </motion.div>
  );
}

export function CardHeader({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("mb-3 flex items-start justify-between gap-3", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardTitle({ className, children, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 className={cn("font-display text-xl leading-tight text-text", className)} {...rest}>
      {children}
    </h3>
  );
}

export function CardMeta({ className, children, ...rest }: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={cn("text-sm text-muted", className)} {...rest}>
      {children}
    </p>
  );
}

export function CardFooter({ className, children, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("mt-4 flex items-center gap-2 border-t border-border pt-3", className)}
      {...rest}
    >
      {children}
    </div>
  );
}
