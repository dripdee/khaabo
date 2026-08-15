import { cn } from "@/lib/format";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

/** Matches DishCard's layout so there is no shift when data arrives. */
export function DishCardSkeleton() {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 space-y-2">
          <Skeleton className="h-6 w-2/3" />
          <Skeleton className="h-4 w-1/3" />
        </div>
        <Skeleton className="h-14 w-14 rounded-full" />
      </div>
      <div className="mt-4 space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-4/5" />
      </div>
    </div>
  );
}

/** Matches RestaurantCard's layout. */
export function RestaurantCardSkeleton() {
  return (
    <div className="card p-5">
      <div className="flex items-start gap-4">
        <Skeleton className="h-12 w-12 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-4 w-5/6" />
        </div>
      </div>
    </div>
  );
}

export function ListSkeleton({
  count = 5,
  variant = "restaurant",
}: {
  count?: number;
  variant?: "dish" | "restaurant";
}) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading results">
      {Array.from({ length: count }, (_, index) =>
        variant === "dish" ? (
          <DishCardSkeleton key={index} />
        ) : (
          <RestaurantCardSkeleton key={index} />
        ),
      )}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export function MapSkeleton() {
  return (
    <div className="skeleton h-full min-h-[320px] w-full rounded-card" role="status">
      <span className="sr-only">Loading map…</span>
    </div>
  );
}

export function DishPageSkeleton() {
  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-16 w-3/4" />
        <div className="flex gap-2">
          <Skeleton className="h-7 w-24 rounded-chip" />
          <Skeleton className="h-7 w-20 rounded-chip" />
          <Skeleton className="h-7 w-28 rounded-chip" />
        </div>
      </div>
      <ListSkeleton count={4} />
    </div>
  );
}
