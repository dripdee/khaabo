# Khaabo — Zero-cost Production Deploy

Every service below has a permanent free tier. No credit card costs, no trial
expiry. Total monthly bill: **$0**.

```
                                          ┌──────────────────────┐
   you ──→  Cloudflare DNS + CDN  ───────▶│  Cloudflare Pages     │  SPA
   https://khaabo.in                      │  khaabo.pages.dev     │  (build+host free)
                                          └──────────────────────┘
                                                    │
                                                    │ fetch (CORS)
                                                    ▼
   internet ──→  Oracle Cloud Always Free VM        ┌──────────────────────┐
   https://api.khaabo.in  (Caddy :443 auto-TLS) ──▶│ api  (uvicorn ×1)    │
                                                   │ worker (celery)      │
                                                   │ beat   (celery beat) │
                                                   │ caddy  (edge proxy)  │
                                                   └─────────┬──────┬─────┘
                                                       TLS  │      │ TLS
                                          ┌──────────────────▼┐ ┌──▼──────────────┐
                                          │ Supabase free     │ │ Upstash free   │
                                          │ Postgres + PostGIS│ │ Redis (TLS)    │
                                          └───────────────────┘ └────────────────┘
```

| Component | Free tier | Why this one |
|---|---|---|
| VM | Oracle Cloud Always Free (1 OCPU, 1 GB RAM, 50 GB disk) | Only free tier that allows Docker + a long-running process. 365+ trial credit card required for verification but no charge |
| Postgres + PostGIS | Supabase free (500 MB DB, PostGIS preinstalled, 2 GB egress/month) | Only managed Postgres free tier with PostGIS — essential for our geo queries |
| Redis | Upstash free (10,000 commands/day, TLS, pay-as-you-go after) | Managed Redis with TLS; Celery only needs it as a broker + cache, not for data |
| SPA | Cloudflare Pages (unlimited bandwidth, 500 builds/month) | Frontend + CDN + free TLS + custom domain |
| Container registry | GHCR (free for public packages) | The deploy workflow pushes here; Oracle VM pulls |
| Auth | Supabase free (50,000 monthly active users) | Backend verifies Supabase JWTs |
| Secrets | GitHub Actions secrets (free) | The deploy workflow reads them at runtime |
| Error tracking | Sentry developer plan (5,000 errors/month free) | Optional |
| Domain | Any ~$10/yr `.com`, or free with a Freenom alternative / `.pages.dev` | Domain itself is the only thing that isn't free |

---

## 0. Prerequisites — what you need before you start

1. **A domain name.** Cheapest option: `.xyz` or `.click` at ~$1/yr, or use
   `khaabo.pages.dev` + `api.khaabo.pages.dev` for a zero-cost subdomain. We'll
   assume `khaabo.in` below — substitute your domain everywhere.
2. **A GitHub account** (free).
3. **A Cloudflare account** (free) — for DNS + Pages.
4. **A Google account** (free) — for YouTube Data API, optional.
5. **A Reddit account** (free) — optional, for ingestion.

This guide assumes `khaabo.in` (apex) for the SPA and `api.khaabo.in` for the
API. Adapt to your domain.

---

## 1. Set up Supabase (Postgres + PostGIS + Auth)

1. Sign up at https://supabase.com → create a **new project**. Pick a strong DB
   password and save it — call it `SUPABASE_DB_PASSWORD`.
2. Wait for provisioning (~2 min). Open **Project Settings → Database** and copy:
   - **Connection string → Transaction pooler** → URL looks like
     `postgresql://postgres.xxxx:PASSWORD@aws-0-region.pooler.supabase.com:6543/postgres`
     This is your `DATABASE_URL`. Strip `postgresql://` and re-add the
     `+psycopg` driver: `postgresql+psycopg://postgres.xxxx:...`.
   - **Connection string → Session pooler** (port 5432) → use as `SYNC_DATABASE_URL`
     for Alembic migrations (transactions-mode pooling breaks DDL).
3. **Project Settings → API:**
   - `Project URL` = `SUPABASE_URL` = `VITE_SUPABASE_URL`
   - `anon public` key = `SUPABASE_ANON_KEY` = `VITE_SUPABASE_ANON_KEY`
   - `JWT secret` = `SUPABASE_JWT_SECRET` (do NOT put in frontend; never expose)
4. Install the PostGIS extension (free on Supabase):

   ```sql
   -- run this in the Supabase SQL Editor (Dashboard → SQL Editor → New query)
   create extension if not exists postgis;
   create extension if not exists postgis_topology;
   ```

   You only need to do this once. The migration that ships with Khaabo also
   includes it, but Supabase's managed layer may need it preinstalled for
   `create extension` in a later migration to succeed.

---

## 2. Set up Upstash (Redis with TLS)

1. Sign up at https://upstash.com → **Create Database**. Pick a region close to
   your Oracle VM (we'll get the VM in §5; pick the same region now).
2. Copy the **UPSTASH_REDIS_URL** — it starts with `rediss://` (double s = TLS).
   This is your `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`.
3. Upstash's free tier allows 10,000 commands/day — more than enough for a
   low-traffic launch. Celery only uses Redis as a broker + cache, not as data.

---

## 3. Set up Cloudflare Pages (frontend)

### 3.1 Push the repo to GitHub
First: commit and push the entire `C:\hungrykolkata` repo to a public GitHub
repository. The deploy workflow and Cloudflare Pages both need the code there.

### 3.2 Connect Cloudflare Pages
1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
2. Select your `khaabo` repo. If your repo is private, Pages will need read
   access — authorize it.
3. Configure the build:
   - **Framework preset:** None
   - **Build command:** `cd frontend && npm ci && npm run build`
   - **Build output directory:** `frontend/dist`
   - **Root directory:** `/` (leave blank)
4. **Settings → Environment variables** (set these before the first build):
   - `VITE_API_BASE_URL` = `https://api.khaabo.in/api/v1`
   - `VITE_SUPABASE_URL` = your Supabase Project URL
   - `VITE_SUPABASE_ANON_KEY` = your Supabase anon public key
5. Save and Deploy. First build should take ~1 min. You'll get a URL like
   `khaabo-xyz.pages.dev` — bookmark it for the next step.

### 3.3 Custom domain
1. Pages → **Custom domains → Set up a custom domain** → `khaabo.in`.
2. Cloudflare adds the DNS records automatically (or tells you to). Wait for
   the certificate to issue (~1 min). The SPA is live.

---

## 4. Set up DNS in Cloudflare

This is the only place both DNS records live. Cloudflare's free DNS has no
limit on records.

| Type | Name | Value | Proxy | Purpose |
|---|---|---|---|---|
| CNAME | `khaabo.in` (apex) | `khaabo-xyz.pages.dev` | Proxied ✓ | SPA via Pages |
| A | `api` | `<Oracle VM IP from §5>` | DNS only (gray cloud) | API through Caddy's auto-TLS |

**Why `api` is DNS-only:** Caddy needs an HTTP-01 challenge from Let's Encrypt
to reach port 80 on the VM. Cloudflare's proxy blocks that, so we bypass it for
the API subdomain. The SPA stays proxied — it benefits from the CDN + free WAF.

---

## 5. Provision the Oracle Cloud Always Free VM

### 5.1 Create the instance
1. Sign up at https://cloud.oracle.com → verify with a credit card (no charge).
2. **Compute → Create Instance**:
   - Image: **Canonical Ubuntu 22.04**
   - Shape: **VM.Standard.A1.Flex** — set to 1 OCPU, 1 GB RAM (free per Always Free).
     *If the A1 is unavailable in your region, try AMD `VM.Standard.E2.1.Micro`
     (also 1 GB, free) — but it's slower; A1 (ARM) is better.*
   - SSH keys: **save the private key** locally — this is `DEPLOY_SSH_KEY`.
3. Wait for the instance to come up (~2 min). Note the **public IP**.
4. In the OCI security list / VCN: ensure ports 22, 80, 443 are open inbound.
5. Copy the public IP into Cloudflare (§4, the `api` A record).

### 5.2 Bootstrap the VM
SSH in once with your private key:

```bash
ssh -i ~/.ssh/khaabo-deploy.key ubuntu@<VM-IP>
git clone https://github.com/<you>/khaabo.git /opt/khaabo
cd /opt/khaabo
sudo bash scripts/prod-bootstrap.sh
```

This installs Docker, sets up a firewall, hardens SSH, and adds 1 GB swap so
Celery's memory spikes don't OOM anything. Takes ~5 min.

### 5.3 Configure secrets on the VM
Still SSH'd in:

```bash
cd /opt/khaabo
cp .env.example .env
nano .env       # or: vi .env
```

Fill in (lines without `…` are literal):

```ini
# ── core
ENV=production
DEBUG=false
SECRET_KEY=<run: openssl rand -hex 32, paste here>
PUBLIC_BASE_URL=https://api.khaabo.in
CORS_ORIGINS=https://khaabo.in,https://khaabo-xyz.pages.dev
CONTACT_EMAIL=you@khaabo.in
DOMAIN=api.khaabo.in
PAGES_URL=https://khaabo.in
DEFAULT_CITY_SLUG=kolkata
DEFAULT_CITY=kolkata

# ── database (Supabase) — Session pooler (port 5432) for Alembic
DATABASE_URL=postgresql+psycopg://postgres.xxxx:PASSWORD@aws-0-region.pooler.supabase.com:5432/postgres
SYNC_DATABASE_URL=postgresql+psycopg://postgres.xxxx:PASSWORD@aws-0-region.pooler.supabase.com:5432/postgres

# ── redis (Upstash, TLS)
REDIS_URL=rediss://default:PASSWORD@us1-xxx.upstash.io:6379
CELERY_BROKER_URL=rediss://default:PASSWORD@us1-xxx.upstash.io:6379
CELERY_RESULT_BACKEND=rediss://default:PASSWORD@us1-xxx.upstash.io:6379

# ── supabase auth
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_JWT_SECRET=...      # Project Settings → API → JWT Secret

# ── optional
AI_PROVIDER=heuristic
SOURCES_ENABLED=osm,user
SENTRY_DSN=
```

**Important:** Don't use the transaction pooler URL (port 6543) for migrations.
Alembic uses `SYNC_DATABASE_URL` and DDL over a transaction-mode pooler fails.

### 5.4 First deploy (manual)

```bash
cd /opt/khaabo
docker compose -f docker-compose.yml -f docker-compose.free.yml up -d
```

Verify — the migration runs automatically as part of the API container startup.
Wait a couple of minutes for Caddy to fetch its TLS certificate, then:

```bash
# health (should return {"status":"ok",...} or "degraded" without Redis errors)
curl https://api.khaabo.in/api/v1/health

# docs
open https://api.khaabo.in/docs
```

Then seed the DB (one-time):

```bash
docker compose -f docker-compose.yml -f docker-compose.free.yml exec api \
    python -m scripts.seed --city kolkata

# optional: demo data for a populated UI
docker compose -f docker-compose.yml -f docker-compose.free.yml exec api \
    python -m scripts.seed_demo

# generate sitemap
docker compose -f docker-compose.yml -f docker-compose.free.yml exec api \
    python -m scripts.generate_sitemap --base-url https://khaabo.in \
    --output /app/../frontend/public/sitemap.xml
# (then copy sitemap.xml out of the container and commit it to /frontend/public/)
```

---

## 6. Set up GitHub Actions for automated deploys

The repo has `.github/workflows/deploy-free.yml`. To activate it:

**Repo → Settings → Secrets and variables → Actions → New repository secret:**

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | Oracle VM public IP |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | the **private** SSH key (PEM format, start with `-----BEGIN...`) |
| `GHCR_TOKEN` | a GitHub PAT with `read:packages` scope (Settings → Developer settings → PAT) |

Then create a `production` environment: **Settings → Environments → New
environment → "production"**. No protection rules required for the free-tier
flow — but you may want "Required reviewers" set to yourself to gate deploys.

Now every push to `main`:

1. Builds the backend image, tags it `latest` + SHA, pushes to GHCR.
2. SCPs the compose files to `/opt/khaabo` on the VM.
3. SSH: log into GHCR on the VM with `GHCR_TOKEN`, pull, run migrations as a
   one-shot, restart services, smoke-test `/api/v1/health`.

Cloudflare Pages watches the same repo and redeploys the SPA independently on
every push to `main`. Both pipelines are triggered by the same commit, so a
merge auto-roll both ends.

---

## 7. Set up cron jobs (on the VM)

Edit crontab as the ubuntu user:

```bash
crontab -e
```

Add:

```cron
# nightly DB backup to a local dir (object storage sync is optional)
0 2 * * *  cd /opt/khaabo && DATABASE_URL="$(grep ^DATABASE_URL .env | cut -d= -f2-)" BACKUP_DIR=/opt/khaabo/backups ./scripts/backup-db.sh >> /var/log/khaabo-backup.log 2>&1

# rotate the backup log so it doesn't grow forever
0 3 * * *  /usr/sbin/logrotate -s /tmp/logrotate-state /etc/logrotate.d/khaabo 2>/dev/null
```

Create `/etc/logrotate.d/khaabo`:

```
/var/log/khaabo-backup.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
```

Test a backup immediately:

```bash
cd /opt/khaabo && ./scripts/backup-db.sh
ls -la backups/
./scripts/restore-db.sh --force backups/khaabo-*.dump   # on a scratch DB!
```

**Restore on the production DB is destructive — only test on a clone.**

---

## 8. Verify: the end-to-end user flow

| Step | Test | Expected |
|---|---|---|
| 1. SPA loads | `https://khaabo.in` | Khaabo home with "What should I eat, and where?" |
| 2. API health | `https://api.khaabo.in/api/v1/health` | `{"status":"ok"}`, db ok, redis ok |
| 3. Search | `https://api.khaabo.in/api/v1/search?q=biryani` | JSON: dishes array (after seeding) |
| 4. SPA calls API | On the SPA, type "bir" in the search box | Live suggestions (network tab: 200) |
| 5. Worker active | `make -C /opt/khaabo free-logs` (or `docker logs worker`) | Logs showing beat schedules firing |
| 6. Trends page | `https://khaabo.in/trending` | Empty state until enough reviews exist |
| 7. Submit review (auth required) | `https://khaabo.in/submit-review` | Supabase login → form |
| 8. Origins | `curl -I https://khaabo.in/` | `x-content-type-options: nosniff`, etc. |
| 9. TLS cert | `curl -vI https://api.khaabo.in 2>&1 \| grep -i 'expire'` | Expiry ~90 days out, auto-renews |

If any item fails, see `docs/runbook.md` §5 (incident response).

---

## 9. What's free forever vs. "free for now"

| Service | Always free? | Notes |
|---|---|---|
| Cloudflare Pages / DNS | ✓ | unlimited bandwidth, no expiry |
| Oracle Cloud Always Free | ✓ | you can lose it if the instance idles >7 days; see §10 |
| Supabase free | ✓ | 500 MB DB, 50k MAU; no expiry |
| Upstash free | ✓ | 10k commands/day, refreshes daily; no expiry |
| GHCR | ✓ | unlimited storage for public packages |
| Sentry dev | ✓ | 5k errors/month; resets |

The only genuine risk is **Oracle deactivating an idle A1 instance**. Mitigation
in §10.

---

## 10. Keeping the Oracle VM "alive"

Oracle reclaims Always Free A1 instances idle for 7+ days. Solve this by
having a recurring task that keeps CPU busy briefly:

```bash
# on the VM
crontab -e
```

Append:

```cron
# keep-alive: a 30s CPU spike every 4h so Oracle's idle detector never trips
0 */4 * * *  stress-ng --cpu 1 --timeout 30s 2>/dev/null || dd if=/dev/zero of=/dev/null bs=1M count=200 2>/dev/null
```

If `stress-ng` isn't installed: `sudo apt install -y stress-ng`.

---

## 11. Disaster scenarios & recovery

- **VM dies / Oracle reclaims it**: Re-create the VM, run `prod-bootstrap.sh`,
  `git clone`, restore `.env`, restore the DB from the latest backup,
  `docker compose -f ... -f docker-compose.free.yml up -d`. DNS just needs the
  new IP in the `api` A record.
- **Supabase goes down**: Depends on the free Supabase SLA. Mitigation: backups
  are nightly; you can restore to any Postgres+PostGIS. Switch `DATABASE_URL`
  to the new endpoint and restart.
- **Upstash exhausts daily commands**: Backend falls back to no cache; Celery
  jobs block briefly. Set `CACHE_ENABLED=false` temporarily to drop cache
  pressure.
- **Cloudflare Pages build fails**: The site keeps serving the last successful
  build — no downtime. Look at the build log, fix, push again.

---

## 12. Going beyond free-tier later

When you outgrow free tiers (most likely Supabase 500 MB or Upstash 10k
commands/day):

1. Add resource: Supabase Pro ($25/mo) for 8 GB, or self-host Postgres on the VM
   (the full `docker-compose.prod.yml` already supports local Postgres — just
   swap the free overlay for the prod overlay).
2. The architecture doesn't change — only `.env` does.

---

## 13. The cost summary (final check)

| Item | Monthly |
|---|---|
| VM (Oracle Always Free) | $0 |
| Postgres + PostGIS (Supabase free) | $0 |
| Redis (Upstash free) | $0 |
| Frontend + CDN (Cloudflare Pages free) | $0 |
| Container registry (GHCR public) | $0 |
| Auth (Supabase free) | $0 |
| Error tracking (Sentry dev) | $0 |
| Domain (optional) | ~$0.10 (amortized) — or $0 with `.pages.dev` |
| **Total** | **$0** |
