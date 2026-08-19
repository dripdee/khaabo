# Khaabo — Deploy Workflow Guide

Two independent pipelines, both fully automated — **you deploy by pushing to
`main`, nothing else**:

| Piece | Host | Triggered by | Secrets live |
|---|---|---|---|
| Frontend (SPA) | **Cloudflare Pages** (connected via GitHub) | every push to `main` | Pages dashboard env vars (`VITE_*`) |
| Backend (API + workers) | Oracle VM (GHCR image) | every push to `main` | GitHub Actions + `/opt/khaabo/.env` |

> ⚠️ **Never** run `npm run build` + wrangler/any deploy locally for the
> frontend. Local builds have no `VITE_*` values — deploying one puts up the
> "Auth is not configured" dev-mode site and hides all food items. That is the
> exact breakage we had three times with Workers; Pages' GitHub connection is
> now the only frontend deploy path.

---

## A. Frontend changes (Cloudflare Pages)

**1. Edit** in `frontend/src/`.

**2. Test locally** (optional but recommended):

```powershell
cd C:\hungrykolkata\frontend
npm run dev          # http://localhost:5173, proxies /api to local backend
npm run lint         # must pass
npm run typecheck    # must pass
```

**3. Commit & push:**

```powershell
cd C:\hungrykolkata
git add frontend
git commit -m "feat: your change"
git push origin main
```

**4. Cloudflare Pages builds & deploys automatically** (~2 min). Watch it at
`https://dash.cloudflare.com` → Workers & Pages → **khaabo-in** → Deployments
(gated on GitHub's `CI` workflow passing).

**5. Verify:** hard refresh (Ctrl+Shift+R) khaabo.in. The Pages deployment's
env vars (`VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`,
`VITE_PROXY_TARGET`) are injected at build time; `frontend/public/_redirects`
keeps SPA deep links working.

Where Pages settings live: Pages project → Settings → **Environment variables**
(Production) and Settings → **Build & deployments**.

---

## B. Backend changes

**1. Edit** in `backend/app/`.

**2. If DB models changed** → create a migration (the deploy job runs it):

```powershell
cd C:\hungrykolkata\backend
# with your dev docker stack running (docker compose up):
docker compose exec api alembic revision --autogenerate -m "describe change"
```

Review the file in `backend/alembic/versions/`.

**3. Test locally:**

```powershell
docker compose up
docker compose exec api pytest tests/ -q
```

**4. Commit & push** (include migrations):

```powershell
git add backend
git commit -m "feat: your backend change"
git push origin main
```

**5. CI (`deploy-free.yml`) does the rest** (~3–4 min):

- Builds backend image → GHCR (`latest` + `production` tags)
- SSHes to the VM: `docker compose pull` → pins the exact pushed image →
  `alembic upgrade head` → `up -d --force-recreate` for worker/beat/api →
  verifies every container runs the pushed image digest → polls the api
  container's healthcheck until `healthy` → fails loudly on any mismatch

The *Deploy to Oracle VM* job uses the `production` environment — if
"Required reviewers" is set, approve it in the Actions tab.

**6. Verify:**

```powershell
curl https://api.khaabo.in/api/v1/health
ssh -i ~/.ssh/khaabo-deploy.key ubuntu@130.210.0.124 "docker logs khaabo-api-1 --tail=50"
```

---

## C. New configuration variables

| Where | What to do |
|---|---|
| Frontend build-time (`VITE_*`) | Pages dashboard → khaabo-in → Settings → Environment variables → add for **Production** → "Retry deployment" (or next push) applies it |
| Backend runtime | SSH VM → edit `/opt/khaabo/.env` → `docker compose -f docker-compose.yml -f docker-compose.free.yml up -d` (recreates containers to pick up env) |

Also: if you add a brand-new `VITE_*` consumed by code, update the Pages
`Build & deployments` list too — Pages only injects env vars that are listed
there.

---

## D. Rollback

- **Frontend:** Pages keeps every deployment → Dash → khaabo-in → Deployments →
  **Rollback** on any earlier deployment. One click, instant.
- **Backend:** `git revert` the bad commit + push (CI rebuilds the previous
  state and redeploys). Images are SHA-tagged, so pinning a previous tag in
  `/opt/khaabo/.env` (`API_IMAGE=...`) + `up -d` also works.

---

## E. If the frontend is ever broken again ("Auth is not configured" / no food)

The only possible cause is a build missing `VITE_*` values. Fix: Dash →
khaabo-in → Deployments → **Retry deployment** (or roll back to the last good
one). Do NOT touch wrangler/Workers for this project.

---

## F. Everyday summary

```
edit → test locally → git push origin main:
  • Cloudflare Pages rebuilds the SPA (~2 min)
  • GitHub Actions rebuilds + rolls the backend (~3–4 min)
refresh the site
```

---

## Reference: where things live

| Thing | Location |
|---|---|
| Frontend source | `C:\hungrykolkata\frontend\src` |
| Backend source | `C:\hungrykolkata\backend\app` |
| Backend deploy workflow | `C:\hungrykolkata\.github\workflows\deploy-free.yml` |
| Frontend hosting | Cloudflare Pages project **khaabo-in** (GitHub-connected) |
| Frontend env vars | Pages dash → khaabo-in → Settings → Environment variables |
| SPA routing | `frontend/public/_redirects`, headers in `frontend/public/_headers` |
| VM app directory | `/opt/khaabo` (SSH: `ubuntu@130.210.0.124`) |
| Production secrets (VM) | `/opt/khaabo/.env` (`PAGES_URL`, `CORS_ORIGINS` must contain the Pages domain after the Pages project is live) |
| Backend repo secrets | GitHub → Settings → Environments → `production` |
| Nightly backups | `/opt/khaabo/backups/` (cron 02:00, log `/var/log/khaabo-backup.log`) |
