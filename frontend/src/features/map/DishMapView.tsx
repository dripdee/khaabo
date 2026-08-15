import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "leaflet.markercluster";
import { useEffect, useMemo, useRef } from "react";

import { SCORE_BAND_COLORS, scoreBand } from "@/lib/format";
import type { MapMarker } from "@/types/api";

/** OSM attribution is a licence requirement and is always visible on the map. */
export const OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
export const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export interface DishMapViewProps {
  markers: MapMarker[];
  bounds?: { south: number; west: number; north: number; east: number } | null;
  center?: { lat: number; lng: number };
  selectedId?: string | null;
  userCoords?: { lat: number; lng: number } | null;
  onSelect?: (marker: MapMarker) => void;
  className?: string;
}

function markerIcon(marker: MapMarker, selected: boolean): L.DivIcon {
  const band = scoreBand(marker.score);
  const color = SCORE_BAND_COLORS[band];
  const size = selected ? 44 : 36;
  const hasScore = marker.score != null;

  // Badge shape carries meaning beyond colour, so the map stays readable for
  // colour-blind users and in greyscale.
  const badgeGlyph = marker.badges.includes("best_value")
    ? "₹"
    : marker.badges.includes("hidden_gem")
      ? "◆"
      : marker.badges.includes("most_consistent")
        ? "◎"
        : "";

  const trendGlyph =
    marker.trend === "rising" ? "↑" : marker.trend === "declining" ? "↓" : "";

  return L.divIcon({
    className: "khaabo-marker-wrapper",
    html: `
      <div class="khaabo-marker" style="
        width:${size}px;height:${size}px;
        background:rgb(var(--surface));
        border-color:${color};
        color:${color};
        font-size:${selected ? 13 : 12}px;
        box-shadow:${selected ? `0 0 0 4px ${color}33` : "0 2px 8px rgb(0 0 0 / .5)"};
      ">
        <span>${hasScore ? Math.round(marker.score as number) : "–"}</span>
        ${badgeGlyph ? `<span style="position:absolute;top:-6px;right:-6px;font-size:10px;background:rgb(var(--surface));border:1px solid ${color};border-radius:999px;width:16px;height:16px;display:grid;place-items:center;">${badgeGlyph}</span>` : ""}
        ${trendGlyph ? `<span style="position:absolute;bottom:-6px;right:-6px;font-size:10px;">${trendGlyph}</span>` : ""}
      </div>
    `,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/**
 * Leaflet map, imperatively managed.
 *
 * react-leaflet is a dependency for typing/consistency, but markercluster needs the
 * raw layer API, and driving Leaflet directly avoids re-creating hundreds of markers
 * on every React render.
 */
export function DishMapView({
  markers,
  bounds,
  center,
  selectedId,
  userCoords,
  onSelect,
  className,
}: DishMapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const clusterRef = useRef<L.MarkerClusterGroup | null>(null);
  const markerRefs = useRef<Map<string, L.Marker>>(new Map());
  const userMarkerRef = useRef<L.Marker | null>(null);

  const fallbackCenter = useMemo(
    () => center ?? { lat: 22.5726, lng: 88.3639 },
    [center],
  );

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    // Captured for cleanup: `markerRefs.current` may point at a different Map by the
    // time the effect tears down.
    const markers = markerRefs.current;

    const map = L.map(containerRef.current, {
      center: [fallbackCenter.lat, fallbackCenter.lng],
      zoom: 12,
      zoomControl: true,
      attributionControl: true,
      // Scroll-wheel zoom is off until the map is clicked, so scrolling the page
      // does not get hijacked when it passes over the map.
      scrollWheelZoom: false,
    });

    L.tileLayer(OSM_TILE_URL, {
      attribution: OSM_ATTRIBUTION,
      maxZoom: 19,
      detectRetina: true,
    }).addTo(map);

    map.on("click", () => map.scrollWheelZoom.enable());
    map.on("mouseout", () => map.scrollWheelZoom.disable());

    const cluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      maxClusterRadius: 48,
      iconCreateFunction: (clusterLayer) => {
        const count = clusterLayer.getChildCount();
        const size = count < 10 ? 36 : count < 50 ? 44 : 52;
        return L.divIcon({
          html: `<div class="khaabo-cluster" style="width:${size}px;height:${size}px;">${count}</div>`,
          className: "khaabo-cluster-wrapper",
          iconSize: [size, size],
        });
      },
    });

    map.addLayer(cluster);
    mapRef.current = map;
    clusterRef.current = cluster;

    return () => {
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
      markers.clear();
    };
  }, [fallbackCenter]);

  // Rebuild the marker layer whenever the dish (and therefore the marker set)
  // changes. This is what makes the map dish-scoped by construction.
  useEffect(() => {
    const cluster = clusterRef.current;
    const map = mapRef.current;
    if (!cluster || !map) return;

    cluster.clearLayers();
    markerRefs.current.clear();

    markers.forEach((marker) => {
      const layer = L.marker([marker.lat, marker.lng], {
        icon: markerIcon(marker, marker.id === selectedId),
        title: marker.name,
        alt: marker.name,
        keyboard: true,
      });

      layer.on("click", () => onSelect?.(marker));
      layer.on("keypress", () => onSelect?.(marker));
      cluster.addLayer(layer);
      markerRefs.current.set(marker.id, layer);
    });

    if (bounds) {
      map.fitBounds(
        [
          [bounds.south, bounds.west],
          [bounds.north, bounds.east],
        ],
        { padding: [40, 40], maxZoom: 15 },
      );
    } else if (markers.length > 0) {
      const group = L.featureGroup(Array.from(markerRefs.current.values()));
      map.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 15 });
    }
  }, [markers, bounds, selectedId, onSelect]);

  // Selection only restyles the two affected markers rather than rebuilding the layer.
  useEffect(() => {
    markerRefs.current.forEach((layer, id) => {
      const marker = markers.find((candidate) => candidate.id === id);
      if (marker) layer.setIcon(markerIcon(marker, id === selectedId));
    });

    if (selectedId) {
      const layer = markerRefs.current.get(selectedId);
      const map = mapRef.current;
      if (layer && map) {
        map.panTo(layer.getLatLng(), { animate: true, duration: 0.4 });
      }
    }
  }, [selectedId, markers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (userMarkerRef.current) {
      map.removeLayer(userMarkerRef.current);
      userMarkerRef.current = null;
    }
    if (!userCoords) return;

    userMarkerRef.current = L.marker([userCoords.lat, userCoords.lng], {
      icon: L.divIcon({
        className: "khaabo-user-marker",
        html: `<div style="width:14px;height:14px;border-radius:999px;background:rgb(var(--accent));box-shadow:0 0 0 6px rgb(var(--accent) / .25);"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      }),
      interactive: false,
    }).addTo(map);
  }, [userCoords]);

  return (
    <div
      ref={containerRef}
      className={className}
      role="application"
      aria-label="Map of places serving this dish"
    />
  );
}
