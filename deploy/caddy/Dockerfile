FROM caddy:2.8-alpine

# Default to the production Caddyfile (single-host). The free-tier overlay
# passes CADDYFILE_NAME=Caddyfile.free so Caddy only serves the API subdomain.
ARG CADDYFILE_NAME=Caddyfile
COPY deploy/caddy/${CADDYFILE_NAME} /etc/caddy/Caddyfile

EXPOSE 80 443 443/udp
