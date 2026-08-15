/**
 * JSON-LD builders.
 *
 * Kept separate from the `Seo` component so the metadata helpers can be unit tested
 * and imported without pulling in React.
 */
const BASE_URL =
  (import.meta.env.VITE_PUBLIC_URL as string | undefined) ??
  (typeof window !== "undefined" ? window.location.origin : "https://khaabo.app");

/**
 * Structured data for a dish page.
 *
 * `aggregateRating` is emitted only for ranked restaurants with real mention counts —
 * publishing a rating for an unranked entry would be misleading and a
 * structured-data policy violation.
 */
export function dishJsonLd(input: {
  dishName: string;
  slug: string;
  cityName: string;
  restaurants: {
    name: string;
    id: string;
    score?: number | null;
    mentionCount: number;
    status: string;
  }[];
}): Record<string, unknown> {
  const ranked = input.restaurants.filter(
    (restaurant) => restaurant.status === "ranked" && restaurant.score != null,
  );

  return {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: `Best ${input.dishName} in ${input.cityName}`,
    description: `Restaurants ranked for ${input.dishName} in ${input.cityName}, based on review evidence.`,
    numberOfItems: ranked.length,
    itemListElement: ranked.slice(0, 10).map((restaurant, index) => ({
      "@type": "ListItem",
      position: index + 1,
      item: {
        "@type": "Restaurant",
        name: restaurant.name,
        url: `${BASE_URL}/restaurant/${restaurant.id}`,
        ...(restaurant.mentionCount >= 3 && restaurant.score != null
          ? {
              aggregateRating: {
                "@type": "AggregateRating",
                ratingValue: (restaurant.score / 20).toFixed(1),
                bestRating: "5",
                worstRating: "1",
                ratingCount: restaurant.mentionCount,
              },
            }
          : {}),
      },
    })),
  };
}

export function restaurantJsonLd(input: {
  name: string;
  id: string;
  address?: string | null;
  lat: number;
  lng: number;
  cuisines: string[];
  phone?: string | null;
  score?: number | null;
  reviewCount: number;
  status?: string;
}): Record<string, unknown> {
  return {
    "@context": "https://schema.org",
    "@type": "Restaurant",
    name: input.name,
    url: `${BASE_URL}/restaurant/${input.id}`,
    ...(input.address ? { address: input.address } : {}),
    ...(input.phone ? { telephone: input.phone } : {}),
    servesCuisine: input.cuisines,
    geo: {
      "@type": "GeoCoordinates",
      latitude: input.lat,
      longitude: input.lng,
    },
    ...(input.status === "ranked" && input.score != null && input.reviewCount >= 3
      ? {
          aggregateRating: {
            "@type": "AggregateRating",
            ratingValue: (input.score / 20).toFixed(1),
            bestRating: "5",
            worstRating: "1",
            ratingCount: input.reviewCount,
          },
        }
      : {}),
  };
}

export const SEO_BASE_URL = BASE_URL;
