import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { Badge } from "@/components/Badge";
import { Card } from "@/components/Card";
import { useAuth } from "@/hooks/authContext";
import {
  SOURCE_LABELS,
  cn,
  formatRelativeTime,
  humanizeAttribute,
  initials,
  sentimentLabel,
  stringToHue,
} from "@/lib/format";
import { reviewsApi } from "@/services/endpoints";
import type { Review } from "@/types/api";

/**
 * One piece of evidence.
 *
 * Shows which dishes the review was interpreted as talking about, so the reader can
 * see how their words fed the rankings — and spot a mis-extraction.
 */
export function ReviewCard({ review }: { review: Review }) {
  const { isSignedIn } = useAuth();
  const queryClient = useQueryClient();

  const like = useMutation({
    mutationFn: () => reviewsApi.toggleLike(review.id),
    onSettled: () => {
      void queryClient.invalidateQueries({
        queryKey: ["restaurant", review.restaurant_id, "reviews"],
      });
    },
  });

  const authorName =
    review.author?.display_name ?? review.author?.username ?? "Community";
  const hue = stringToHue(authorName);

  return (
    <Card animate>
      <div className="flex items-start gap-3">
        {review.author?.avatar_url ? (
          <img
            src={review.author.avatar_url}
            alt=""
            loading="lazy"
            className="h-10 w-10 rounded-full object-cover"
          />
        ) : (
          <span
            aria-hidden
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-sm font-semibold"
            style={{
              backgroundColor: `hsl(${hue} 60% 22%)`,
              color: `hsl(${hue} 80% 78%)`,
            }}
          >
            {initials(authorName)}
          </span>
        )}

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            {review.author?.username ? (
              <Link
                to={`/u/${review.author.username}`}
                className="font-medium text-text hover:text-accent"
              >
                {authorName}
              </Link>
            ) : (
              <span className="font-medium text-text">{authorName}</span>
            )}

            <Badge tone="neutral" className="px-1.5 py-0 text-[10px]">
              {SOURCE_LABELS[review.source] ?? review.source}
            </Badge>

            <span className="text-xs text-subtle">
              {formatRelativeTime(review.published_at)}
            </span>

            {review.rating != null && (
              <span className="text-xs text-muted" title={`Rated ${review.rating} out of 5`}>
                {"★".repeat(Math.round(review.rating))}
                <span className="text-subtle">{"★".repeat(5 - Math.round(review.rating))}</span>
              </span>
            )}
          </div>

          {review.title && (
            <h4 className="mt-2 font-display text-lg leading-tight text-text">{review.title}</h4>
          )}

          <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-muted">
            {review.body}
          </p>

          {review.dish_mentions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {review.dish_mentions.map((mention) => {
                const tone = sentimentLabel(mention.sentiment);
                return (
                  <Link key={mention.dish_slug} to={`/dish/${mention.dish_slug}`}>
                    <span
                      className={cn(
                        "chip transition-colors hover:border-accent/40",
                        tone === "positive" && "border-positive/30 text-positive",
                        tone === "negative" && "border-negative/30 text-negative",
                      )}
                      title={`Recorded as ${tone} for ${mention.dish_name}`}
                    >
                      {tone === "positive" ? "▲" : tone === "negative" ? "▼" : "■"}
                      {mention.dish_name}
                      {mention.attributes.length > 0 && (
                        <span className="text-subtle">
                          {humanizeAttribute(mention.attributes[0]!)}
                        </span>
                      )}
                    </span>
                  </Link>
                );
              })}
            </div>
          )}

          <div className="mt-3 flex items-center gap-4">
            <button
              type="button"
              disabled={!isSignedIn || like.isPending}
              onClick={() => like.mutate()}
              aria-pressed={review.liked_by_me}
              title={isSignedIn ? "Mark as useful" : "Sign in to mark reviews useful"}
              className={cn(
                "inline-flex items-center gap-1.5 text-xs transition-colors",
                review.liked_by_me ? "text-accent" : "text-subtle hover:text-text",
                !isSignedIn && "cursor-not-allowed opacity-60",
              )}
            >
              <span aria-hidden>{review.liked_by_me ? "♥" : "♡"}</span>
              {review.like_count > 0 ? review.like_count : "Useful"}
            </button>

            {review.source_url && (
              <a
                href={review.source_url}
                target="_blank"
                rel="noreferrer noopener nofollow"
                className="text-xs text-subtle hover:text-text"
              >
                View source
              </a>
            )}

            {review.attribution && (
              <span className="text-xs text-subtle">{review.attribution}</span>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
