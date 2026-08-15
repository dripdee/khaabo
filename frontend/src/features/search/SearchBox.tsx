import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/Button";
import { useDebounce } from "@/hooks/useDebounce";
import { useGeolocation } from "@/hooks/useGeolocation";
import { useMotionVariants } from "@/hooks/usePrefersReducedMotion";
import { cn } from "@/lib/format";
import { fadeUp } from "@/lib/motion";
import { useSuggest } from "@/features/search/useSearch";

const EXAMPLES = [
  "best chicken momo",
  "biryani under ₹300",
  "best ramen near Salt Lake",
  "cafes for working",
  "cheap kathi roll",
];

export interface SearchBoxProps {
  initialValue?: string;
  size?: "md" | "lg";
  autoFocus?: boolean;
  showExamples?: boolean;
  className?: string;
  onSubmit?: (query: string) => void;
}

/**
 * The product's primary input.
 *
 * Suggestions are keyboard-navigable and a dish suggestion routes straight to the
 * dish page — that is the shortest path to the core loop (dish → places → evidence).
 */
export function SearchBox({
  initialValue = "",
  size = "lg",
  autoFocus = false,
  showExamples = false,
  className,
  onSubmit,
}: SearchBoxProps) {
  const navigate = useNavigate();
  const [value, setValue] = useState(initialValue);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [exampleIndex, setExampleIndex] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const debounced = useDebounce(value, 220);
  const { data: suggestions = [] } = useSuggest(debounced, open);
  const geo = useGeolocation();
  const variants = useMotionVariants(fadeUp);

  // Rotating placeholder teaches the query language without a help panel.
  useEffect(() => {
    if (!showExamples || value) return;
    const timer = window.setInterval(
      () => setExampleIndex((index) => (index + 1) % EXAMPLES.length),
      3200,
    );
    return () => window.clearInterval(timer);
  }, [showExamples, value]);

  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  function submit(query: string) {
    const trimmed = query.trim();
    if (!trimmed) return;
    setOpen(false);
    inputRef.current?.blur();

    if (onSubmit) {
      onSubmit(trimmed);
      return;
    }

    const params = new URLSearchParams({ q: trimmed });
    if (geo.coords && /near me|nearby|closest/i.test(trimmed)) {
      params.set("lat", String(geo.coords.lat));
      params.set("lng", String(geo.coords.lng));
    }
    navigate(`/search?${params.toString()}`);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((index) => Math.min(index + 1, suggestions.length - 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, -1));
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const active = activeIndex >= 0 ? suggestions[activeIndex] : undefined;
      if (active) {
        choose(active.kind, active.slug ?? active.id ?? "", active.label);
      } else {
        submit(value);
      }
    }
  }

  function choose(kind: string, identifier: string, label: string) {
    setOpen(false);
    if (kind === "dish" && identifier) {
      navigate(`/dish/${identifier}`);
      return;
    }
    if (kind === "restaurant" && identifier) {
      navigate(`/restaurant/${identifier}`);
      return;
    }
    submit(label);
  }

  const inputHeight = size === "lg" ? "h-14 text-lg" : "h-12 text-base";

  return (
    <div ref={containerRef} className={cn("relative w-full", className)}>
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          submit(value);
        }}
        className="relative"
      >
        <div
          className={cn(
            "flex items-center gap-2 rounded-input border border-border bg-surface px-4",
            "shadow-card transition-all duration-base focus-within:border-accent/60",
            "focus-within:shadow-glow",
            inputHeight,
          )}
        >
          <span aria-hidden className="text-lg text-subtle">
            ⌕
          </span>
          <input
            ref={inputRef}
            type="search"
            value={value}
            autoFocus={autoFocus}
            onChange={(event) => {
              setValue(event.target.value);
              setOpen(true);
              setActiveIndex(-1);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
            placeholder={showExamples && !value ? EXAMPLES[exampleIndex] : "Search a dish…"}
            aria-label="Search for a dish, restaurant, cuisine or area"
            aria-autocomplete="list"
            aria-expanded={open && suggestions.length > 0}
            aria-controls="search-suggestions"
            className="h-full flex-1 bg-transparent text-text placeholder:text-subtle focus:outline-none
              [&::-webkit-search-cancel-button]:hidden"
          />
          {value && (
            <button
              type="button"
              onClick={() => {
                setValue("");
                inputRef.current?.focus();
              }}
              aria-label="Clear search"
              className="text-subtle transition-colors hover:text-text"
            >
              ✕
            </button>
          )}
          <Button type="submit" size={size === "lg" ? "md" : "sm"} className="shrink-0">
            Search
          </Button>
        </div>
      </form>

      <AnimatePresence>
        {open && suggestions.length > 0 && (
          <motion.ul
            id="search-suggestions"
            role="listbox"
            variants={variants}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="absolute left-0 right-0 top-full z-30 mt-2 overflow-hidden rounded-card
              border border-border bg-surface shadow-lift"
          >
            {suggestions.map((item, index) => (
              <li key={`${item.kind}-${item.slug ?? item.id ?? item.label}`} role="option" aria-selected={index === activeIndex}>
                <button
                  type="button"
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => choose(item.kind, item.slug ?? item.id ?? "", item.label)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors",
                    index === activeIndex ? "bg-surface-2" : "bg-transparent",
                  )}
                >
                  <span className="flex items-center gap-3">
                    <span aria-hidden className="text-base">
                      {item.kind === "dish" ? "🍽️" : item.kind === "restaurant" ? "📍" : "🏷️"}
                    </span>
                    <span>
                      <span className="block text-sm font-medium text-text">{item.label}</span>
                      {item.subtitle && (
                        <span className="block text-xs text-subtle">{item.subtitle}</span>
                      )}
                    </span>
                  </span>
                  <span className="text-[10px] uppercase tracking-wide text-subtle">
                    {item.kind}
                  </span>
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>

      {showExamples && (
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.slice(0, 4).map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setValue(example);
                submit(example);
              }}
              className="chip transition-colors hover:border-accent/50 hover:text-accent"
            >
              {example}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
