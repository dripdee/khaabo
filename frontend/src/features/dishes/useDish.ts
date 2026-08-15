import { useQuery } from "@tanstack/react-query";

import { STALE, queryKeys } from "@/lib/queryClient";
import { dishesApi } from "@/services/endpoints";
import type { DishRestaurantFilters } from "@/services/endpoints";

export function useDish(
  slug: string | undefined,
  params?: { city?: string; lat?: number; lng?: number },
) {
  return useQuery({
    queryKey: queryKeys.dish(slug ?? "", params as Record<string, unknown>),
    queryFn: () => dishesApi.detail(slug as string, params),
    enabled: Boolean(slug),
    staleTime: STALE.dish,
  });
}

export function useDishRestaurants(slug: string | undefined, filters: DishRestaurantFilters) {
  return useQuery({
    queryKey: queryKeys.dishRestaurants(slug ?? "", filters as Record<string, unknown>),
    queryFn: () => dishesApi.restaurants(slug as string, filters),
    enabled: Boolean(slug),
    staleTime: STALE.dish,
  });
}

export function useDishMap(
  slug: string | undefined,
  filters: {
    city?: string;
    lat?: number;
    lng?: number;
    radius_m?: number;
    max_price?: number;
    trend?: string;
  },
  enabled = true,
) {
  return useQuery({
    queryKey: queryKeys.dishMap(slug ?? "", filters as Record<string, unknown>),
    queryFn: () => dishesApi.map(slug as string, filters),
    // The map payload is separate from the list so the list view never downloads
    // marker data it will not render.
    enabled: enabled && Boolean(slug),
    staleTime: STALE.dish,
  });
}
