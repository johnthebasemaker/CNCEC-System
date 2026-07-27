# Phase 3 prep — Cloudflare Tunnel topology (run log)

**Decision:** ingress is a Cloudflare Tunnel; the Hetzner box publishes **zero**
host ports and Cloudflare terminates TLS + enforces Zero Trust Access.
**Date:** 2026-07-27 · **Branch:** `chore/deploy-cloudflare-tunnel` (off `main`
at `ad7aed1`, which carries Phase 2 Themes A–D + the by-id mop-up)

---

## 1. Why this changed

The deploy kit implemented the *opposite* topology from the one the
architecture docs described. `docker-compose.prod.yml` published `:80`/`:443`
and ran certbot against a DNS **A record**, while `ARCHITECTURE.md` §1 and
`PROJECT_STATUS.md` §3 described a **Cloudflare Tunnel** on `gi.giinventory.com`.
The only cloudflared config in the repo (`deploy/cloudflared/config.yml`) is for
**local** multi-user testing — it points the hostname at `http://localhost:5173`
on the developer's Mac. Operator ruling: the tunnel is correct, because Zero
Trust Access protects the portal.

```
client → Cloudflare edge (TLS + Access) → tunnel → cloudflared → web:80 → api:8000
```

## 2. Changes

| File | Change |
|---|---|
| `docker-compose.prod.yml` | `certbot` service **removed** (+ its `certbot-etc`/`certbot-www` volumes); `web` loses its `ports:` and cert mounts; new **`cloudflared`** service (`cloudflare/cloudflared:latest`, `tunnel --no-autoupdate run`, `TUNNEL_TOKEN` with a `:?` fail-fast, `depends_on: [web]`) |
| `nginx.conf` | Rewritten: one `:80` server block, no TLS, no ACME webroot, **no http→https redirect** (it would loop behind the edge). Adds explicit `proxy_set_header CF-Connecting-IP`; `X-Forwarded-Proto` pinned to `https` since the edge served the user over TLS |
| `Dockerfile.web` | `EXPOSE 80` only |
| `ratelimit.py` | `GI_TRUSTED_PROXIES` now accepts `*` as a wildcard — see §3 |
| `deploy/.env` | `PUBLIC_BASE_URL=https://gi.giinventory.com/api` · `GI_TRUSTED_PROXIES=*` · `TUNNEL_TOKEN=` (blank, operator fills on the box) |
| `deploy/.env.example` | Same keys documented; `LETSENCRYPT_*` removed; **`PUBLIC_BASE_URL` added** — it was missing entirely even though the API refuses to boot without it |
| `health-check.sh` | Host `curl http://localhost/` would now always fail (no port) → fetches `http://web/` in-network; **new check that `cloudflared` is running**, since it is the only ingress |
| `deploy-v2.sh` | Port handover removed — v1 and v2 no longer contend, so v1 nginx is left alone. Cutover is a tunnel-route change |
| `rollback.sh` | Stops `cloudflared`+`web` instead of "releasing ports"; no longer starts the non-existent `certbot`; prints a loud manual step (see §4) |
| `init-letsencrypt.sh` | Kept but **guarded** — prints why it is obsolete and exits 1, rather than failing halfway on a missing `certbot` service |
| `docs/DEPLOY.md`, `deploy/README.md`, `tools/migration/README.md` | Rewritten prerequisites: CNAME not A record, **deny all inbound**, no TLS step, tunnel route → `http://web:80` |

## 3. `GI_TRUSTED_PROXIES=*` — the requested value needed a code change first

As requested, `*` would have been a **silent outage**. The Theme C
implementation did a literal set-membership test:

```python
return peer in _TRUSTED_PROXIES     # peer is an IP; "*" matches nothing
```

so `*` would match no peer, the API would fall back to the *nginx container's*
address, and **every user on the site would share one rate-limit bucket** —
`/auth/login` locks out globally at 10/min. Implemented `*` as a first-class
wildcard meaning "trust any peer", which is what the value is intended to mean
and is genuinely safe here: with no published host port there is no path for a
client to reach the origin and forge `CF-Connecting-IP`.

Documented the inverse trap too — if a host port is ever published again, `*`
must be narrowed to the real peer.

**The header chain also had to be fixed.** nginx sets
`X-Real-IP $remote_addr`, which under the tunnel is the *cloudflared container*,
not the user. `CF-Connecting-IP` (set by Cloudflare's edge) carries the true
client address and is now forwarded explicitly rather than relying on nginx's
default header pass-through. Without that, trusting the peer would have keyed
everyone onto one bucket regardless.

## 4. `PUBLIC_BASE_URL` — the `/api` suffix is load-bearing

Set to `https://gi.giinventory.com/api`, which resolves the domain bug flagged
last session (`api.giinventory.com` appeared nowhere else in the kit). The
suffix is required for two independent reasons:

1. nginx's `proxy_pass http://api:8000/;` has a **trailing slash**, so it strips
   `/api`. `…/api/reports/weekly-exec/<token>` reaches the route; the bare
   domain would 404 into the SPA fallback.
2. It places the link under the Cloudflare Access **Bypass** policy for
   `/api/*`. Recipients open these from WhatsApp with **no Access session** — a
   link on the bare domain would hit the Zero Trust login wall instead.

## 5. Rollback is no longer self-contained — deliberate, and surfaced

Previously rollback freed `:80`/`:443` and v1 nginx reclaimed them. Under the
tunnel there are no ports to hand back: DNS points at the tunnel, so stopping
cloudflared makes the domain return a Cloudflare **502** rather than falling
back to v1. `rollback.sh` now says so explicitly and names the manual step
(repoint the tunnel's public hostname at the v1 origin in the Zero Trust
dashboard). Better a loud instruction than a script that appears to succeed
while the site stays dark.

## 6. Test evidence — suites AT + AV (+6 checks)

- `at-f6` — `GI_TRUSTED_PROXIES='*'` resolves the forwarded client IP
  (`1.2.3.4`) rather than the peer, alongside the existing trusted/untrusted
  matrix.
- `av-tunnel` ×4 — no service publishes a host port · cloudflared present,
  official image, `tunnel … run`, `TUNNEL_TOKEN` with fail-fast · certbot and
  its volumes gone · nginx has no `ssl_certificate` / `listen 443` /
  `acme-challenge` / `return 301 https`, and **does** forward `CF-Connecting-IP`
  while keeping the prefix-stripping `proxy_pass`.

Negative-verified against the pre-change tree: `web` published
`['80:80','443:443']`, `certbot` present, `cloudflared` absent, and nginx had
`ssl_certificate`/`listen 443`/`acme-challenge` with **no** `CF-Connecting-IP`.

## 7. Gates

| Gate | Before | After |
|---|---|---|
| `backend.api.service_tests` | 847 / 0 | **853 / 0** (+6) |
| Playwright E2E | 39 / 39 | **39 / 39** |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `npm run build --prefix frontend` | ✅ | ✅ |
| `bash -n` on all four deploy scripts | — | ✅ |

Compose parsed and asserted structurally (no Docker CLI on this machine, so
nothing was actually built or run — this remains an unexercised kit).
`gi_database.db` sha256 unchanged; `deploy/.env` stays `0600`, gitignored and
out of the commit.

## 8. Operator TODO before `up -d`

1. Create/confirm the tunnel and paste its token into `deploy/.env` as
   `TUNNEL_TOKEN` (the stack refuses to start without it).
2. Route the tunnel's public hostname to **`http://web:80`** — the compose
   service name. Pointing it at `localhost` resolves inside cloudflared's own
   container and every request 502s.
3. Confirm the DNS record is a **CNAME to the tunnel** (it already is — the
   local testing config has been using it).
4. Keep the Access **Bypass (Everyone)** policy on `/api/*`.
5. Firewall: deny all inbound; only outbound 443 is needed.
6. Retire `deploy/cloudflared/config.yml` from the shared hostname when the
   server goes live — it currently hijacks `gi.giinventory.com` to a laptop.
