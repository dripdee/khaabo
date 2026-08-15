import { useState } from "react";

import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Drawer } from "@/components/Drawer";
import { useGeolocation } from "@/hooks/useGeolocation";
import { cn } from "@/lib/format";
import type { SearchParams } from "@/types/api";

const PRICE_OPTIONS = [
  { label: "Under ₹100", max_price: 100 },
  { label: "Under ₹200", max_price: 200 },
  { label: "Under ₹300", max_price: 300 },
  { label: "Under ₹500", max_price: 500 },
];

const DISTANCE_OPTIONS = [
  { label: "1 km", radius_m: 1000 },
  { label: "3 km", radius_m: 3000 },
  { label: "5 km", radius_m: 5000 },
  { label: "10 km", radius_m: 10000 },
];

const DIETARY_OPTIONS = [
  { label: "Veg", value: "veg" },
  { label: "Non-veg", value: "non_veg" },
  { label: "Vegan", value: "vegan" },
  { label: "Halal", value: "halal" },
];

const MOOD_OPTIONS = [
  { label: "Working", value: "work" },
  { label: "Studying", value: "study" },
  { label: "Date", value: "date" },
  { label: "Late night", value: "late_night" },
  { label: "Group", value: "group" },
];

const TREND_OPTIONS = [
  { label: "↑ Rising", value: "rising" },
  { label: "→ Stable", value: "stable" },
  { label: "↓ Declining", value: "declining" },
];

const SORT_OPTIONS = [
  { label: "Best score", value: "score" },
  { label: "Nearest", value: "distance" },
  { label: "Cheapest", value: "price" },
  { label: "Trending", value: "trending" },
];

export interface FiltersProps {
  filters: SearchParams;
  onChange: (next: Partial<SearchParams>) => void;
  onClear: () => void;
  activeCount: number;
  /** Distance and "nearest" sorting are hidden when the view has no location context. */
  showDistance?: boolean;
}

function Chip({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-chip border px-3 py-1.5 text-sm transition-colors duration-fast",
        active
          ? "border-accent bg-accent/15 text-accent"
          : "border-border bg-surface-2 text-muted hover:border-accent/40 hover:text-text",
      )}
    >
      {children}
    </button>
  );
}

function FilterGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <fieldset className="border-none p-0">
      <legend className="mb-2 text-xs uppercase tracking-wide text-subtle">{label}</legend>
      <div className="flex flex-wrap gap-2">{children}</div>
    </fieldset>
  );
}

/** Filter body, shared by the desktop rail and the mobile bottom sheet. */
function FilterBody({ filters, onChange, showDistance = true }: Omit<FiltersProps, "onClear" | "activeCount">) {
  const geo = useGeolocation();

  return (
    <div className="space-y-6">
      <FilterGroup label="Sort by">
        {SORT_OPTIONS.filter((option) => showDistance || option.value !== "distance").map(
          (option) => (
            <Chip
              key={option.value}
              active={(filters.sort ?? "score") === option.value}
              onClick={() => {
                // "Nearest" is meaningless without coordinates, so requesting it
                // triggers the permission prompt rather than silently failing.
                if (option.value === "distance" && !geo.coords) {
                  geo.request();
                  return;
                }
                onChange({
                  sort: option.value as SearchParams["sort"],
                  ...(option.value === "distance" && geo.coords
                    ? { lat: geo.coords.lat, lng: geo.coords.lng }
                    : {}),
                });
              }}
            >
              {option.label}
            </Chip>
          ),
        )}
      </FilterGroup>

      <FilterGroup label="Price">
        {PRICE_OPTIONS.map((option) => (
          <Chip
            key={option.label}
            active={filters.max_price === option.max_price}
            onClick={() =>
              onChange({
                max_price: filters.max_price === option.max_price ? undefined : option.max_price,
              })
            }
          >
            {option.label}
          </Chip>
        ))}
      </FilterGroup>

      {showDistance && (
        <FilterGroup label="Distance">
          {!geo.coords && (
            <Button size="sm" variant="outline" onClick={geo.request} loading={geo.status === "prompting"}>
              Use my location
            </Button>
          )}
          {geo.coords &&
            DISTANCE_OPTIONS.map((option) => (
              <Chip
                key={option.label}
                active={filters.radius_m === option.radius_m}
                onClick={() =>
                  onChange({
                    radius_m: filters.radius_m === option.radius_m ? undefined : option.radius_m,
                    lat: geo.coords?.lat,
                    lng: geo.coords?.lng,
                  })
                }
              >
                {option.label}
              </Chip>
            ))}
          {geo.error && <p className="w-full text-xs text-warning">{geo.error}</p>}
        </FilterGroup>
      )}

      <FilterGroup label="Dietary">
        {DIETARY_OPTIONS.map((option) => (
          <Chip
            key={option.value}
            active={filters.dietary === option.value}
            onClick={() =>
              onChange({ dietary: filters.dietary === option.value ? undefined : option.value })
            }
          >
            {option.label}
          </Chip>
        ))}
      </FilterGroup>

      <FilterGroup label="Occasion">
        {MOOD_OPTIONS.map((option) => (
          <Chip
            key={option.value}
            active={filters.mood === option.value}
            onClick={() =>
              onChange({ mood: filters.mood === option.value ? undefined : option.value })
            }
          >
            {option.label}
          </Chip>
        ))}
      </FilterGroup>

      <FilterGroup label="Trend">
        {TREND_OPTIONS.map((option) => (
          <Chip
            key={option.value}
            active={filters.trend === option.value}
            onClick={() =>
              onChange({ trend: filters.trend === option.value ? undefined : option.value })
            }
          >
            {option.label}
          </Chip>
        ))}
      </FilterGroup>
    </div>
  );
}

/**
 * Mobile entry point: a button that opens a bottom sheet.
 *
 * Exported separately from the desktop rail because the two live in different places
 * in the page layout — rendering one component that contains both would either
 * duplicate the trigger or force an awkward DOM position.
 */
export function FiltersSheet({ filters, onChange, onClear, activeCount, showDistance }: FiltersProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
        Filters
        {activeCount > 0 && (
          <Badge tone="accent" className="ml-1 px-1.5 py-0">
            {activeCount}
          </Badge>
        )}
      </Button>

      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        title="Filters"
        footer={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              fullWidth
              onClick={() => {
                onClear();
                setOpen(false);
              }}
            >
              Clear all
            </Button>
            <Button fullWidth onClick={() => setOpen(false)}>
              Show results
            </Button>
          </div>
        }
      >
        <FilterBody filters={filters} onChange={onChange} showDistance={showDistance} />
      </Drawer>
    </>
  );
}

/** Desktop rail. Hidden below `lg` by the caller's layout. */
export function FiltersRail({ filters, onChange, onClear, activeCount, showDistance }: FiltersProps) {
  return (
    <aside className="hidden lg:block">
      <div className="sticky top-24 space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-lg text-text">Filters</h2>
          {activeCount > 0 && (
            <button type="button" onClick={onClear} className="text-xs text-accent hover:underline">
              Clear all
            </button>
          )}
        </div>
        <FilterBody filters={filters} onChange={onChange} showDistance={showDistance} />
      </div>
    </aside>
  );
}
