/**
 * API response types.
 *
 * These mirror the backend Pydantic schemas. Zod schemas in `lib/schemas.ts` parse
 * responses at the boundary so a backend contract change fails loudly in one place
 * instead of rendering `undefined` deep inside a component.
 */

export type ScoreStatus = "ranked" | "insufficient_data";
export type TrendDirection = "rising" | "stable" | "declining";
export type SourceType = "osm" | "reddit" | "youtube" | "google" | "user" | "manual";
export type BadgeCode = "best_value" | "hidden_gem" | "most_consistent";

export interface WhyReason {
  code: string;
  label: string;
  value?: number | null;
}

export interface Trend {
  direction: TrendDirection | null;
  delta?: number | null;
  significant?: boolean;
}

export interface PriceRange {
  min?: number | null;
  max?: number | null;
  avg?: number | null;
  currency?: string;
}

export interface AttributeCount {
  label: string;
  count: number;
}

export interface City {
  id: string;
  slug: string;
  name: string;
  country: string;
  lat: number;
  lng: number;
  timezone: string;
}

export interface DishBrief {
  id: string;
  slug: string;
  name: string;
  cuisine?: string | null;
  category: string;
  is_veg?: boolean | null;
  hero_image_url?: string | null;
}

export interface DishCard extends DishBrief {
  score?: number | null;
  status: ScoreStatus;
  trend?: Trend | null;
  mention_count: number;
  restaurant_count: number;
  price_range?: PriceRange | null;
  top_restaurant_name?: string | null;
}

export interface Snippet {
  text: string;
  sentiment: number;
  source: SourceType;
  published_at?: string | null;
  review_id: string;
}

export interface RestaurantBrief {
  id: string;
  name: string;
  slug: string;
  area?: string | null;
  lat: number;
  lng: number;
  cuisines: string[];
  price_level?: number | null;
  google_rating?: number | null;
  google_rating_count?: number | null;
}

/** One dot on the city-wide map — kept light for ~14k rows. */
export interface CityMapPoint {
  id: string;
  name: string;
  slug: string;
  lat: number;
  lng: number;
  google_rating?: number | null;
  google_rating_count?: number | null;
}

/** A restaurant ranked for one specific dish — the core unit of the product. */
export interface DishRestaurant extends RestaurantBrief {
  score?: number | null;
  status: ScoreStatus;
  positive_ratio: number;
  mention_count: number;
  consistency: number;
  price_avg?: number | null;
  value_score?: number | null;
  trend?: Trend | null;
  badges: BadgeCode[];
  why: WhyReason[];
  top_attributes: string[];
  snippets: Snippet[];
  distance_m?: number | null;
}

export interface DishHighlights {
  top?: DishRestaurant | null;
  best_value?: DishRestaurant | null;
  hidden_gem?: DishRestaurant | null;
  most_consistent?: DishRestaurant | null;
}

export interface DishSummary {
  text: string;
  generated_by: "template" | "model";
  evidence_review_ids: string[];
  mention_count: number;
  positive_ratio: number;
}

export interface RecentSignal {
  period: string;
  positive_ratio: number;
  mentions: number;
}

export interface DishDetail {
  dish: DishBrief;
  city_slug: string;
  score?: number | null;
  status: ScoreStatus;
  trend?: Trend | null;
  mention_count: number;
  restaurant_count: number;
  price_range?: PriceRange | null;
  positive_attributes: AttributeCount[];
  negative_attributes: AttributeCount[];
  summary?: DishSummary | null;
  highlights: DishHighlights;
  recent_signals: RecentSignal[];
  attribution: string[];
}

export interface DishRestaurantsResponse {
  dish_slug: string;
  city_slug: string;
  items: DishRestaurant[];
  /** Rows without enough evidence, kept out of the ranking on purpose. */
  insufficient: DishRestaurant[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
  attribution: string[];
}

export interface MapMarker {
  id: string;
  name: string;
  lat: number;
  lng: number;
  score?: number | null;
  status: ScoreStatus;
  price_avg?: number | null;
  trend?: TrendDirection | null;
  badges: BadgeCode[];
}

export interface DishMap {
  dish: DishBrief;
  city_slug: string;
  markers: MapMarker[];
  bounds?: {
    south: number;
    west: number;
    north: number;
    east: number;
  } | null;
  attribution: string[];
}

export interface DnaChip {
  code: string;
  label: string;
  emoji: string;
  group: string;
  value?: number | null;
}

export interface FoodDna {
  restaurant_id: string;
  chips: DnaChip[];
  overall_score?: number | null;
  sentiment: number;
  consistency: number;
  value_score?: number | null;
  trend?: Trend | null;
  evidence_count: number;
  status: ScoreStatus;
}

export interface RestaurantDish {
  dish: DishBrief;
  score?: number | null;
  status: ScoreStatus;
  mention_count: number;
  positive_ratio: number;
  price_avg?: number | null;
  is_signature: boolean;
  trend?: Trend | null;
  why: WhyReason[];
}

export interface RestaurantDetail extends RestaurantBrief {
  address?: string | null;
  phone?: string | null;
  website?: string | null;
  opening_hours?: string | null;
  is_closed: boolean;
  is_verified: boolean;
  review_count: number;
  city_slug: string;
  food_dna?: FoodDna | null;
  top_dishes: RestaurantDish[];
  attribution: string[];
}

export interface ReviewAuthor {
  username?: string | null;
  display_name?: string | null;
  avatar_url?: string | null;
}

export interface ReviewDishMention {
  dish_slug: string;
  dish_name: string;
  sentiment: number;
  snippet?: string | null;
  attributes: string[];
  price_mentioned?: number | null;
}

export interface Review {
  id: string;
  restaurant_id: string;
  restaurant_name?: string | null;
  source: SourceType;
  title?: string | null;
  body: string;
  rating?: number | null;
  lang: string;
  overall_sentiment?: number | null;
  like_count: number;
  liked_by_me: boolean;
  status: string;
  published_at: string;
  author?: ReviewAuthor | null;
  dish_mentions: ReviewDishMention[];
  source_url?: string | null;
  attribution?: string | null;
}

export interface ParsedQuery {
  raw: string;
  dish_terms: string[];
  cuisine?: string | null;
  area?: string | null;
  min_price?: number | null;
  max_price?: number | null;
  dietary?: string | null;
  mood?: string | null;
  near_me: boolean;
  superlative: boolean;
  price_band?: string | null;
  intent: string;
}

export interface SearchResponse {
  intent: string;
  parsed?: ParsedQuery | null;
  dishes: DishCard[];
  restaurants: DishRestaurant[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
  city_slug?: string | null;
  attribution: string[];
}

export interface SuggestItem {
  kind: "dish" | "restaurant" | "area" | "cuisine";
  label: string;
  slug?: string | null;
  id?: string | null;
  subtitle?: string | null;
}

export interface TrendingItem {
  kind: "dish" | "restaurant";
  dish?: DishBrief | null;
  restaurant?: RestaurantBrief | null;
  direction: TrendDirection;
  delta?: number | null;
  recent_count: number;
  significant: boolean;
  score?: number | null;
}

export interface TrendingResponse {
  city_slug: string;
  direction: string;
  dishes: TrendingItem[];
  restaurants: TrendingItem[];
}

export interface Badge {
  code: string;
  label: string;
  description: string;
  emoji: string;
  level: number;
  progress: number;
  target?: number | null;
  awarded_at?: string | null;
}

export interface Profile {
  username: string;
  display_name?: string | null;
  avatar_url?: string | null;
  bio?: string | null;
  city_slug?: string | null;
  review_count: number;
  published_review_count: number;
  like_received_count: number;
  contribution_score: number;
  badges: Badge[];
  favourite_dishes: { id: string; name: string; slug: string }[];
  favourite_restaurants: { id: string; name: string; slug: string }[];
  created_at?: string | null;
}

export interface Me {
  id: string;
  email?: string | null;
  role: "user" | "moderator" | "admin";
  profile?: Profile | null;
}

export interface Bookmark {
  id: string;
  target_type: "dish" | "restaurant" | "dish_restaurant";
  dish_id?: string | null;
  restaurant_id?: string | null;
  dish_name?: string | null;
  dish_slug?: string | null;
  restaurant_name?: string | null;
  collection_id?: string | null;
  note?: string | null;
  created_at: string;
}

export interface Collection {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  is_public: boolean;
  bookmark_count: number;
}

export interface Paginated<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

export interface SearchParams {
  q?: string;
  city?: string;
  dish?: string;
  cuisine?: string;
  area?: string;
  lat?: number;
  lng?: number;
  radius_m?: number;
  min_price?: number;
  max_price?: number;
  dietary?: string;
  mood?: string;
  trend?: string;
  sort?: "score" | "distance" | "trending" | "price" | "relevance";
  page?: number;
  page_size?: number;
}
