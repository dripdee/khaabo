import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ListSkeleton } from "@/components/Skeleton";
import { useAuth } from "@/hooks/authContext";
import { formatRelativeTime, truncate } from "@/lib/format";
import { queryKeys } from "@/lib/queryClient";
import { Seo } from "@/lib/seo";
import { adminApi, moderationApi } from "@/services/endpoints";

type Tab = "moderation" | "ranking" | "conflicts" | "jobs" | "ai";

const TABS: { id: Tab; label: string }[] = [
  { id: "moderation", label: "Moderation" },
  { id: "ranking", label: "Ranking" },
  { id: "conflicts", label: "Entity conflicts" },
  { id: "jobs", label: "Failed jobs" },
  { id: "ai", label: "AI outputs" },
];

/**
 * Admin dashboard.
 *
 * Deliberately lightweight — a set of operational tables, not a CMS. The moderation
 * queue and the entity-conflict queue exist because the pipeline refuses to guess:
 * ambiguous merges and borderline reviews land here for a human decision.
 */
export default function AdminPage() {
  const { isModerator, isAdmin, isLoading } = useAuth();
  const [tab, setTab] = useState<Tab>("moderation");

  if (isLoading) {
    return <div className="mx-auto max-w-content px-4 py-16 text-center text-muted">Loading…</div>;
  }

  if (!isModerator) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-20 text-center">
        <Seo title="Admin" description="Restricted area." noIndex />
        <h1 className="font-display text-hero text-text">Not authorised</h1>
        <p className="mt-3 text-muted">This area needs a moderator or admin role.</p>
        <Link to="/" className="mt-6 inline-block text-accent hover:underline">
          Back to Khaabo
        </Link>
      </div>
    );
  }

  return (
    <>
      <Seo title="Admin" description="Operational dashboard." noIndex />

      <div className="mx-auto max-w-content px-4 py-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-hero text-text">Admin</h1>
            <p className="mt-1 text-sm text-subtle">
              {isAdmin ? "Full access" : "Moderator access"}
            </p>
          </div>
        </header>

        <nav
          className="mt-6 flex gap-1 overflow-x-auto rounded-chip border border-border bg-surface-2 p-1 no-scrollbar"
          aria-label="Admin sections"
        >
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              aria-current={tab === item.id ? "page" : undefined}
              className={
                tab === item.id
                  ? "whitespace-nowrap rounded-chip bg-accent px-3 py-1.5 text-sm font-medium text-black"
                  : "whitespace-nowrap rounded-chip px-3 py-1.5 text-sm text-muted hover:text-text"
              }
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="mt-8">
          {tab === "moderation" && <ModerationTab />}
          {tab === "ranking" && <RankingTab canRecompute={isAdmin} />}
          {tab === "conflicts" && <ConflictsTab />}
          {tab === "jobs" && <JobsTab />}
          {tab === "ai" && <AiTab />}
        </div>
      </div>
    </>
  );
}

function ModerationTab() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("open");

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.moderationQueue(status, 1),
    queryFn: () => moderationApi.queue(status, 1),
  });

  const decide = useMutation({
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "publish" | "reject" | "flag" | "dismiss";
    }) => moderationApi.decide(id, action),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["moderation"] });
    },
  });

  if (isLoading) return <ListSkeleton count={4} />;

  return (
    <section>
      <div className="mb-4 flex gap-2">
        {["open", "resolved", "dismissed"].map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setStatus(option)}
            aria-pressed={status === option}
            className={status === option ? "chip border-accent/40 text-accent" : "chip"}
          >
            {option}
          </button>
        ))}
      </div>

      {data && data.items.length > 0 ? (
        <div className="space-y-3">
          {data.items.map((item) => (
            <Card key={item.id}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={item.reason === "spam" ? "negative" : "neutral"}>
                      {item.reason}
                    </Badge>
                    {item.is_duplicate && <Badge tone="warning">duplicate</Badge>}
                    {item.spam_score != null && item.spam_score > 0.3 && (
                      <Badge tone="warning">spam {Math.round(item.spam_score * 100)}%</Badge>
                    )}
                    <span className="text-xs text-subtle">
                      severity {item.severity} · {formatRelativeTime(item.created_at)}
                    </span>
                  </div>

                  <p className="mt-3 text-sm text-muted">
                    {truncate(item.review_body ?? "", 400)}
                  </p>

                  {item.history.length > 0 && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs text-subtle">
                        History ({item.history.length})
                      </summary>
                      <ul className="mt-2 space-y-1 text-xs text-subtle">
                        {item.history.map((entry, index) => (
                          <li key={index}>{JSON.stringify(entry)}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>

                {status === "open" && (
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => decide.mutate({ id: item.id, action: "publish" })}
                      loading={decide.isPending}
                    >
                      Publish
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => decide.mutate({ id: item.id, action: "reject" })}
                    >
                      Reject
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => decide.mutate({ id: item.id, action: "dismiss" })}
                    >
                      Dismiss
                    </Button>
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="text-center">
          <p className="text-muted">Queue is empty.</p>
        </Card>
      )}
    </section>
  );
}

function RankingTab({ canRecompute }: { canRecompute: boolean }) {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.adminRanking,
    queryFn: adminApi.ranking,
  });

  const recompute = useMutation({
    mutationFn: (scope: "stale" | "all") => adminApi.recompute(scope),
  });

  if (isLoading || !data) return <ListSkeleton count={2} />;

  return (
    <section className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { label: "Ranked pairs", value: data.ranked_pairs },
          { label: "Insufficient data", value: data.insufficient_pairs },
          { label: "Reviews pending AI", value: data.reviews_pending_ai },
        ].map((stat) => (
          <Card key={stat.label}>
            <p className="text-xs uppercase tracking-wide text-subtle">{stat.label}</p>
            <p className="mt-1 font-display text-3xl text-text">{stat.value}</p>
          </Card>
        ))}
      </div>

      <Card>
        <h2 className="font-display text-xl text-text">Weights</h2>
        <p className="mt-1 text-sm text-subtle">
          Version {data.weights_version} · half-life {data.halflife_days} days · shrinkage m ={" "}
          {data.bayes_m} · minimum {data.min_mentions} mentions
        </p>

        <ul className="mt-4 space-y-2">
          {Object.entries(data.weights).map(([key, weight]) => (
            <li key={key} className="flex items-center gap-3">
              <span className="w-32 text-sm capitalize text-muted">{key.replace("_", " ")}</span>
              <span className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2">
                <span
                  className="block h-full rounded-full bg-accent"
                  style={{ width: `${weight * 100 * 2.5}%` }}
                />
              </span>
              <span className="w-12 text-right text-sm tabular-nums text-text">
                {Math.round(weight * 100)}%
              </span>
            </li>
          ))}
        </ul>

        {canRecompute && (
          <div className="mt-6 flex gap-2 border-t border-border pt-4">
            <Button size="sm" variant="secondary" onClick={() => recompute.mutate("stale")}>
              Recompute stale
            </Button>
            <Button size="sm" variant="ghost" onClick={() => recompute.mutate("all")}>
              Full recompute
            </Button>
            {recompute.isSuccess && (
              <span className="self-center text-xs text-positive">Queued</span>
            )}
          </div>
        )}
      </Card>
    </section>
  );
}

function ConflictsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "conflicts"],
    queryFn: () => adminApi.entityConflicts(1),
  });

  if (isLoading) return <ListSkeleton count={3} />;

  return (
    <section>
      <p className="mb-4 text-sm text-subtle">
        Places the resolver refused to merge because two candidates were too similar to
        separate. A wrong merge corrupts every ranking that touches the restaurant, so these
        wait for a human.
      </p>

      {data && data.items.length > 0 ? (
        <div className="space-y-3">
          {data.items.map((conflict) => (
            <Card key={String(conflict.id)}>
              <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted">
                {JSON.stringify(conflict, null, 2)}
              </pre>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="text-center">
          <p className="text-muted">No open conflicts.</p>
        </Card>
      )}
    </section>
  );
}

function JobsTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: adminApi.failedJobs,
  });

  if (isLoading) return <ListSkeleton count={3} />;

  const ingestion = data?.ingestion ?? [];
  const ai = data?.ai ?? [];

  return (
    <section className="space-y-8">
      <div>
        <h2 className="mb-3 font-display text-xl text-text">Ingestion</h2>
        {ingestion.length > 0 ? (
          <div className="space-y-2">
            {ingestion.map((job) => (
              <Card key={String(job.id)}>
                <p className="text-sm text-text">{String(job.job_key)}</p>
                <p className="mt-1 text-xs text-negative">{String(job.error ?? "")}</p>
              </Card>
            ))}
          </div>
        ) : (
          <Card className="text-center">
            <p className="text-muted">No failed ingestion jobs.</p>
          </Card>
        )}
      </div>

      <div>
        <h2 className="mb-3 font-display text-xl text-text">AI processing</h2>
        {ai.length > 0 ? (
          <div className="space-y-2">
            {ai.map((job) => (
              <Card key={String(job.id)}>
                <p className="text-sm text-text">review {String(job.review_id)}</p>
                <p className="mt-1 text-xs text-negative">{String(job.error ?? "")}</p>
              </Card>
            ))}
          </div>
        ) : (
          <Card className="text-center">
            <p className="text-muted">No failed AI jobs.</p>
          </Card>
        )}
      </div>
    </section>
  );
}

function AiTab() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "ai-outputs"],
    queryFn: () => adminApi.aiOutputs(1),
  });

  if (isLoading) return <ListSkeleton count={3} />;

  return (
    <section>
      <p className="mb-4 text-sm text-subtle">
        Recent extraction runs, including which provider handled each review. A `heuristic`
        provider means no model was available and the deterministic pipeline ran instead.
      </p>

      {data && data.items.length > 0 ? (
        <div className="space-y-2">
          {data.items.map((job) => (
            <Card key={String(job.id)}>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <Badge tone="neutral">{String(job.provider ?? "unknown")}</Badge>
                {job.model ? <Badge tone="neutral">{String(job.model)}</Badge> : null}
                <span className="text-subtle">
                  {String(job.mentions_created ?? 0)} mentions ·{" "}
                  {String(job.latency_ms ?? "?")} ms
                </span>
              </div>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-muted">
                {JSON.stringify(job.payload, null, 2)}
              </pre>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="text-center">
          <p className="text-muted">No AI runs recorded yet.</p>
        </Card>
      )}
    </section>
  );
}
