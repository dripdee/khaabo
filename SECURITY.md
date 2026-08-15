# Security Policy

## Reporting a vulnerability

Email security@khaabo.in with a description and, if possible, a reproduction.
Do not open a public issue. We respond within 72 hours and credit responsible
disclosure in release notes unless you prefer to remain anonymous.

## Threat model

| Asset | Exposure | Mitigation |
|---|---|---|
| Supabase JWT secret | Backend env only | Never in frontend image or git; rotate via Supabase dashboard |
| DB password | Backend env only | `DB_PASSWORD` required in prod compose; network-isolated to the `db` service |
| User auth tokens | Browser localStorage | Short-lived Supabase JWTs; `AUTH_DEV_BYPASS` forced false in prod |
| Ingestion API keys (Reddit/YouTube) | Backend env only | Read-only scoped keys; in `.env`, gitignored |
| `SECRET_KEY` | Backend env | ≥ 32 chars enforced in prod; rotation invalidates sessions |

## Production enforcement

The app refuses to start in production (`ENV=production`) unless:

- `SECRET_KEY` is set and ≥ 32 characters
- `AUTH_DEV_BYPASS` is false
- `DEBUG` is false
- `CORS_ORIGINS` has no wildcard
- `CONTACT_EMAIL` is a real address (not the dev default)
- `PUBLIC_BASE_URL` is not localhost

This is enforced in `app/core/config.py` at import time, so a misconfigured
deploy fails fast rather than serving insecure traffic.

## Dependencies

- Backend deps are pinned in `requirements.txt`; Dependabot/GitHub Security
  Advisories flag CVEs. The CI workflow runs on every PR so a vulnerable
  transitive dep fails the build before merge.
- The frontend ships no runtime dependencies that handle secrets. Vite injects
  only `VITE_*` env vars, which are public by design (Supabase anon key).
