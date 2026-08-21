import { motion, useScroll, useTransform } from "framer-motion";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

import { Button } from "@/components/Button";
import { SearchBox } from "@/features/search/SearchBox";
import { useAuth } from "@/hooks/authContext";
import { cn } from "@/lib/format";

const NAV = [
  { to: "/trending", label: "Trending" },
  { to: "/map", label: "Map" },
  { to: "/bookmarks", label: "Saved" },
];

/**
 * App shell.
 *
 * The header carries a compact search box on every route except home (where the hero
 * already owns one), because the core loop starts with a dish query from anywhere.
 */
export function AppLayout() {
  const location = useLocation();
  const { isSignedIn, me, isModerator } = useAuth();
  const { scrollY } = useScroll();
  const borderOpacity = useTransform(scrollY, [0, 60], [0, 1]);
  const [menuOpen, setMenuOpen] = useState(false);

  const isHome = location.pathname === "/";

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <div className="min-h-screen bg-bg">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50
          focus:rounded-input focus:bg-accent focus:px-4 focus:py-2 focus:text-black"
      >
        Skip to content
      </a>

      <motion.header
        className="glass sticky top-0 z-40 border-b"
        style={{ borderColor: `rgb(var(--border) / ${borderOpacity.get()})` }}
      >
        <div className="mx-auto flex max-w-content items-center gap-4 px-4 py-3">
          <Link to="/" className="flex shrink-0 items-center gap-2" aria-label="Khaabo home">
            <span
              aria-hidden
              className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-accent"
            >
              ◍
            </span>
            <span className="font-display text-xl text-text">Khaabo</span>
          </Link>

          {!isHome && (
            <div className="hidden min-w-0 flex-1 md:block">
              <SearchBox size="md" />
            </div>
          )}

          <nav className="ml-auto hidden items-center gap-1 md:flex" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    "rounded-input px-3 py-2 text-sm transition-colors",
                    isActive ? "text-accent" : "text-muted hover:text-text",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}

            {isModerator && (
              <NavLink
                to="/admin"
                className={({ isActive }) =>
                  cn(
                    "rounded-input px-3 py-2 text-sm transition-colors",
                    isActive ? "text-accent" : "text-muted hover:text-text",
                  )
                }
              >
                Admin
              </NavLink>
            )}

            {isSignedIn && me?.profile ? (
              <Link to={`/u/${me.profile.username}`}>
                <Button size="sm" variant="ghost">
                  {me.profile.username}
                </Button>
              </Link>
            ) : (
              <Link to="/auth">
                <Button size="sm" variant="secondary">
                  Sign in
                </Button>
              </Link>
            )}
          </nav>

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-label="Menu"
            className="ml-auto grid h-11 w-11 place-items-center rounded-input text-muted md:hidden"
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>

        {!isHome && (
          <div className="px-4 pb-3 md:hidden">
            <SearchBox size="md" />
          </div>
        )}

        {menuOpen && (
          <nav
            className="border-t border-border px-4 py-3 md:hidden"
            aria-label="Mobile navigation"
          >
            <ul className="space-y-1">
              {NAV.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    className="block rounded-input px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-text"
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
              {isModerator && (
                <li>
                  <NavLink
                    to="/admin"
                    className="block rounded-input px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-text"
                  >
                    Admin
                  </NavLink>
                </li>
              )}
              <li>
                <NavLink
                  to={isSignedIn && me?.profile ? `/u/${me.profile.username}` : "/auth"}
                  className="block rounded-input px-3 py-2.5 text-muted hover:bg-surface-2 hover:text-text"
                >
                  {isSignedIn ? "My profile" : "Sign in"}
                </NavLink>
              </li>
            </ul>
          </nav>
        )}
      </motion.header>

      <main id="main">
        <Outlet />
      </main>

      <footer className="mt-16 border-t border-border">
        <div className="mx-auto max-w-content px-4 py-10">
          <div className="flex flex-wrap items-start justify-between gap-8">
            <div className="max-w-sm">
              <p className="font-display text-xl text-text">Khaabo</p>
              <p className="mt-2 text-sm text-muted">
                Dish-first food discovery. Rankings are computed from stored review evidence,
                and we say “not enough data” rather than guessing.
              </p>
            </div>

            <div className="text-sm">
              <p className="mb-2 text-xs uppercase tracking-wide text-subtle">Data</p>
              <ul className="space-y-1 text-muted">
                <li>
                  Places ©{" "}
                  <a
                    href="https://www.openstreetmap.org/copyright"
                    className="text-accent hover:underline"
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    OpenStreetMap
                  </a>{" "}
                  contributors
                </li>
                <li>Discussion from Reddit and YouTube public APIs</li>
                <li>Reviews from Khaabo users</li>
              </ul>
            </div>
          </div>

          <p className="mt-8 text-xs text-subtle">
            Launching in Kolkata. Built with open data and free tooling.
          </p>
        </div>
      </footer>
    </div>
  );
}
