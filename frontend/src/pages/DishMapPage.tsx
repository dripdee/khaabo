import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Badge, NotEnoughData } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Drawer } from "@/components/Drawer";
import { MapSkeleton } from "@/components/Skeleton";
import { useDish, useDishMap } from "@/features/dishes/useDish";
import { DishMapView } from "@/features/map/DishMapView";
import { RestaurantPreview } from "@/features/map/RestaurantPreview";
import { FiltersSheet } from "@/features/search/Filters";
import { useSearchFilters } from "@/features/search/useSearch";
import { useGeolocation } from "@/hooks/useGeolocation";
import { Seo } from "@/lib/seo";
import type { MapMarker } from "@/types/api";

/**
 * Dish map results — priority screen 3.
 *
 * The marker set is fetched per dish, so changing the dish replaces the entire map.
 * Selection opens a bottom sheet on mobile and a side panel on desktop, which is the
 * only layout that works on a phone without shrinking the map to uselessness.
 */
export default function DishMapPage() {
  const { slug } = useParams<{ slug: string }>();
  const { filters, setFilters, clearFilters, activeFilterCount } = useSearchFilters();
  const geo = useGeolocation();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const coords = filters.lat && filters.lng ? { lat: filters.lat, lng: filters.lng } : geo.coords;

  const { data: detail } = useDish(slug);
  const { data: mapData, isLoading } = useDishMap(slug, {
    ...(coords ? { lat: coords.lat, lng: coords.lng } : {}),
    radius_m: filters.radius_m,
    max_price: filters.max_price,
    trend: filters.trend,
  });

  const markers = useMemo(() => mapData?.markers ?? [], [mapData]);
  const selected = useMemo(
    () => markers.find((marker) => marker.id === selectedId) ?? null,
    [markers, selectedId],
  );

  const rankedCount = markers.filter((marker) => marker.status === "ranked").length;
  const dishName = detail?.dish.name ?? mapData?.dish.name ?? slug;

  function onSelect(marker: MapMarker) {
    setSelectedId(marker.id);
  }

  return (
    <>
      <Seo
        title={`${dishName} map · Kolkata`}
        description={`Map of places serving ${dishName} in Kolkata, coloured by dish score.`}
        canonicalPath={`/dish/${slug}/map`}
        noIndex
      />

      <div className="mx-auto max-w-content px-4 py-6">
        <nav aria-label="Breadcrumb" className="text-sm text-subtle">
          <Link to="/" className="hover:text-text">
            Khaabo
          </Link>
          <span aria-hidden className="mx-2">
            /
          </span>
          <Link to={`/dish/${slug}`} className="hover:text-text">
            {dishName}
          </Link>
          <span aria-hidden className="mx-2">
            /
          </span>
          <span className="text-muted">Map</span>
        </nav>

        <header className="mt-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-title text-text">{dishName} on the map</h1>
            <p aria-live="polite" className="mt-1 text-sm text-subtle">
              {isLoading
                ? "Loading places…"
                : `${rankedCount} ranked place${rankedCount === 1 ? "" : "s"}${
                    markers.length > rankedCount
                      ? ` · ${markers.length - rankedCount} with too little data`
                      : ""
                  }`}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <FiltersSheet
              filters={filters}
              onChange={setFilters}
              onClear={() => clearFilters()}
              activeCount={activeFilterCount}
            />
            {!coords && (
              <Button
                variant="outline"
                size="sm"
                onClick={geo.request}
                loading={geo.status === "prompting"}
              >
                Near me
              </Button>
            )}
            <Link to={`/dish/${slug}`}>
              <Button variant="secondary" size="sm">
                List view
              </Button>
            </Link>
          </div>
        </header>

        <div className="mt-5 grid gap-4 lg:grid-cols-[1fr_360px]">
          <div className="relative h-[60vh] min-h-[380px] overflow-hidden rounded-card border border-border lg:h-[calc(100vh-220px)]">
            {isLoading ? (
              <MapSkeleton />
            ) : markers.length === 0 ? (
              <div className="grid h-full place-items-center p-8 text-center">
                <div>
                  <NotEnoughData />
                  <p className="mt-4 max-w-sm text-muted">
                    No mapped places for {dishName}
                    {activeFilterCount > 0 ? " with these filters" : ""} yet.
                  </p>
                  {activeFilterCount > 0 && (
                    <Button variant="ghost" className="mt-4" onClick={() => clearFilters()}>
                      Clear filters
                    </Button>
                  )}
                </div>
              </div>
            ) : (
              <DishMapView
                markers={markers}
                bounds={mapData?.bounds ?? null}
                selectedId={selectedId}
                userCoords={coords}
                onSelect={onSelect}
                className="h-full w-full"
              />
            )}

            {/* Legend: the score bands and badge glyphs used by the markers. */}
            <div className="glass pointer-events-none absolute bottom-3 left-3 rounded-card border border-border px-3 py-2 text-[11px] text-muted">
              <div className="flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: "rgb(var(--positive))" }}
                    aria-hidden
                  />
                  80+
                </span>
                <span className="flex items-center gap-1">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: "rgb(var(--accent-2))" }}
                    aria-hidden
                  />
                  65+
                </span>
                <span className="flex items-center gap-1">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ background: "rgb(var(--warning))" }}
                    aria-hidden
                  />
                  45+
                </span>
                <span className="flex items-center gap-1">
                  <span aria-hidden>₹</span> value
                </span>
                <span className="flex items-center gap-1">
                  <span aria-hidden>◆</span> gem
                </span>
              </div>
            </div>
          </div>

          {/* Desktop: side panel. */}
          <aside className="hidden lg:block">
            <div className="sticky top-24 space-y-3">
              {selected ? (
                <div className="card overflow-hidden p-0">
                  <RestaurantPreview
                    marker={selected}
                    dishName={dishName}
                    onClose={() => setSelectedId(null)}
                  />
                </div>
              ) : (
                <div className="card">
                  <h2 className="font-display text-lg text-text">Pick a marker</h2>
                  <p className="mt-2 text-sm text-muted">
                    Each marker shows the dish score for {dishName} at that place — not a
                    general restaurant rating.
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Badge tone="neutral">₹ best value</Badge>
                    <Badge tone="neutral">◆ hidden gem</Badge>
                    <Badge tone="neutral">◎ most consistent</Badge>
                  </div>
                </div>
              )}

              <div className="card">
                <h3 className="text-sm font-medium text-text">Top of the list</h3>
                <ul className="mt-3 space-y-2">
                  {markers
                    .filter((marker) => marker.status === "ranked")
                    .slice(0, 6)
                    .map((marker, index) => (
                      <li key={marker.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(marker.id)}
                          className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5
                            text-left text-sm transition-colors hover:bg-surface-2"
                        >
                          <span className="truncate text-muted">
                            <span className="mr-2 tabular-nums text-subtle">{index + 1}</span>
                            {marker.name}
                          </span>
                          <span className="tabular-nums text-text">
                            {marker.score != null ? Math.round(marker.score) : "—"}
                          </span>
                        </button>
                      </li>
                    ))}
                </ul>
              </div>
            </div>
          </aside>
        </div>

        <p className="mt-4 text-xs text-subtle">
          {(mapData?.attribution ?? ["© OpenStreetMap contributors"]).join(" · ")}
        </p>
      </div>

      {/* Mobile: bottom sheet, so the map keeps full width. */}
      <div className="lg:hidden">
        <Drawer
          open={Boolean(selected)}
          onClose={() => setSelectedId(null)}
          title={selected?.name}
        >
          {selected && <RestaurantPreview marker={selected} dishName={dishName} />}
        </Drawer>
      </div>
    </>
  );
}
