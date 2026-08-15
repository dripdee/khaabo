import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Modal } from "@/components/Modal";
import { Button } from "@/components/Button";
import { useAuth } from "@/hooks/authContext";
import { cn } from "@/lib/format";
import { queryKeys } from "@/lib/queryClient";
import { bookmarksApi } from "@/services/endpoints";
import type { Bookmark } from "@/types/api";

export interface BookmarkButtonProps {
  targetType: "dish" | "restaurant" | "dish_restaurant";
  dishId?: string;
  restaurantId?: string;
  label?: string;
  className?: string;
}

/**
 * Bookmark toggle with an optimistic update.
 *
 * The API upsert is idempotent, so an optimistic flip is safe: a double-save
 * reconciles to the same state rather than erroring.
 */
export function BookmarkButton({
  targetType,
  dishId,
  restaurantId,
  label,
  className,
}: BookmarkButtonProps) {
  const { isSignedIn } = useAuth();
  const queryClient = useQueryClient();
  const [promptOpen, setPromptOpen] = useState(false);

  const listKey = queryKeys.bookmarks({});

  const { data } = useQuery({
    queryKey: listKey,
    queryFn: () => bookmarksApi.list({ page: 1 }),
    enabled: isSignedIn,
    staleTime: 60 * 1000,
  });

  const existing = data?.items.find(
    (bookmark) =>
      bookmark.target_type === targetType &&
      (bookmark.dish_id ?? undefined) === dishId &&
      (bookmark.restaurant_id ?? undefined) === restaurantId,
  );

  const saved = Boolean(existing);

  const mutation = useMutation({
    mutationFn: async () => {
      if (existing) {
        await bookmarksApi.remove(existing.id);
        return null;
      }
      return bookmarksApi.create({
        target_type: targetType,
        dish_id: dishId,
        restaurant_id: restaurantId,
      });
    },
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const previous = queryClient.getQueryData(listKey);

      queryClient.setQueryData(listKey, (current: { items: Bookmark[] } | undefined) => {
        if (!current) return current;
        if (existing) {
          return { ...current, items: current.items.filter((item) => item.id !== existing.id) };
        }
        const optimistic: Bookmark = {
          id: `optimistic-${Date.now()}`,
          target_type: targetType,
          dish_id: dishId ?? null,
          restaurant_id: restaurantId ?? null,
          created_at: new Date().toISOString(),
        };
        return { ...current, items: [optimistic, ...current.items] };
      });

      return { previous };
    },
    onError: (_error, _variables, context) => {
      // Roll back so the icon never lies about what is saved.
      if (context?.previous) queryClient.setQueryData(listKey, context.previous);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: listKey });
    },
  });

  function onClick(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();

    if (!isSignedIn) {
      setPromptOpen(true);
      return;
    }
    mutation.mutate();
  }

  return (
    <>
      <button
        type="button"
        onClick={onClick}
        aria-pressed={saved}
        aria-label={saved ? `Remove ${label ?? "item"} from bookmarks` : `Save ${label ?? "item"}`}
        title={saved ? "Saved" : "Save"}
        className={cn(
          "grid h-9 w-9 place-items-center rounded-full border transition-all duration-fast",
          saved
            ? "border-accent/50 bg-accent/15 text-accent"
            : "border-border text-subtle hover:border-accent/40 hover:text-accent",
          className,
        )}
      >
        <span aria-hidden>{saved ? "★" : "☆"}</span>
      </button>

      <Modal
        open={promptOpen}
        onClose={() => setPromptOpen(false)}
        title="Sign in to save"
        description="Bookmarks and collections are tied to your account."
        footer={
          <>
            <Button variant="ghost" onClick={() => setPromptOpen(false)}>
              Not now
            </Button>
            <Button onClick={() => (window.location.href = "/auth")}>Sign in</Button>
          </>
        }
      >
        <p className="text-sm text-muted">
          Saving {label ?? "this"} needs an account. It takes a few seconds and lets you build
          collections like “momo crawl” or “places to try”.
        </p>
      </Modal>
    </>
  );
}
