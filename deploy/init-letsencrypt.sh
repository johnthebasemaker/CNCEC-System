#!/usr/bin/env bash
# ============================================================================
# deploy/init-letsencrypt.sh — ⛔ OBSOLETE under the Cloudflare Tunnel topology.
#
# TLS is now terminated by CLOUDFLARE. The stack publishes no host ports, there
# is no `certbot` service in docker-compose.prod.yml, and nginx serves plain
# HTTP on an internal port only. A Let's Encrypt HTTP-01 challenge cannot even
# reach this box, so this script cannot work — it would fail partway with a
# confusing "no such service: certbot" instead of saying why.
#
# Kept in the tree (rather than deleted) so the history and the alternative
# topology stay discoverable: if you ever move OFF the tunnel and publish
# :80/:443 again, restore the `certbot` service and the TLS server block in
# deploy/nginx.conf, then delete this guard.
#
# Original purpose: seed a throwaway self-signed cert → start nginx → swap in
# the real Let's Encrypt cert (breaking the nginx/certbot chicken-and-egg).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

cat >&2 <<'EOM'
init-letsencrypt.sh is OBSOLETE — this deployment uses a Cloudflare Tunnel and
Cloudflare terminates TLS. There is no certbot service and no public port to
answer an ACME challenge on.

  * Nothing to run: just `docker compose -f docker-compose.prod.yml up -d`.
  * Set TUNNEL_TOKEN in deploy/.env and route the tunnel hostname to http://web:80.

Refusing to run so a half-applied TLS bootstrap can't confuse the deploy.
EOM
exit 1

COMPOSE="docker compose -f docker-compose.prod.yml"

[ -f .env ] || { echo "ERROR: deploy/.env is missing — copy .env.example → .env and fill it in."; exit 1; }
set -a; . ./.env; set +a
: "${DOMAIN:?set DOMAIN in .env}"
: "${LETSENCRYPT_EMAIL:?set LETSENCRYPT_EMAIL in .env}"

cert_path="/etc/letsencrypt/live/$DOMAIN"
staging_arg=""
[ "${LETSENCRYPT_STAGING:-0}" != "0" ] && staging_arg="--staging"

echo "### [1/6] Building images (api + web)…"
$COMPOSE build

echo "### [2/6] Seeding a throwaway self-signed cert for $DOMAIN…"
$COMPOSE run --rm --entrypoint "sh -c \
  'mkdir -p $cert_path && openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
     -keyout $cert_path/privkey.pem -out $cert_path/fullchain.pem -subj /CN=$DOMAIN'" certbot

echo "### [3/6] Starting nginx…"
$COMPOSE up -d web

echo "### [4/6] Removing the throwaway cert…"
$COMPOSE run --rm --entrypoint "rm -rf $cert_path /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

echo "### [5/6] Requesting the real Let's Encrypt certificate…"
$COMPOSE run --rm --entrypoint "certbot certonly --webroot -w /var/www/certbot \
  $staging_arg --email $LETSENCRYPT_EMAIL -d $DOMAIN \
  --rsa-key-size 4096 --agree-tos --no-eff-email --force-renewal" certbot

echo "### [6/6] Reloading nginx with the real cert…"
$COMPOSE exec web nginx -s reload

echo
echo "✅ TLS ready for https://$DOMAIN"
echo "   Now bring up the full stack:  $COMPOSE up -d"
