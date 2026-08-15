import { AnimatePresence, motion } from "framer-motion";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { cn } from "@/lib/format";
import { drawerRight, drawerUp, fadeIn } from "@/lib/motion";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
  /**
   * "bottom" is the mobile default: a bottom sheet keeps controls in thumb reach,
   * where a side panel would not be.
   */
  side?: "bottom" | "right";
  className?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  footer,
  side = "bottom",
  className,
}: DrawerProps) {
  const overlayVariants = useMotionVariants(fadeIn);
  const panelVariants = useMotionVariants(side === "bottom" ? drawerUp : drawerRight);

  useEffect(() => {
    if (!open) return;

    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = overflow;
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div
            variants={overlayVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={onClose}
            className="absolute inset-0 bg-black/60"
            aria-hidden
          />
          <motion.div
            variants={panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            role="dialog"
            aria-modal="true"
            aria-label={title}
            className={cn(
              "glass absolute border-border shadow-lift",
              side === "bottom"
                ? // Safe-area padding so the footer clears the iOS home indicator.
                  "inset-x-0 bottom-0 max-h-[85vh] rounded-t-card border-t pb-[env(safe-area-inset-bottom)]"
                : "inset-y-0 right-0 w-full max-w-md border-l",
              className,
            )}
          >
            {side === "bottom" && (
              <div className="flex justify-center pt-3" aria-hidden>
                <span className="h-1 w-10 rounded-full bg-border" />
              </div>
            )}

            <div className="flex items-center justify-between px-5 py-4">
              {title && <h2 className="font-display text-xl text-text">{title}</h2>}
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="grid h-9 w-9 place-items-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-text"
              >
                ✕
              </button>
            </div>

            <div className="max-h-[60vh] overflow-y-auto px-5 pb-5">{children}</div>

            {footer && (
              <div className="border-t border-border px-5 py-4">{footer}</div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
