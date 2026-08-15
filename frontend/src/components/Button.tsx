import { motion } from "framer-motion";
import { forwardRef } from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { cn } from "@/lib/format";
import { DURATION, EASE } from "@/lib/motion";

type Variant = "primary" | "secondary" | "ghost" | "outline" | "danger";
type Size = "sm" | "md" | "lg" | "icon";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-black hover:bg-accent/90 active:bg-accent/95 shadow-glow font-semibold",
  secondary: "bg-surface-2 text-text hover:bg-surface-2/70 border border-border",
  ghost: "text-muted hover:text-text hover:bg-surface-2",
  outline: "border border-border text-text hover:border-accent/60 hover:text-accent",
  danger: "bg-negative/15 text-negative border border-negative/30 hover:bg-negative/25",
};

const SIZES: Record<Size, string> = {
  // 44px minimum touch target on the sizes used in mobile flows.
  sm: "h-9 px-3 text-sm gap-1.5",
  md: "h-11 px-4 text-sm gap-2",
  lg: "h-12 px-6 text-base gap-2",
  icon: "h-11 w-11 p-0",
};

/**
 * React's native drag/animation handlers collide with Framer Motion's own
 * gesture props of the same names, so they are omitted rather than cast away.
 */
type NativeButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "onDrag" | "onDragStart" | "onDragEnd" | "onAnimationStart" | "onAnimationEnd"
>;

export interface ButtonProps extends NativeButtonProps {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "primary",
    size = "md",
    loading = false,
    leadingIcon,
    trailingIcon,
    fullWidth,
    className,
    children,
    disabled,
    ...rest
  },
  ref,
) {
  const variants = useMotionVariants({
    rest: { scale: 1 },
    tap: { scale: 0.98 },
  });

  return (
    <motion.button
      ref={ref}
      variants={variants}
      initial="rest"
      whileTap={disabled || loading ? undefined : "tap"}
      transition={{ duration: DURATION.fast, ease: EASE }}
      // `aria-busy` matters because the label stays put while loading; screen
      // readers otherwise get no signal that the action is in flight.
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center rounded-input transition-colors duration-fast",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? (
        <span
          className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
          aria-hidden
        />
      ) : (
        leadingIcon
      )}
      {children}
      {!loading && trailingIcon}
    </motion.button>
  );
});
