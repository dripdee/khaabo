/**
 * Design tokens live here, not scattered in components.
 *
 * Dark-first: the `dark` class is on <html> by default and the same token names
 * resolve through CSS variables, so a light theme is a variable swap rather than a
 * second set of utility classes.
 */
import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        "surface-2": "rgb(var(--surface-2) / <alpha-value>)",
        border: "rgb(var(--border) / <alpha-value>)",
        text: "rgb(var(--text) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        subtle: "rgb(var(--subtle) / <alpha-value>)",
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          soft: "rgb(var(--accent-soft) / <alpha-value>)",
        },
        accent2: "rgb(var(--accent-2) / <alpha-value>)",
        positive: "rgb(var(--positive) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
        negative: "rgb(var(--negative) / <alpha-value>)",
      },
      fontFamily: {
        display: ['"Instrument Serif"', "Georgia", "Cambria", "serif"],
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        // Editorial scale: large display sizes, tight leading.
        display: ["clamp(2.75rem, 7vw, 5rem)", { lineHeight: "0.95", letterSpacing: "-0.02em" }],
        hero: ["clamp(2rem, 5vw, 3.25rem)", { lineHeight: "1.02", letterSpacing: "-0.015em" }],
        title: ["clamp(1.5rem, 3vw, 2rem)", { lineHeight: "1.1", letterSpacing: "-0.01em" }],
      },
      borderRadius: {
        card: "20px",
        input: "14px",
        chip: "999px",
      },
      boxShadow: {
        card: "0 1px 2px rgb(0 0 0 / 0.4), 0 12px 32px -12px rgb(0 0 0 / 0.6)",
        lift: "0 2px 4px rgb(0 0 0 / 0.35), 0 18px 44px -14px rgb(0 0 0 / 0.7)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.25), 0 8px 40px -12px rgb(var(--accent) / 0.35)",
      },
      transitionTimingFunction: {
        soft: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      transitionDuration: {
        fast: "120ms",
        base: "220ms",
        slow: "380ms",
      },
      maxWidth: {
        content: "1200px",
        prose: "68ch",
      },
      keyframes: {
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
