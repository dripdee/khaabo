import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Modal } from "@/components/Modal";
import { ListSkeleton } from "@/components/Skeleton";
import { useAuth } from "@/hooks/authContext";
import { formatRelativeTime } from "@/lib/format";
import { queryKeys } from "@/lib/queryClient";
import { Seo } from "@/lib/seo";
import { bookmarksApi } from "@/services/endpoints";

/** Saved dishes, places and dish-at-place pairs, optionally grouped into collections. */
export default function BookmarksPage() {
  const { isSignedIn, isLoading: authLoading } = useAuth();
  const queryClient = useQueryClient();
  const [activeCollection, setActiveCollection] = useState<string | undefined>();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const { data: collections } = useQuery({
    queryKey: queryKeys.collections,
    queryFn: bookmarksApi.collections,
    enabled: isSignedIn,
  });

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.bookmarks({ collection_id: activeCollection }),
    queryFn: () => bookmarksApi.list({ collection_id: activeCollection }),
    enabled: isSignedIn,
  });

  const createCollection = useMutation({
    mutationFn: () => bookmarksApi.createCollection({ name }),
    onSuccess: () => {
      setCreating(false);
      setName("");
      void queryClient.invalidateQueries({ queryKey: queryKeys.collections });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => bookmarksApi.remove(id),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["bookmarks"] });
    },
  });

  if (authLoading) {
    return <div className="mx-auto max-w-content px-4 py-16 text-center text-muted">Loading…</div>;
  }

  if (!isSignedIn) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-20 text-center">
        <Seo title="Bookmarks" description="Your saved dishes and places." noIndex />
        <h1 className="font-display text-hero text-text">Sign in to see saves</h1>
        <Link to="/auth" className="mt-6 inline-block">
          <Button>Sign in</Button>
        </Link>
      </div>
    );
  }

  return (
    <>
      <Seo title="Bookmarks" description="Your saved dishes and places." noIndex />

      <div className="mx-auto max-w-content px-4 py-10">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-hero text-text">Saved</h1>
            <p className="mt-2 text-muted">Dishes, places, and specific dish-at-place picks.</p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => setCreating(true)}>
            New collection
          </Button>
        </header>

        {collections && collections.items.length > 0 && (
          <div className="mt-6 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setActiveCollection(undefined)}
              aria-pressed={!activeCollection}
              className={
                !activeCollection
                  ? "chip border-accent/40 text-accent"
                  : "chip hover:text-text"
              }
            >
              All
            </button>
            {collections.items.map((collection) => (
              <button
                key={collection.id}
                type="button"
                onClick={() => setActiveCollection(collection.id)}
                aria-pressed={activeCollection === collection.id}
                className={
                  activeCollection === collection.id
                    ? "chip border-accent/40 text-accent"
                    : "chip hover:text-text"
                }
              >
                {collection.name}
                <span className="text-subtle">{collection.bookmark_count}</span>
              </button>
            ))}
          </div>
        )}

        <div className="mt-8">
          {isLoading ? (
            <ListSkeleton count={4} />
          ) : data && data.items.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {data.items.map((bookmark) => {
                const href =
                  bookmark.target_type === "dish" && bookmark.dish_slug
                    ? `/dish/${bookmark.dish_slug}`
                    : bookmark.restaurant_id
                      ? `/restaurant/${bookmark.restaurant_id}`
                      : "/";

                const label =
                  bookmark.target_type === "dish_restaurant"
                    ? `${bookmark.dish_name ?? "Dish"} at ${bookmark.restaurant_name ?? "place"}`
                    : (bookmark.dish_name ?? bookmark.restaurant_name ?? "Saved item");

                return (
                  <Card key={bookmark.id} animate>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link
                          to={href}
                          className="font-display text-lg leading-tight text-text hover:text-accent"
                        >
                          {label}
                        </Link>
                        <p className="mt-1 text-xs text-subtle">
                          {bookmark.target_type.replace("_", " + ")} ·{" "}
                          {formatRelativeTime(bookmark.created_at)}
                        </p>
                        {bookmark.note && (
                          <p className="mt-2 text-sm text-muted">{bookmark.note}</p>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => remove.mutate(bookmark.id)}
                        aria-label={`Remove ${label}`}
                        className="text-subtle transition-colors hover:text-negative"
                      >
                        ✕
                      </button>
                    </div>
                  </Card>
                );
              })}
            </div>
          ) : (
            <Card className="text-center">
              <p className="text-muted">Nothing saved yet.</p>
              <p className="mt-1 text-sm text-subtle">
                Tap the star on any dish or place to keep it here.
              </p>
              <Link to="/" className="mt-4 inline-block">
                <Button size="sm">Find something to eat</Button>
              </Link>
            </Card>
          )}
        </div>
      </div>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="New collection"
        description="Group saves however you like — “momo crawl”, “date spots”, “to try”."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => createCollection.mutate()}
              loading={createCollection.isPending}
              disabled={name.trim().length === 0}
            >
              Create
            </Button>
          </>
        }
      >
        <label htmlFor="collection-name" className="mb-1.5 block text-sm text-muted">
          Name
        </label>
        <input
          id="collection-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={80}
          className="h-12 w-full rounded-input border border-border bg-surface px-4 text-text
            placeholder:text-subtle focus:border-accent/60 focus:outline-none"
          placeholder="Momo crawl"
        />
      </Modal>
    </>
  );
}
