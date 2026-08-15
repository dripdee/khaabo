import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { useAuth } from "@/hooks/authContext";
import { queryKeys } from "@/lib/queryClient";
import { Seo } from "@/lib/seo";
import { ApiError } from "@/services/client";
import { restaurantsApi, reviewsApi } from "@/services/endpoints";

const schema = z.object({
  restaurant_id: z.string().uuid("Pick a restaurant"),
  body: z
    .string()
    .min(20, "Tell us a bit more — at least 20 characters")
    .max(5000, "That's longer than we can store"),
  title: z.string().max(200).optional(),
  rating: z.coerce.number().min(1).max(5).optional(),
});

type FormValues = z.infer<typeof schema>;

/**
 * Review submission.
 *
 * The form is explicit that submissions are moderated and processed asynchronously,
 * because the review will not appear instantly and pretending otherwise would read
 * as a bug.
 */
export default function ReviewSubmitPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { isSignedIn, isLoading } = useAuth();
  const [serverError, setServerError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<{ status: string; message: string } | null>(null);

  const restaurantId = searchParams.get("restaurant") ?? "";
  const dishSlug = searchParams.get("dish");

  const { data: restaurant } = useQuery({
    queryKey: queryKeys.restaurant(restaurantId),
    queryFn: () => restaurantsApi.detail(restaurantId),
    enabled: Boolean(restaurantId),
  });

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    defaultValues: { restaurant_id: restaurantId, body: "", rating: undefined },
  });

  const body = watch("body") ?? "";

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      reviewsApi.create({
        restaurant_id: values.restaurant_id,
        body: values.body,
        title: values.title || undefined,
        rating: values.rating,
        dish_hints: dishSlug ? [dishSlug] : undefined,
      }),
    onSuccess: (result) => {
      setSubmitted({ status: result.status, message: result.message });
    },
    onError: (error) => {
      setServerError(
        error instanceof ApiError
          ? error.code === "duplicate_review"
            ? "You've already submitted this exact review."
            : error.message
          : "Something went wrong. Please try again.",
      );
    },
  });

  async function onSubmit(values: FormValues) {
    setServerError(null);
    const parsed = schema.safeParse(values);
    if (!parsed.success) {
      setServerError(parsed.error.issues[0]?.message ?? "Please check the form");
      return;
    }
    await mutation.mutateAsync(parsed.data);
  }

  if (isLoading) {
    return <div className="mx-auto max-w-2xl px-4 py-16 text-center text-muted">Loading…</div>;
  }

  if (!isSignedIn) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-20 text-center">
        <Seo title="Write a review" description="Sign in to contribute a review." noIndex />
        <h1 className="font-display text-hero text-text">Sign in to review</h1>
        <p className="mt-4 text-muted">
          Reviews are attributed to an account so rankings can be traced back to real
          contributions.
        </p>
        <Link to="/auth" className="mt-8 inline-block">
          <Button>Sign in</Button>
        </Link>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-20 text-center">
        <Seo title="Review submitted" description="Thanks for contributing." noIndex />
        <h1 className="font-display text-hero text-text">Thank you</h1>
        <p className="mt-4 text-muted">{submitted.message}</p>
        <div className="mt-4 flex justify-center">
          <Badge tone={submitted.status === "published" ? "positive" : "neutral"}>
            {submitted.status === "published" ? "Published" : "Awaiting moderation"}
          </Badge>
        </div>
        <p className="mx-auto mt-6 max-w-md text-sm text-subtle">
          We extract each dish you mentioned separately, so a review praising one dish and
          criticising another affects both rankings correctly.
        </p>
        <div className="mt-8 flex justify-center gap-2">
          {restaurantId && (
            <Button variant="secondary" onClick={() => navigate(`/restaurant/${restaurantId}`)}>
              Back to place
            </Button>
          )}
          <Button onClick={() => navigate("/")}>Done</Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Seo title="Write a review" description="Share what you ate and how it was." noIndex />

      <div className="mx-auto max-w-2xl px-4 py-10">
        <h1 className="font-display text-hero text-text">Write a review</h1>
        <p className="mt-3 text-muted">
          Name the dishes you actually ate. We read each one separately, so “the momo was
          great but the biryani was average” helps both rankings.
        </p>

        <Card className="mt-8">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
            <div>
              <label htmlFor="restaurant_id" className="mb-1.5 block text-sm text-muted">
                Restaurant
              </label>
              {restaurant ? (
                <div className="rounded-input border border-border bg-surface-2 px-4 py-3">
                  <p className="font-medium text-text">{restaurant.name}</p>
                  {restaurant.area && (
                    <p className="text-sm text-subtle">{restaurant.area}</p>
                  )}
                </div>
              ) : (
                <input
                  id="restaurant_id"
                  {...register("restaurant_id")}
                  placeholder="Restaurant ID"
                  className="h-12 w-full rounded-input border border-border bg-surface px-4 text-text
                    placeholder:text-subtle focus:border-accent/60 focus:outline-none"
                />
              )}
              {errors.restaurant_id && (
                <p className="mt-1 text-xs text-negative">{errors.restaurant_id.message}</p>
              )}
              {!restaurant && (
                <p className="mt-1.5 text-xs text-subtle">
                  Tip: open a restaurant page and use “Write a review” to prefill this.
                </p>
              )}
            </div>

            <div>
              <label htmlFor="rating" className="mb-1.5 block text-sm text-muted">
                Rating <span className="text-subtle">(optional)</span>
              </label>
              <select
                id="rating"
                {...register("rating")}
                className="h-12 w-full rounded-input border border-border bg-surface px-4 text-text
                  focus:border-accent/60 focus:outline-none"
              >
                <option value="">No rating</option>
                {[5, 4, 3, 2, 1].map((value) => (
                  <option key={value} value={value}>
                    {"★".repeat(value)} ({value}/5)
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="body" className="mb-1.5 block text-sm text-muted">
                Your review
              </label>
              <textarea
                id="body"
                rows={7}
                {...register("body")}
                placeholder="The chicken momo was juicy and hot, ₹120 for eight pieces. The chowmein was oily though."
                aria-describedby="body-help"
                className="w-full rounded-input border border-border bg-surface p-4 text-text
                  placeholder:text-subtle focus:border-accent/60 focus:outline-none"
              />
              <div className="mt-1 flex items-center justify-between">
                <p id="body-help" className="text-xs text-subtle">
                  Mention dish names and prices — both feed the rankings.
                </p>
                <span
                  className={
                    body.length > 5000 || (body.length > 0 && body.length < 20)
                      ? "text-xs text-warning"
                      : "text-xs text-subtle"
                  }
                >
                  {body.length}/5000
                </span>
              </div>
              {errors.body && (
                <p className="mt-1 text-xs text-negative">{errors.body.message}</p>
              )}
            </div>

            {serverError && (
              <p role="alert" className="rounded-input bg-negative/10 px-4 py-3 text-sm text-negative">
                {serverError}
              </p>
            )}

            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-subtle">
                Reviews are moderated. Rate limits apply to keep rankings honest.
              </p>
              <Button type="submit" loading={isSubmitting || mutation.isPending}>
                Submit
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}
