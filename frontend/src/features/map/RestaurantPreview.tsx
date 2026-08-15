import { Link } from "react-router-dom";

import { Badge, NotEnoughData, RankBadge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { ScorePill } from "@/components/Score";
import { formatPrice } from "@/lib/format";
import type { MapMarker } from "@/types/api";

/**
 * Selected-marker preview.
 *
 * Rendered as a bottom sheet on mobile and a side panel on desktop by the caller;
 * this component stays layout-agnostic so the same markup serves both.
 */
export function RestaurantPreview({
  marker,
  dishName,
  onClose,
}: {
  marker: MapMarker;
  dishName?: string;
  onClose?: () => void;
}) {
  const isRanked = marker.status === "ranked";

  return (
    <div className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-xl leading-tight text-text">{marker.name}</h3>
          {dishName && (
            <p className="mt-0.5 text-sm text-subtle">
              ranked for <span className="text-muted">{dishName}</span>
            </p>
          )}
        </div>
        {isRanked ? <ScorePill value={marker.score} /> : <NotEnoughData />}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {marker.badges.map((badge) => (
          <RankBadge key={badge} code={badge} />
        ))}
        {marker.price_avg != null && (
          <Badge tone="neutral" title="Average price mentioned for this dish">
            ~{formatPrice(marker.price_avg)}
          </Badge>
        )}
        {marker.trend === "rising" && <Badge tone="positive">↑ Rising</Badge>}
        {marker.trend === "declining" && <Badge tone="warning">↓ Declining</Badge>}
      </div>

      <div className="mt-4 flex gap-2">
        <Link to={`/restaurant/${marker.id}`} className="flex-1">
          <Button fullWidth size="sm">
            View place
          </Button>
        </Link>
        <a
          // Directions deliberately go to OpenStreetMap, keeping the whole product on
          // free, open mapping rather than a proprietary provider.
          href={`https://www.openstreetmap.org/directions?to=${marker.lat}%2C${marker.lng}`}
          target="_blank"
          rel="noreferrer noopener"
          className="flex-1"
        >
          <Button fullWidth size="sm" variant="secondary">
            Directions
          </Button>
        </a>
        {onClose && (
          <Button size="icon" variant="ghost" onClick={onClose} aria-label="Close preview">
            ✕
          </Button>
        )}
      </div>
    </div>
  );
}
