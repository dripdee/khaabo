import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/services/client";

/**
 * Query client defaults.
 *
 * `staleTime` mirrors the server's cache TTLs (see docs/architecture.md §4) so the
 * client is not more eager than the data can actually change. Retries deliberately
 * skip non-retryable API errors — retrying a 404 or a validation failure only delays
 * the error the user needs to see.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2 * 60 * 1000,
      gcTime: 10 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && !error.isRetryable) return false;
        return failureCount < 2;
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    },
    mutations: {
      retry: false,
    },
  },
});

/** Server cache TTLs, in milliseconds, for per-query overrides. */
export const STALE = {
  search: 2 * 60 * 1000,
  dish: 5 * 60 * 1000,
  restaurant: 5 * 60 * 1000,
  trending: 15 * 60 * 1000,
  cities: 60 * 60 * 1000,
  me: 5 * 60 * 1000,
} as const;

export const queryKeys = {
  cities: ["cities"] as const,
  search: (params: Record<string, unknown>) => ["search", params] as const,
  suggest: (query: string) => ["suggest", query] as const,
  dish: (slug: string, params?: Record<string, unknown>) => ["dish", slug, params ?? {}] as const,
  dishRestaurants: (slug: string, filters: Record<string, unknown>) =>
    ["dish", slug, "restaurants", filters] as const,
  dishMap: (slug: string, filters: Record<string, unknown>) =>
    ["dish", slug, "map", filters] as const,
  restaurant: (id: string) => ["restaurant", id] as const,
  restaurantLocations: (city: string) => ["restaurants", "locations", city] as const,
  restaurantDishes: (id: string, page: number) => ["restaurant", id, "dishes", page] as const,
  restaurantReviews: (id: string, page: number) => ["restaurant", id, "reviews", page] as const,
  trending: (params: Record<string, unknown>) => ["trending", params] as const,
  me: ["me"] as const,
  profile: (username: string) => ["profile", username] as const,
  bookmarks: (params: Record<string, unknown>) => ["bookmarks", params] as const,
  collections: ["collections"] as const,
  moderationQueue: (status: string, page: number) => ["moderation", status, page] as const,
  adminRanking: ["admin", "ranking"] as const,
} as const;
