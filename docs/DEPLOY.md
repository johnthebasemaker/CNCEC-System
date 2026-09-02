# Deploying the new stack (React + FastAPI + PostgreSQL)

> ⏸️ **PAUSED by decision (2026-07-30).** Phase 3 (Hetzner Ubuntu +
> Docker) is deferred until *Feature Fine-Tuning and UI Polish* is
> complete. Nothing here is blocked — the kit is built, documented and
> ready. Current state + locked rules:
> [`PROJECT_HANDOVER.md`](../PROJECT_HANDOVER.md).

Turnkey deploy for the **new** React/FastAPI/Postgres stack — the feature-complete
app built in the 2026-07 session. This is a *separate* deployment from the
Streamlit app (repo-root `docker-compose.yml`); the two do not interfere.

Everything lives in [`deploy/`](../deploy/):

| File | What it is |
|---|---|
| `docker-compose.prod.yml` | db (Postgres 16) · api (FastAPI) · web (nginx: SPA + `/api` proxy) · **cloudflared** (the only ingress) · **backup** (nightly pg_dump) |
| `Dockerfile.api` | FastAPI image (uvicorn, 4 workers, `GI_ENV=production`) |
| `Dockerfile.web` | multi-stage: builds the Vite bundle → nginx serves it |
| `nginx.conf` | SPA fallback + `/api/`→api proxy (strips prefix), plain HTTP on an internal `:80` |
| `init-letsencrypt.sh` | ⛔ obsolete — refuses to run (Cloudflare terminates TLS) |
| `.env.example` | secrets template → copy to `deploy/.env` (gitignored) |
| `backup/backup-pg.sh` | nightly `pg_dump -Fc` + 14-day retention + `.last_success`/`.last_failure` markers |
| `deploy-v2.sh` · `health-check.sh` · `rollback.sh` | server-side manual-deploy orchestrator + health gate + automatic rollback (see §9) |

> ⚠️ **Nothing here has been run against a server.** It's a kit. Provision the
> box and run it when you're ready.

---

## 0. Topology — Cloudflare Tunnel, zero open ports

Ingress is a **Cloudflare Tunnel**. `cloudflared` dials *out* to Cloudflare's
edge and receives proxied requests over that connection, so the box never
listens on the public internet:

```
client → Cloudflare edge (TLS + Zero Trust Access) → tunnel → cloudflared → web:80 → api:8000
```

Consequences, all of them deliberate:

- **No service publishes a host port** — not even nginx. Publishing one would
  expose the origin directly and let anyone with the server IP bypass Access.
- **Cloudflare terminates TLS.** There is no `certbot` service, no ACME
  challenge, and no TLS in `nginx.conf`. `init-letsencrypt.sh` is obsolete and
  refuses to run.
- **DNS is a CNAME to the tunnel**, not an A record to the box.
- The v1 Streamlit stack can keep holding `:80`/`:443` — the two no longer
  contend, so the cutover is a *tunnel route* change, not a port handover.

### Prerequisites
- A Linux VPS (the parked **Hetzner CPX42** per the workstream-C decisions), Ubuntu 22.04+.
- **Docker Engine + Compose v2** installed (`docker --version`, `docker compose version`).
- A **Cloudflare Tunnel** created in Zero Trust → Networks → Tunnels, with its
  connector token to hand, and its public hostname routed to **`http://web:80`**
  (the compose service name — *not* `localhost`; cloudflared runs in its own
  container). Do not add a separate `/api` route: nginx already strips that prefix.
- Firewall: **deny all inbound.** Only outbound 443 to Cloudflare is required.
- A copy of the live **`gi_database.db`** (for the one-time data migration).

> The Zero Trust Access policy protects the HTML portal. Keep the documented
> **Bypass (Everyone)** policy on `/api/*` — the native apps and the WhatsApp
> report links have no Access session (see `docs/NATIVE_APPS.md` §6).

## 1. Get the code + configure secrets
```bash
git clone https://github.com/johnthebasemaker/GI_Hub_Project.git gihub && cd gihub/deploy
cp .env.example .env
# Fill in .env:
#   DOMAIN, LETSENCRYPT_EMAIL
#   POSTGRES_PASSWORD   →  openssl rand -base64 32   (alphanumeric only — a
#                           '@' or '/' would corrupt the DATABASE_URL compose builds)
#   JWT_SECRET          →  openssl rand -hex 32   (MANDATORY, >=32 chars)
#   PUBLIC_BASE_URL     →  https://<DOMAIN>/api   (MANDATORY — note the /api)
#   CORS_ORIGINS        →  native app origins, or blank for browser-only
#   GI_TRUSTED_PROXIES  →  *                      (see the table below)
#   TUNNEL_TOKEN        →  Zero Trust → Networks → Tunnels → Install connector
nano .env
chmod 600 .env          # holds live Meta/SMTP credentials — owner-only
```
`GI_ENV=production` is set by compose, so the API **refuses to boot** unless both
of these hold — that's intentional, and both are startup errors rather than
silent degradation:

- **`JWT_SECRET`** is ≥32 chars and is not one of the published placeholder /
  CI values (the documented gate key `ci-only-…` is explicitly refused).
- **`PUBLIC_BASE_URL`** is set and does not point at localhost. It builds the
  72-hour tokenized weekly-report links sent over WhatsApp; pointing at
  localhost means the recipient's own device resolves the link and a 256-bit
  capability token was broadcast for nothing.

Two more production-only settings that are easy to miss:

| Variable | Why it matters |
|---|---|
| **`CORS_ORIGINS`** | In production an unset value now means **no cross-origin access at all** (it used to fall back to a dev list containing `http://localhost` as a *credentialed* origin). Browser-only deployments behind nginx can leave it blank. The **Tauri/Capacitor apps call the API cross-origin**, so their fixed webview origins must be listed or every native call fails: `tauri://localhost,http://tauri.localhost,https://tauri.localhost,capacitor://localhost,https://localhost` |
| **`GI_TRUSTED_PROXIES`** | The rate limiter keys on `CF-Connecting-IP` / `X-Real-IP`, which are attacker-supplied on any request reaching the origin *directly*. Under this topology nothing can: no host port is published, so the only path is edge → cloudflared → nginx → api. Set **`*`** ("trust any peer") — nginx forwards the real client address as `CF-Connecting-IP`. ⚠️ Do **not** pin a specific container IP: it changes on every recreate, and a non-matching value makes every request key on the proxy's own address, so all users share ONE bucket and `/auth/login` locks out globally at 10/min. If you ever publish a host port again, narrow this to the real peer — `*` would then let anyone hitting the origin spoof their key. |
| **`TUNNEL_TOKEN`** | The cloudflared connector token (Zero Trust → Networks → Tunnels → Install connector). Compose **fails fast** if it is unset, since without it there is no ingress at all. Treat it as a credential: `deploy/.env` stays `chmod 600` and gitignored. |

Everything in `deploy/.env` reaches the container via the api service's
`env_file:` — if you add a new variable, it is passed automatically.

### 1a. Phase 9 / Phase 10 settings

None of these is required — every one has a working default — but three of them
change behaviour a person will notice.

| Variable | Default | Why you might set it |
|---|---|---|
| `GI_AI_VISION_TIMEOUT_S` | `900` | How long a vision OCR read may take. Sized from measurement, not guesswork: a full-page form takes 269–444 s and a 30-row consumption log 361 s on the reference box. **Lowering this below ~600 reintroduces the ReadTimeout Phase 9 fixed.** |
| `GI_AI_VISION_NUM_CTX` / `_MAX` | `8192` / `16384` | The model's context window. ⚠️ **Never raise a per-lane `num_predict` without this** — Ollama runs the model at 4,096 by default and an over-budget request ABORTS the runner (`ggml_abort`), taking every queued job with it. See ARCHITECTURE §7a. |
| `GI_AI_ORPHAN_STALE_S` | `180` | How long a job may go without a heartbeat before the sweep reaps it (§7a-ii). It is five missed beats, **not** the job timeout — sizing it off job duration means waiting 15 minutes to reap a corpse. |
| `GI_SUPPLIER_CHASE_TO` | *(unset)* | E.164 number for the day-shift MTC supplier chase. ⚠️ Unset means **no supplier draft is created at all**; set, it writes a WhatsApp **DRAFT** that a human must release in the admin Console. It never sends automatically. |
| `GI_BRIEFING_HOUR` / `_MINUTE` | `7` / `0` | When the morning briefing (and the day-shift MTC chase that rides it) runs. |
| `GI_REDIS_URL` | — | **Does not exist, deliberately.** Ruling P10-1: the shared limiters are Postgres-backed (`rate_buckets`). Do not add Redis without revisiting that ruling. |

⚠️ **Two settings are rows in `app_settings`, not environment variables**, and
both were made that way so widening or delaying them is an admin action rather
than a deploy: `mfa_required_roles` (default `admin,logistics,hod,qc_hod,auditor`)
and `mfa_enforced_from` (an ISO date; **absent means warn-only, never block**).
Set the date to **14 days after go-live** to give people the grace period the
manual promises them (§24.1.1).

⚠️ **`uvicorn --workers 4` is load-bearing in a way that bites.** Three
production bugs in Phase 10 came from state that was per-process: the OCR
orphan sweep, the daily schedulers and four rate limiters. If you add anything
that runs on a timer or keeps a counter in memory, **assume four of it** and
give it a claim (`services/dailyjob.py`) or a shared row (`rate_buckets`).

## 2. TLS — nothing to do
Cloudflare terminates TLS at the edge, so there is no certificate to issue,
install or renew on the box. `init-letsencrypt.sh` is kept only for the
alternative (port-publishing) topology and refuses to run as-is.

The one thing to verify is the **tunnel route**: Zero Trust → Networks →
Tunnels → your tunnel → Public hostname → `gi.giinventory.com` →
`http://web:80`. If it points at `localhost`, cloudflared resolves that inside
its own container and every request 502s.

## 3. Bring the stack up
```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps      # all healthy?
```
At this point `https://DOMAIN` serves the SPA, but Postgres is **empty** — do the
data migration next.

## 4. One-time data migration — SQLite → PostgreSQL
This makes Postgres the system of record. It uses the already-proven
`backend/dual_ci.py` (migrates all 64 tables + asserts parity).

> 🛑 **`dual_ci` WIPES and re-copies the target Postgres.** Run it ONLY for the
> initial cutover (or a pre-go-live re-sync). **Never** run it again after users
> start writing to production — it would erase their data.

Copy `gi_database.db` to `deploy/` on the server, then (note the **psycopg2** URL —
dual_ci is synchronous; substitute your `.env` password/user/db):
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  -v "$(pwd)/gi_database.db:/data/gi_database.db:ro" \
  api python backend/dual_ci.py --source /data/gi_database.db
# expect:  == DUAL-CI: ✅ PASS ==   (64/64 tables, identity-math parity)
```
Then hand the schema over to Alembic for future changes — `dual_ci` already
created the tables, so **stamp** the baseline (don't upgrade):
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  api alembic -c backend/alembic.ini stamp head
```
After this, schema changes go through Alembic (`backend/alembic/README.md`).

## 5. Verify
```bash
curl -s https://DOMAIN/api/health          # {"status":"ok","dialect":"postgresql",...}
```
Then in a browser: `https://DOMAIN` → sign in (`admin` / your migrated password) →
click through Dashboard, Stock, Reports (download an Excel), Admin → Users.
Optional smoke test from inside the api container:
```bash
docker compose -f docker-compose.prod.yml run --rm \
  -e DATABASE_URL=postgresql+psycopg2://gihub:YOUR_PG_PASSWORD@db:5432/gihub \
  -e JWT_SECRET="$(grep ^JWT_SECRET .env | cut -d= -f2)" \
  api python -m backend.api.service_tests      # 386/386
```

## 6. Cutover decision (making React primary)
When you're satisfied:
1. **Freeze** Streamlit writes (put it in maintenance mode, or take it offline).
2. Re-run the **step-4 migration** one last time to catch any writes since the first run.
3. Point users at `https://DOMAIN`.
4. Retire Streamlit, or keep it running **read-only** as a fallback for a transition.

**Rollback is trivial** while you're deciding: the Streamlit app + `gi_database.db`
are completely untouched — if anything's wrong, send users back to Streamlit.

## 7. Operations
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f api` (or `web`, `db`).
- **Restart / update:** `git pull && docker compose -f docker-compose.prod.yml up -d --build`.
- **TLS renewal:** automatic (the `certbot` service). Force: `docker compose -f docker-compose.prod.yml run --rm certbot renew`.
- **Backups (automated):** the `backup` service runs `deploy/backup/backup-pg.sh`
  nightly at **02:00 Asia/Riyadh** — `pg_dump -Fc` (custom format) into the
  `pg-backups` volume, **14-day retention**, writing `.last_success`/`.last_failure`
  markers (same convention as the v1 SQLite backup, so the Admin **Service Health**
  card reads them unchanged). The console's manual **Admin → Backup** button
  (`POST /admin/backup`) writes to the **same** volume, so manual and nightly dumps
  live together. Run one on demand:
  ```bash
  docker compose -f docker-compose.prod.yml exec backup /bin/sh /usr/local/bin/backup-pg.sh
  ```
  Restore a dump:
  ```bash
  docker compose -f docker-compose.prod.yml exec -T db \
    pg_restore -U gihub -d gihub -c < gihub-<stamp>.dump
  ```
  ⚠️ **Off-box before go-live:** the `pg-backups` volume is on the same VPS disk.
  Two options to survive a total-VPS-loss:
    - **S3 (recommended):** set `AWS_S3_BUCKET` + IAM creds in `deploy/.env` —
      `backup-pg.sh` then pushes each dump to S3 (SSE-encrypted). Retention is a
      bucket **lifecycle policy** (e.g. 30d → Glacier, 90d → expire). Use a
      dedicated IAM user scoped to put/list on that bucket. Restore: `aws s3 cp`
      the dump back, then `pg_restore` as above.
    - **Hetzner Storage Box:** bind the `pg-backups` volume to it (the compose
      `volumes:` block has the CIFS stub).

## 8. What this does NOT include (confirm before go-live)
Not ported to the new stack (Streamlit-only): WhatsApp, email/mailer. The local-LLM
(Ollama) Intelligence layer (Q&A, OCR, NL→SQL, CV) **is** in the new stack (the
`ollama` service). Reads **are** site-scoped as of 2026-07-05 (below level 3, reads
pin to the user's own `Site_ID`; policy in `backend/api/auth.py: site_scope()` /
`resolve_site_param()`). Remaining pre-cutover item: the **WhatsApp/email outbox
(Phase 7)**, on hold for the Meta permanent token. Address it before day one if
outbound messaging matters at launch.

## 9. Automated deploy + rollback (manual trigger)
For repeatable cutover/redeploy, `.github/workflows/deploy-v2.yml` drives the whole
thing — **manual trigger only** (`workflow_dispatch`, type `deploy` to confirm), on
its own concurrency group so it can never collide with the v1 pipeline (`deploy.yml`,
untouched). Flow:

1. **Gate** — the v2 test matrix on GitHub runners: `dual_ci` populate → `parity_check`
   → `service_tests` → frontend build. Black runs **advisory only** (`continue-on-error`)
   — it never blocks a deploy (no forced repo-wide reformat).
2. **Smoke-build** — builds the `api` + `web` production images (catches Dockerfile
   breakage before anything ships).
3. **Deploy** — SSH to the server (reusing `HETZNER_*` secrets + `SLACK_WEBHOOK_URL`)
   and run `deploy/deploy-v2.sh`, which:
   - pre-flight (`.env` present, docker present, ≥2 GB free) → `git reset --hard origin/main`;
   - builds **SHA-tagged** images (`gi-hub-newstack-{api,web}:<sha>`) for rollback;
   - `db` up → `alembic upgrade head`;
   - **PORT-HANDOVER** — stops the v1 root `nginx` (frees `:80`/`:443`), then `up -d` the v2 stack;
   - runs `deploy/health-check.sh` (api `/health` <2s · web `/` <400 · alembic at head);
   - on success: records the SHA, prunes layers, Slack ✅. On failure: runs
     `deploy/rollback.sh` — **reverts the port-handover** (stops v2 `web`, restarts v1
     `nginx` so users land back on the known-good Streamlit app), retags the previous
     SHA images, Slack 🔁.

**The v1 and v2 stacks both bind `:80`/`:443`** — only one serves at a time; the
handover/rollback is how that's arbitrated. **DB schema is never auto-downgraded** —
rollback reverts containers/images only; a schema rollback stays a deliberate manual op.
