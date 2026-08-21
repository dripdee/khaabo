/**
 * Typed endpoint modules.
 *
 * Grouped by resource, mirroring the API. Components never build paths themselves,
 * so a route change is a one-line edit here.
 */
import { api } from "@/services/client";
import type {
  Bookmark,
  City,
  CityMapPoint,
  Collection,
  DishDetail,
  DishMap,
  DishRestaurantsResponse,
  Me,
  Paginated,
  Profile,
  Review,
  RestaurantBrief,
  RestaurantDetail,
  RestaurantDish,
  SearchParams,
  SearchResponse,
  SuggestItem,
  TrendingResponse,
} from "@/types/api";

const DEFAULT_CITY = (import.meta.env.VITE_DEFAULT_CITY as string | undefined) ?? "kolkata";

export const citiesApi = {
  list: () => api.get<City[]>("/cities"),
};

export const searchApi = {
  search: (params: SearchParams, signal?: AbortSignal) =>
    api.get<SearchResponse>("/search", {
      params: { city: DEFAULT_CITY, ...params },
      signal,
      auth: true,
    }),

  suggest: (q: string, city = DEFAULT_CITY, signal?: AbortSignal) =>
    api.get<{ items: SuggestItem[] }>("/search/suggest", {
      params: { q, city },
      signal,
    }),
};

export interface DishRestaurantFilters {
  city?: string;
  lat?: number;
  lng?: number;
  radius_m?: number;
  min_price?: number;
  max_price?: number;
  area?: string;
  trend?: string;
  sort?: "score" | "distance" | "trending" | "price";
  page?: number;
  page_size?: number;
}

export const dishesApi = {
  detail: (slug: string, params?: { city?: string; lat?: number; lng?: number }) =>
    api.get<DishDetail>(`/dishes/${slug}`, {
      params: { city: DEFAULT_CITY, ...params },
    }),

  restaurants: (slug: string, filters: DishRestaurantFilters = {}) =>
    api.get<DishRestaurantsResponse>(`/dishes/${slug}/restaurants`, {
      params: { city: DEFAULT_CITY, ...filters },
      auth: true,
    }),

  map: (
    slug: string,
    filters: {
      city?: string;
      lat?: number;
      lng?: number;
      radius_m?: number;
      max_price?: number;
      trend?: string;
    } = {},
  ) =>
    api.get<DishMap>(`/dishes/${slug}/map`, {
      params: { city: DEFAULT_CITY, ...filters },
    }),

  summary: (slug: string, city = DEFAULT_CITY) =>
    api.get<{ status: string; text?: string; message?: string }>(`/dishes/${slug}/summary`, {
      params: { city },
    }),
};

export const restaurantsApi = {
  list: (params: { city?: string; cuisine?: string; area?: string; q?: string; page?: number } = {}) =>
    api.get<Paginated<RestaurantBrief> & { city_slug: string }>("/restaurants", {
      params: { city: DEFAULT_CITY, ...params },
    }),

  detail: (id: string) => api.get<RestaurantDetail>(`/restaurants/${id}`),

  locations: (city = DEFAULT_CITY) =>
    api.get<{ city_slug: string; items: CityMapPoint[]; attribution: string[] }>(
      "/restaurants/locations",
      { params: { city } },
    ),

  foodDna: (id: string) => api.get<RestaurantDetail["food_dna"]>(`/restaurants/${id}/food-dna`),

  dishes: (id: string, page = 1) =>
    api.get<Paginated<RestaurantDish> & { restaurant_id: string }>(
      `/restaurants/${id}/dishes`,
      { params: { page } },
    ),

  reviews: (id: string, page = 1) =>
    api.get<Paginated<Review> & { restaurant_id: string }>(`/restaurants/${id}/reviews`, {
      params: { page },
      auth: true,
    }),
};

export const trendingApi = {
  get: (params: { city?: string; direction?: "rising" | "declining"; limit?: number } = {}) =>
    api.get<TrendingResponse>("/trending", {
      params: { city: DEFAULT_CITY, direction: "rising", ...params },
    }),
};

export interface ReviewPayload {
  restaurant_id: string;
  body: string;
  title?: string;
  rating?: number;
  dish_hints?: string[];
}

export const reviewsApi = {
  create: (payload: ReviewPayload) =>
    api.post<{
      id: string;
      status: string;
      ai_state: string;
      moderation: Record<string, unknown>;
      message: string;
    }>("/reviews", payload),

  get: (id: string) => api.get<Review>(`/reviews/${id}`, { auth: true }),

  remove: (id: string) => api.delete<{ status: string }>(`/reviews/${id}`),

  report: (id: string, reason: string, note?: string) =>
    api.post<{ status: string }>(`/reviews/${id}/report`, { reason, note }),

  toggleLike: (reviewId: string) =>
    api.post<{ review_id: string; liked: boolean; like_count: number }>("/likes", {
      review_id: reviewId,
    }),
};

export const bookmarksApi = {
  list: (params: { collection_id?: string; target_type?: string; page?: number } = {}) =>
    api.get<Paginated<Bookmark>>("/bookmarks", { params, auth: true }),

  create: (payload: {
    target_type: "dish" | "restaurant" | "dish_restaurant";
    dish_id?: string;
    restaurant_id?: string;
    collection_id?: string;
    note?: string;
  }) => api.post<Bookmark>("/bookmarks", payload),

  remove: (id: string) => api.delete<{ status: string }>(`/bookmarks/${id}`),

  collections: () => api.get<{ items: Collection[] }>("/collections", { auth: true }),

  createCollection: (payload: { name: string; description?: string; is_public?: boolean }) =>
    api.post<Collection>("/collections", payload),
};

export const usersApi = {
  me: () => api.get<Me>("/users/me", { auth: true }),

  updateMe: (payload: Partial<{
    username: string;
    display_name: string;
    bio: string;
    avatar_url: string;
    city_slug: string;
    favourite_dish_ids: string[];
    favourite_restaurant_ids: string[];
  }>) => api.patch<Me>("/users/me", payload),

  profile: (username: string) => api.get<Profile>(`/users/${username}`),
};

export const moderationApi = {
  queue: (status = "open", page = 1) =>
    api.get<
      Paginated<{
        id: string;
        review_id: string;
        reason: string;
        status: string;
        severity: number;
        review_body?: string;
        review_status?: string;
        spam_score?: number;
        is_duplicate: boolean;
        created_at: string;
        history: Record<string, unknown>[];
      }>
    >("/moderation/queue", { params: { status, page }, auth: true }),

  decide: (itemId: string, action: "publish" | "reject" | "flag" | "dismiss", note?: string) =>
    api.post<{ review_id: string; status: string }>(`/moderation/${itemId}/decide`, {
      action,
      note,
    }),
};

export const adminApi = {
  ranking: () =>
    api.get<{
      weights: Record<string, number>;
      weights_version: string;
      halflife_days: number;
      bayes_m: number;
      min_mentions: number;
      ranked_pairs: number;
      insufficient_pairs: number;
      reviews_pending_ai: number;
    }>("/admin/ranking", { auth: true }),

  restaurants: (params: { q?: string; unverified_only?: boolean; page?: number } = {}) =>
    api.get<Paginated<Record<string, unknown>>>("/admin/restaurants", { params, auth: true }),

  entityConflicts: (page = 1) =>
    api.get<Paginated<Record<string, unknown>>>("/admin/entity-conflicts", {
      params: { page },
      auth: true,
    }),

  failedJobs: () =>
    api.get<{ ingestion: Record<string, unknown>[]; ai: Record<string, unknown>[] }>(
      "/admin/jobs/failed",
      { auth: true },
    ),

  aiOutputs: (page = 1) =>
    api.get<Paginated<Record<string, unknown>>>("/admin/ai-outputs", {
      params: { page },
      auth: true,
    }),

  recompute: (scope: "stale" | "all" = "stale") =>
    api.post<{ status: string; task_id: string }>("/admin/ranking/recompute", { scope }),
};

export const DEFAULT_CITY_SLUG = DEFAULT_CITY;
