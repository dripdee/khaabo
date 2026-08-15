import { useQuery } from "@tanstack/react-query";

import { STALE, queryKeys } from "@/lib/queryClient";
import { trendingApi } from "@/services/endpoints";

export function useTrending(params: {
  city?: string;
  direction?: "rising" | "declining";
  limit?: number;
} = {}) {
  return useQuery({
    queryKey: queryKeys.trending(params as Record<string, unknown>),
    queryFn: () => trendingApi.get(params),
    staleTime: STALE.trending,
  });
}
