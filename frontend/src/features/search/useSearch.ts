import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { STALE, queryKeys } from "@/lib/queryClient";
import { searchApi } from "@/services/endpoints";
import type { SearchParams } from "@/types/api";

/** Suggest is only fetched when the dropdown is open and the term is meaningful. */
export function useSuggest(query: string, enabled: boolean) {
  const trimmed = query.trim();

  const result = useQuery({
    queryKey: queryKeys.suggest(trimmed),
    queryFn: ({ signal }) => searchApi.suggest(trimmed, undefined, signal),
    enabled: enabled && trimmed.length >= 2,
    staleTime: 60 * 1000,
  });

  return { ...result, data: result.data?.items };
}

export function useSearch(params: SearchParams) {
  const enabled = Boolean(
    params.q || params.dish || params.cuisine || params.area || params.mood,
  );

  return useQuery({
    queryKey: queryKeys.search(params as Record<string, unknown>),
    queryFn: ({ signal }) => searchApi.search(params, signal),
    enabled,
    staleTime: STALE.search,
    // Filter changes keep the previous page visible instead of flashing skeletons,
    // which makes refinement feel continuous.
    placeholderData: keepPreviousData,
  });
}

const NUMERIC_KEYS = ["lat", "lng", "radius_m", "min_price", "max_price", "page", "page_size"];

/**
 * URL is the source of truth for search state.
 *
 * Keeping filters in the query string makes results shareable and the back button
 * behave correctly, which local component state would not.
 */
export function useSearchFilters() {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo<SearchParams>(() => {
    const result: Record<string, unknown> = {};
    searchParams.forEach((value, key) => {
      if (!value) return;
      result[key] = NUMERIC_KEYS.includes(key) ? Number(value) : value;
    });
    return result as SearchParams;
  }, [searchParams]);

  function setFilters(next: Partial<SearchParams>, options?: { replace?: boolean }) {
    const params = new URLSearchParams(searchParams);

    for (const [key, value] of Object.entries(next)) {
      if (value === undefined || value === null || value === "") {
        params.delete(key);
      } else {
        params.set(key, String(value));
      }
    }

    // Any filter change invalidates the current page number.
    if (!("page" in next)) params.delete("page");

    setSearchParams(params, { replace: options?.replace ?? false });
  }

  function clearFilters(keep: (keyof SearchParams)[] = ["q"]) {
    const params = new URLSearchParams();
    keep.forEach((key) => {
      const value = searchParams.get(String(key));
      if (value) params.set(String(key), value);
    });
    setSearchParams(params);
  }

  const activeFilterCount = useMemo(() => {
    let count = 0;
    for (const key of ["max_price", "min_price", "area", "cuisine", "dietary", "mood", "trend", "radius_m"]) {
      if (searchParams.get(key)) count += 1;
    }
    return count;
  }, [searchParams]);

  return { filters, setFilters, clearFilters, activeFilterCount };
}
