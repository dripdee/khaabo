import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Badge } from "@/components/Badge";
import { Card } from "@/components/Card";
import { ListSkeleton } from "@/components/Skeleton";
import { formatCount, initials, stringToHue } from "@/lib/format";
import { queryKeys } from "@/lib/queryClient";
import { Seo } from "@/lib/seo";
import { usersApi } from "@/services/endpoints";

/** Public profile: contributions and badges, no personal data beyond what was set. */
export default function ProfilePage() {
  const { username } = useParams<{ username: string }>();

  const { data: profile, isLoading } = useQuery({
    queryKey: queryKeys.profile(username ?? ""),
    queryFn: () => usersApi.profile(username as string),
    enabled: Boolean(username),
  });

  if (isLoading) {
    return (
      <div className="mx-auto max-w-content px-4 py-10">
        <ListSkeleton count={3} />
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="mx-auto max-w-content px-4 py-24 text-center">
        <h1 className="font-display text-hero text-text">Profile not found</h1>
        <Link to="/" className="mt-6 inline-block text-accent hover:underline">
          Back to search
        </Link>
      </div>
    );
  }

  const name = profile.display_name ?? profile.username;
  const hue = stringToHue(name);

  return (
    <>
      <Seo
        title={`${name} · Profile`}
        description={profile.bio ?? `${name}'s contributions on Khaabo.`}
        canonicalPath={`/u/${profile.username}`}
      />

      <div className="mx-auto max-w-content space-y-8 px-4 py-10">
        <header className="flex flex-wrap items-start gap-6">
          {profile.avatar_url ? (
            <img
              src={profile.avatar_url}
              alt=""
              className="h-20 w-20 rounded-full object-cover"
              loading="lazy"
            />
          ) : (
            <span
              aria-hidden
              className="grid h-20 w-20 place-items-center rounded-full text-2xl font-semibold"
              style={{ backgroundColor: `hsl(${hue} 60% 22%)`, color: `hsl(${hue} 80% 78%)` }}
            >
              {initials(name)}
            </span>
          )}

          <div className="min-w-0 flex-1">
            <h1 className="font-display text-hero leading-none text-text">{name}</h1>
            <p className="mt-1 text-muted">@{profile.username}</p>
            {profile.bio && <p className="mt-3 max-w-prose text-muted">{profile.bio}</p>}
          </div>

          <dl className="flex gap-6">
            {[
              { label: "Published", value: profile.published_review_count },
              { label: "Useful votes", value: profile.like_received_count },
              { label: "Points", value: profile.contribution_score },
            ].map((stat) => (
              <div key={stat.label}>
                <dd className="font-display text-2xl text-text">{formatCount(stat.value)}</dd>
                <dt className="text-xs uppercase tracking-wide text-subtle">{stat.label}</dt>
              </div>
            ))}
          </dl>
        </header>

        {profile.badges.length > 0 ? (
          <section aria-labelledby="badges-heading">
            <h2 id="badges-heading" className="mb-1 font-display text-title text-text">
              Badges
            </h2>
            <p className="mb-4 text-sm text-subtle">
              Earned from published reviews across different places — not from volume alone.
            </p>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {profile.badges.map((badge) => (
                <Card key={badge.code} animate>
                  <div className="flex items-start gap-3">
                    <span aria-hidden className="text-2xl">
                      {badge.emoji}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium text-text">{badge.label}</h3>
                        <Badge tone="accent" className="px-1.5 py-0 text-[10px]">
                          Lv {badge.level}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-subtle">{badge.description}</p>

                      {badge.target != null && (
                        <div className="mt-2">
                          <div
                            className="h-1.5 overflow-hidden rounded-full bg-surface-2"
                            role="progressbar"
                            aria-valuenow={badge.progress}
                            aria-valuemax={badge.target}
                            aria-label={`${badge.label} progress`}
                          >
                            <div
                              className="h-full rounded-full bg-accent"
                              style={{
                                width: `${Math.min(100, (badge.progress / badge.target) * 100)}%`,
                              }}
                            />
                          </div>
                          <p className="mt-1 text-[11px] text-subtle">
                            {badge.progress} / {badge.target} to next level
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          </section>
        ) : (
          <Card className="text-center">
            <p className="text-muted">No badges yet.</p>
            <p className="mt-1 text-sm text-subtle">
              Badges come from published reviews at different places.
            </p>
          </Card>
        )}

        {(profile.favourite_dishes.length > 0 || profile.favourite_restaurants.length > 0) && (
          <section aria-labelledby="favourites-heading">
            <h2 id="favourites-heading" className="mb-4 font-display text-title text-text">
              Favourites
            </h2>
            <div className="flex flex-wrap gap-2">
              {profile.favourite_dishes.map((dish) => (
                <Link key={dish.id} to={`/dish/${dish.slug}`} className="chip hover:text-accent">
                  {dish.name}
                </Link>
              ))}
              {profile.favourite_restaurants.map((restaurant) => (
                <Link
                  key={restaurant.id}
                  to={`/restaurant/${restaurant.id}`}
                  className="chip hover:text-accent"
                >
                  {restaurant.name}
                </Link>
              ))}
            </div>
          </section>
        )}
      </div>
    </>
  );
}
