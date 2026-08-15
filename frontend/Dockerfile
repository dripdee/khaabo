FROM node:22-alpine AS base
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install


# ── dev: Vite dev server, source bind-mounted by compose ─────────────────────
FROM base AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


# ── build: type-check then bundle ────────────────────────────────────────────
FROM base AS build
COPY . .
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_SUPABASE_URL=
ARG VITE_SUPABASE_ANON_KEY=
ARG VITE_DEFAULT_CITY=kolkata
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_SUPABASE_URL=$VITE_SUPABASE_URL \
    VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY \
    VITE_DEFAULT_CITY=$VITE_DEFAULT_CITY
RUN npm run build


# ── prod: static files behind nginx, SPA fallback for client-side routes ─────
FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
RUN printf 'server {\n\
  listen 80;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
  server_tokens off;\n\
  client_max_body_size 1m;\n\
  gzip on;\n\
  gzip_types text/css application/javascript application/json image/svg+xml;\n\
  # Security headers — also set at the edge in Caddyfile; nginx covers the\n\
  # standalone-image case (e.g. preview deploys without Caddy).\n\
  add_header X-Content-Type-Options "nosniff" always;\n\
  add_header X-Frame-Options "DENY" always;\n\
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n\
  add_header Permissions-Policy "geolocation=(), interest-cohort=()" always;\n\
  location /assets/ { expires 1y; add_header Cache-Control "public, immutable"; }\n\
  location /robots.txt { expires 1h; add_header Cache-Control "public"; }\n\
  location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --spider -q http://localhost/ || exit 1
