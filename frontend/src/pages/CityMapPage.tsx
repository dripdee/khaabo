import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "leaflet.markercluster";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/Button";
import { MapSkeleton } from "@/components/Skeleton";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "@/features/map/DishMapView";
import { STALE, queryKeys } from "@/lib/queryClient";
import { formatCount } from "@/lib/format";
import { DEFAULT_CITY_SLUG, restaurantsApi } from "@/services/endpoints";
import type { CityMapPoint } from "@/types/api";

/** Gold marks places carrying a Google rating; grey marks the rest. */
const RATED_COLOR = "#f5b301";
const UNRATED_COLOR = "#9a9aa2";

function escapeHtml(value: string): string {
  return value
    .split("&")
    .join("&amp;")
    .split("<")
    .join("&lt;")
    .split(">")
    .join("&gt;")
    .split('"')
    .join("&quot;");
}

function directionsUrl(point: CityMapPoint): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${point.lat},${point.lng}`;
}

/**
 * City-wide map — every restaurant in the catalog, drawn as lightweight circle
 * markers (no divIcons: ~14k rows must stay cheap). Raw Leaflet + markercluster,
 * mirroring DishMapView's imperative setup. Directions hand off to Google Maps
 * via the official deep-link scheme; the map itself stays on free OSM tiles.
 */
export default function CityMapPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.restaurantLocations(DEFAULT_CITY_SLUG),
    queryFn: () => restaurantsApi.locations(DEFAULT_CITY_SLUG),
    staleTime: STALE.restaurant,
  });

  const points = useMemo(() => data?.items ?? [], [data]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [22.5726, 88.3639],
      zoom: 12,
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: false,
    });

    L.tileLayer(OSM_TILE_URL, {
      attribution: OSM_ATTRIBUTION,
      maxZoom: 19,
      detectRetina: true,
    }).addTo(map);

    map.on("click", () => map.scrollWheelZoom.enable());
    map.on("mouseout", () => map.scrollWheelZoom.disable());

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const cluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      maxClusterRadius: 48,
    });

    points.forEach((point) => {
      const rated = point.google_rating != null;
      const ratingLine = rated
        ? `★ ${point.google_rating!.toFixed(1)}${
            point.google_rating_count != null
              ? ` (${point.google_rating_count.toLocaleString("en-IN")})`
              : ""
          }`
        : "Not rated yet";

      const marker = L.circleMarker([point.lat, point.lng], {
        radius: 7,
        fillColor: rated ? RATED_COLOR : UNRATED_COLOR,
        fillOpacity: 0.9,
        color: "#ffffff",
        weight: 1.5,
      });

      marker.bindPopup(
        `<div style="min-width:170px">
          <p style="margin:0 0 4px;font-weight:600">${escapeHtml(point.name)}</p>
          <p style="margin:0 0 10px">${ratingLine}</p>
          <p style="margin:0;display:flex;gap:10px">
            <a href="/restaurant/${point.id}">View place</a>
            <a href="${directionsUrl(point)}" target="_blank" rel="noreferrer noopener">Directions</a>
          </p>
        </div>`,
      );

      cluster.addLayer(marker);
    });

    map.addLayer(cluster);

    if (points.length > 0) {
      const group = L.featureGroup(cluster.getLayers());
      map.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 15 });
    }

    return () => {
      map.removeLayer(cluster);
    };
  }, [points]);

  const ratedCount = points.filter((point) => point.google_rating != null).length;

  return (
    <div className="mx-auto max-w-content px-4 py-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-title text-text">Kolkata on the map</h1>
          <p aria-live="polite" className="mt-1 text-sm text-subtle">
            {isLoading
              ? "Loading places..."
              : `${formatCount(points.length)} places · ${formatCount(ratedCount)} with a Google rating`}
          </p>
        </div>
        <Link to="/">
          <Button variant="secondary" size="sm">
            Back to dishes
          </Button>
        </Link>
      </header>

      <div className="relative mt-5 h-[65vh] min-h-[380px] overflow-hidden rounded-card border border-border lg:h-[calc(100vh-220px)]">
        {isLoading ? (
          <MapSkeleton />
        ) : (
          <div
            ref={containerRef}
            className="h-full w-full"
            role="application"
            aria-label="Map of all restaurants in the city"
          />
        )}

        <div className="glass pointer-events-none absolute bottom-3 left-3 rounded-card border border-border px-3 py-2 text-[11px] text-muted">
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: RATED_COLOR }}
                aria-hidden
              />
              Google rating
            </span>
            <span className="flex items-center gap-1">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: UNRATED_COLOR }}
                aria-hidden
              />
              Not rated yet
            </span>
          </div>
        </div>
      </div>

      <p className="mt-4 text-xs text-subtle">
        {(data?.attribution ?? ["© OpenStreetMap contributors", "Ratings: Google"]).join(" · ")}
      </p>
    </div>
  );
}
