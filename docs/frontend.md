# Khaabo — Frontend

React 18 + Vite + TypeScript, Tailwind CSS, Framer Motion, TanStack Query, React Router,
React Hook Form + Zod, Leaflet.

Design intent: **dark-first, indie/editorial, premium-minimal**. Large type, rounded
cards, soft gradients, restrained translucency (a thin frosted layer on overlays and the
sticky header only — not on every card).

---

## 1. Structure

```text
src/
  components/          design system (Button, Card, Badge, Score, Trend, Skeleton, Modal, Drawer…)
  features/
    search/            SearchBox, Filters, ResultList, useSearch
    dishes/            DishCard, DishHero, DishRestaurantList, WhyChips, AttributeBars
    restaurants/       RestaurantCard, FoodDna, DishBreakdown, ReviewList
    map/               DishMap, MarkerLayer, ClusterLayer, RestaurantPreview
    reviews/           ReviewForm, ReviewCard, LikeButton
    profiles/          ProfileHeader, ContributionStats
    bookmarks/         BookmarkButton, CollectionPicker
    gamification/      BadgeGrid, BadgeChip
  pages/               route components
  hooks/               useDebounce, useGeolocation, usePrefersReducedMotion, useAuth
  services/            api client + typed endpoint modules
  lib/                 cn, format, slug, seo, queryClient, supabase
  types/               shared API types
```

Feature folders own their queries; `services/` owns transport only.

## 2. Routes

| Path | Page | Priority |
|---|---|---|
| `/` | Home / Search | **1** |
| `/search` | Search results | 2 |
| `/dish/:slug` | Dish page | **1** |
| `/dish/:slug/map` | Dish map results | **1** |
| `/restaurant/:id` | Restaurant page | 2 |
| `/map` | City map | 3 |
| `/trending` | Trending | 3 |
| `/u/:username` | Profile | 3 |
| `/bookmarks` | Bookmarks | 3 |
| `/review/new` | Review submission | 3 |
| `/auth` | Sign in / up | 3 |
| `/admin` | Admin dashboard | 4 |

The three priority-1 screens define the product: search a dish → see ranked restaurants
with evidence → see them on a map filtered by that dish.

## 3. Design tokens

```text
bg          #0A0A0B   surface #111113   surface-2 #17171A   border #232327
text        #F5F5F4   muted   #A1A1A6   subtle  #6B6B72
accent      #FF6B35 (saffron)  accent-2 #FFB627 (turmeric)
positive    #34D399   warning #FBBF24   negative #F87171
radius      card 20px  chip 999px  input 14px
font        display: "Instrument Serif"/serif fallback · body: Inter/system
shadow      0 1px 2px rgb(0 0 0 / .4), 0 12px 32px -12px rgb(0 0 0 / .6)
```

Gradients are used once per screen at most (hero glow, score ring). Light theme is
supported via `class="dark"` toggling with the same token names.

## 4. Component contracts (selected)

- `<Score value={88.4} size="lg" />` — ring + number; renders `—` and a "Not enough data"
  tooltip when `value` is null.
- `<Trend direction="rising" delta={0.11} />` — ↑ / → / ↓ with colour; renders **nothing**
  when `direction` is null.
- `<WhyChips why={[{code,label,value}]} />` — joins server-provided labels with `·`. The
  frontend never composes its own explanation.
- `<DishCard>` — name, score, trend, price range, mention count, top-restaurant teaser.
- `<RestaurantCard>` — dish-specific score, badges (Best value / Hidden gem / Consistent),
  distance, price, why chips, 1 verbatim snippet with source attribution.
- `<Skeleton>` — every async surface has a skeleton; no layout shift.

## 5. Motion

`framer-motion` with a single shared easing (`[0.22, 1, 0.36, 1]`) and 3 durations
(120/220/380 ms). Patterns: staggered list entry (30 ms), shared-layout score ring,
drawer slide-up on mobile, marker pop on select, subtle hover lift (2px).

`usePrefersReducedMotion()` short-circuits every variant to opacity-only — verified by
setting `prefers-reduced-motion: reduce`.

## 6. Data layer

TanStack Query with `staleTime` matching server cache (dish 5 min, search 2 min,
trending 15 min), `keepPreviousData` for filter changes, infinite queries for lists.
Likes and bookmarks use optimistic mutations with rollback on error.

Zod schemas mirror API responses and parse at the boundary, so a backend contract change
fails loudly in one place instead of rendering `undefined`.

## 7. Map

Leaflet with OSM tiles (attribution always visible), `markercluster` for density,
custom `divIcon` markers coloured by score band and shaped by badge. Selecting a marker
opens a bottom sheet (mobile) / side panel (desktop) with the dish-specific card.
Markers come from `/dishes/{slug}/map`, so changing the selected dish swaps the entire
marker set — the map is dish-scoped by construction.

## 8. Mobile UX

Bottom sheet filters, thumb-reachable search bar, 44px minimum touch targets, sticky
"see on map" CTA on dish pages, list↔map toggle instead of a cramped split view.

## 9. SEO

`react-helmet-async` for per-route title/description/OG/canonical, `JSON-LD` for dish
pages (`ItemList` of `Restaurant`) and restaurant pages (`Restaurant` + `aggregateRating`
only when `status="ranked"`). Slugs are server-owned. Route-level data loaders are kept
pure so a future SSR/SSG adapter can reuse them unchanged.

## 10. Accessibility

Semantic landmarks, focus-visible rings, keyboard-navigable filters and map markers,
`aria-live` on search result counts, contrast ≥ 4.5:1 for text on `#0A0A0B`, and
`Score`/`Trend` expose text alternatives rather than colour-only meaning.
