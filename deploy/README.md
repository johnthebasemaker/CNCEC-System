# deploy/ — new-stack (React + FastAPI + PostgreSQL) production kit

Turnkey Docker deployment for the **new** stack. Separate from the Streamlit app
(repo-root `docker-compose.yml`) — they don't interfere.

**Full runbook: [`../docs/DEPLOY.md`](../docs/DEPLOY.md).**

Quick start (on the server; ingress is a Cloudflare Tunnel, so no DNS A record
and no open inbound ports are needed):
```bash
cp .env.example .env      # DOMAIN, JWT_SECRET, POSTGRES_PASSWORD, PUBLIC_BASE_URL, TUNNEL_TOKEN, …
chmod 600 .env            # it holds live credentials
docker compose -f docker-compose.prod.yml up -d
# then do the one-time SQLite→Postgres data migration — see the runbook §4
```

Services: `db` (Postgres 16) · `api` (FastAPI, internal) · `web` (nginx: SPA +
`/api` proxy, plain HTTP, internal) · `cloudflared` (the tunnel — the ONLY
ingress) · `backup` (nightly pg_dump). **No service binds a host port**;
Cloudflare terminates TLS and enforces Zero Trust Access. There is no `certbot`,
and `init-letsencrypt.sh` refuses to run.

Route the tunnel's public hostname to **`http://web:80`** — the compose service
name, not `localhost` (cloudflared is in its own container).

Nothing here has been run against a server — provision the box and go when ready.
`deploy/.env` holds secrets and is gitignored; never commit it.
