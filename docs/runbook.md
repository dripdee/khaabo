# Khaabo — Operations Runbook

This is the page to open when something is wrong, or when you need to deploy
without breaking anything. It assumes you are SSH'd into the production host
with `/opt/khaabo` as the deploy directory.

---

## 1. Service topology

```
Caddy :443 ── /api/* ──▶ api :8000 (uvicorn ×2)
           ├── /      ──▶ web :80  (nginx static SPA)
           └── /assets (immutable, 1y cache)

Redis :6379  ← api (cache) + worker/beat (broker, backend)
Postgres+PostGIS :5432

Celery worker (ingestion / ai / ranking / summarization queues)
Celery beat (scheduled ingestion + nightly ranking sweep)
```

Monitoring (optional, `ops` profile):

```
Prometheus :9090  ← scrapes api :8000/api/v1/metrics
Loki :3100       ← promtail tails docker logs
Grafana :3000    ← dashboard "Khaabo API Overview"
```

---

## 2. Common commands

```bash
# Deploy: CI pushes images on main; server pulls + rolls. Manual override:
cd /opt/khaabo
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --no-deps api alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d worker beat
sleep 5
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api web caddy

# Logs
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f api worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=200 api

# Shell into a running container
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api bash

# psql
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db psql -U khaabo -d khaabo

# Inspect celery queues
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec worker \
    celery -A app.workers.celery_app inspect active
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec worker \
    celery -A app.workers.celery_app inspect reserved
```

---

## 3. Health check

The health endpoint reports each component separately so you can tell degraded
from broken:

```bash
curl -s http://localhost:8000/api/v1/health | python -m json.tool
```

| `status` | meaning | action |
|---|---|---|
| `ok` | All components up | None |
| `degraded` | DB up, Redis or AI down | See which component flagged `ok=false` — degraded is intentional for Redis/AI; the app can serve requests without them |
| API unreachable | Container crashed or can't bind | `docker compose logs --tail=50 api` then section 5 |

The Docker healthcheck (`/api/v1/health`) restarts a container that can't reach
its DB within the retry window (30s × 3). A crash loop means the DB itself is
the problem.

---

## 4. Deploying

### 4.1 Routine deploy (CI-driven)

A push to `main` triggers `.github/workflows/deploy.yml`:

1. Build images → push to GHCR (`:latest` + SHA tag)
2. SCP compose files to the server
3. SSH: pull → run migrations as a one-shot → restart worker/beat → api/web/caddy

Watch the action run in GitHub; on failure it stops and the server keeps the
previous version running (no rollback needed — old containers are still up).

### 4.2 Manual deploy (from the server)

```bash
cd /opt/khaabo
git pull   # only if you edit compose files locally
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --no-deps api \
    alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 4.3 Rolling back

Rollback = run the previous image tag. Because migrations are forward-only and
backward-compatible (additive), rolling the image back one version is safe as
long as no destructive migration ran in the new version.

```bash
# List available image tags
docker images ghcr.io/<owner>/khaabo-backend --format '{{.Tag}}  {{.CreatedAt}}'

# Pin to a previous tag (replace <tag>)
export BACKEND_TAG=<tag>
export FRONTEND_TAG=<tag>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d \
    api worker beat web
```

If a migration must be reversed (rare), write a new **down** migration — never
edit history. Test it on a restore first (section 6).

---

## 5. Incident response

### 5.1 API is 5xxing or down

1. `docker compose logs --tail=100 api` — look for the exception.
2. `curl localhost:8000/api/v1/health` — is the DB up?
3. If the DB is the problem: `docker compose ps db`, then `docker compose restart db`.
4. If the API is the problem but DB is fine: `docker compose restart api`.
5. If restart loops: roll back to the previous image (§4.3).
6. Sentry (if `SENTRY_DSN` is set) will have captured the exception with a stack
   trace and breadcrumbs — check it first for an unknown error.

### 5.2 Celery queue is backing up

1. `celery inspect active` / `inspect reserved` — are workers stuck on a task?
2. If a single task is stuck: `celery inspect revoke <task-id> --terminate`.
3. If the queue is growing because ingestion is slow, the AI provider may be
   down. The heuristic provider is the fallback and never goes down, so this
   only happens with `AI_PROVIDER=ollama` or `openai_compat` and the model
   endpoint unreachable. Either fix the endpoint or set `AI_PROVIDER=heuristic`
   in `.env` and `docker compose up -d worker`.
4. To add capacity temporarily: `docker compose up -d --scale worker=2 worker`.

### 5.3 Disk full

1. `docker system df` — check image/container/volume sizes.
2. Prune old images: `docker image prune -a` (careful — removes unused).
3. Logs: the compose prod overlay caps each service to 3 × 10MB files. If a node
   is filling up from logs, check `/var/lib/docker/containers/*/`.
4. Postgres: the `db_data` volume grows with data. Vacuum:
   ```bash
   docker compose exec db psql -U khaabo -d khaabo -c "VACUUM (ANALYZE, VERBOSE);"
   ```

### 5.4 Cert renewal failure

Caddy renews automatically 30 days before expiry. If it fails:
- Check `docker compose logs caddy` for ACME errors.
- Most common cause: ports 80/443 not reachable from the internet (DNS change,
  firewall, or Cloudflare proxying blocking the HTTP-01 challenge).

---

## 6. Backups & restore

### 6.1 Backup (cron, nightly at 02:00 UTC)

```cron
0 2 * * *  /opt/khaabo/scripts/backup-db.sh >> /var/log/khaabo-backup.log 2>&1
```

The script (`scripts/backup-db.sh`):

- `pg_dump -Fc` (custom format, parallel-restore friendly, compressed)
- Writes to `$BACKUP_DIR` (default `./backups`)
- Syncs to S3 if `S3_BUCKET` is set (via aws-cli or rclone)
- Prunes local copies older than `$BACKUP_RETENTION_DAYS` (default 7)
- Verifies the dump with `pg_restore --list` after writing
- Single-flight: a flock prevents two backups racing

### 6.2 Restore (must test before you need it)

```bash
# Latest backup
./scripts/restore-db.sh --latest

# Specific dump
./scripts/restore-db.sh /path/to/khaabo-20260101T020000Z.dump

# Non-empty target DB (use --force once you've confirmed)
./scripts/restore-db.sh --force /path/to/khaabo-*.dump
```

The restore script:

- Refuses to run on a non-empty DB unless `--force` is passed
- Drops and recreates the target DB
- Reinstalls the `postgis` extension (pg_dump can't fully recreate it)
- `pg_restore` with `--clean --if-exists` in 4 parallel jobs
- Verifies row counts on `dishes`, `restaurants`, `reviews`, `dish_scores`
- Tells you to `alembic upgrade head` after — schema should already match

**Test the restore quarterly** against a scratch DB. A backup you have never
restored is a hope, not a backup.

---

## 7. Secrets

- `.env` is gitignored. The only secret file on the server.
- Production requires: `SECRET_KEY` ≥ 32 chars, real `CONTACT_EMAIL`,
  non-localhost `PUBLIC_BASE_URL`, no wildcard `CORS_ORIGINS`, `AUTH_DEV_BYPASS=false`,
  `DEBUG=false`. The app refuses to start otherwise.
- Rotate `SECRET_KEY` by rolling all services (sessions invalidate — users log in
  again) and Supabase JWT secret from the Supabase dashboard.
- Never put `SUPABASE_JWT_SECRET` (the service key) or `DB_PASSWORD` in the
  frontend image. The frontend only ever sees `SUPABASE_ANON_KEY` (public by design).
- GHCR push uses `GITHUB_TOKEN` (auto-provided). Deploy SSH key is a repo secret
  (`DEPLOY_SSH_KEY`).

---

## 8. On-call checklist (first 30 minutes)

1. **Is it real?** — Check Grafana or `curl /health`. A single 5xx on a cold
   start is not an incident.
2. **What broke?** — `docker compose logs --tail=200 api worker` +
   Sentry. One of them will tell you.
3. **Scope it** — Is it the DB (API degraded), Redis (cache misses, slower),
   or app code (Sentry trace)? The health endpoint split tells you which.
4. **Stabilize** — Restart the failing service. If it crash-loops, roll back.
5. **Communicate** — Post in the ops channel: what broke, what you did, ETA.
6. **Post-mortem** — Within 48h, write what happened, root cause, and the
   specific fix that prevents recurrence. Link the Sentry issue.

---

## 9. Pre-launch checklist

- [ ] `ENV=production` and all prod validators pass (app boots)
- [ ] `SECRET_KEY` rotated, ≥ 32 chars
- [ ] Supabase JWT verification confirmed against a real token
- [ ] `CORS_ORIGINS` set to the real domain
- [ ] `CONTACT_EMAIL` + descriptive User-Agent for Nominatim/Overpass
- [ ] `SENTRY_DSN` set (optional but recommended)
- [ ] Rate limits verified with a load test on `POST /reviews`
- [ ] OSM attribution visible on every map
- [ ] Backups verified by an actual restore into a scratch DB
- [ ] `robots.txt` + `sitemap.xml` generated (see `scripts/generate_sitemap.py`)
- [ ] Moderation queue staffed before user reviews are enabled
- [ ] Caddy auto-TLS confirmed (certificate issued, HTTPS redirects working)
- [ ] Docker logging cap verified (no disk fillunder load)
