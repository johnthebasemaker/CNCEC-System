# Local multi-user testing over the existing `gi-hub` Cloudflare Tunnel

This reuses the tunnel you already created for the legacy build
(`8e2f8d9d-08f4-432e-9857-dee2ff4ebb63`) to serve the **new** React/FastAPI stack
from your Mac at **https://gi.giinventory.com**, so several people can test at once.

Because the DNS record `gi.giinventory.com` already points at this tunnel ID,
**no DNS change is needed** — this just swaps what the tunnel serves.

## How the routing works
`config.yml` sends all `gi.giinventory.com` traffic to the **Vite dev server on
:5173**. Vite serves the SPA and proxies `/api/*` to the **FastAPI backend on
:8000**, stripping the `/api` prefix — the same single-origin behaviour nginx
gives in production. (Don't split `/api` in the tunnel config: FastAPI doesn't
mount an `/api` prefix, so Cloudflare — which can't rewrite paths — would 404.)

## Run it (3 terminals)

```bash
# 1) FastAPI backend on :8000 (against the local Postgres mirror)
cd ~/GI_Hub_Project
DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5433/gihub \
  JWT_SECRET="$(openssl rand -hex 32)" \
  ./run_api.sh

# 2) Vite dev server on :5173 in TUNNEL MODE (allows the gi.giinventory.com host
#    + points HMR at the tunnel's TLS port). Real dual_ci data is already loaded.
cd ~/GI_Hub_Project/frontend
VITE_TUNNEL=1 npm run dev

# 3) Start the tunnel with THIS config  ← the command you asked for
cloudflared tunnel --config ~/GI_Hub_Project/deploy/cloudflared/config.yml run gi-hub
```

Then open **https://gi.giinventory.com**. Cloudflare terminates TLS at its edge,
so login refresh cookies (Secure) work, and — thanks to the rate-limiter fix —
each remote tester is keyed on their real IP via `CF-Connecting-IP` instead of
sharing the tunnel's single egress IP.

## Notes
- **Load real data first** (once): `DATABASE_URL=…:5433/gihub python backend/dual_ci.py --source gi_database.db`.
- Keep the Mac awake for the session: `caffeinate -s` in a spare terminal.
- To point the tunnel back at the legacy build later, just run `cloudflared`
  with the old config instead — the tunnel/DNS are unchanged.
- If `credentials-file` isn't at the path above, find it with
  `ls ~/.cloudflared/*.json` and update `config.yml`.
- Optional: put **Cloudflare Access** in front of `gi.giinventory.com` to gate
  who can reach the test site.

---

## ⚠️ Error 1033 — the recurring cause, and the fix

**Error 1033 means the hostname's tunnel has no healthy connector registered.**
On this Mac it has always been the same thing: **several cloudflared connectors
running at once against DIFFERENT tunnel IDs**, so whichever one the DNS CNAME
points at is not the one that is up.

Three connectors were live at diagnosis time (2026-07-29):

| Owner | What |
|---|---|
| you (a terminal) | `cloudflared tunnel --config deploy/cloudflared/config.yml run gi-hub` — a local-config tunnel |
| you (a terminal) | `cloudflared tunnel run --token …` — a *different* tunnel ID |
| **root (LaunchDaemon)** | `cloudflared tunnel run --token …` — the remotely managed one, `/Library/LaunchDaemons/com.cloudflare.cloudflared.plist` |

**Exactly one connector should run: the root LaunchDaemon.** Verified clean
2026-07-30.

### Verify what is actually running

```bash
ps aux | grep -i "[c]loudflared tunnel"
```

```bash
cloudflared tunnel list
```

### Kill the rogue user-level instances (leaves the root daemon alone)

```bash
pkill -f "cloudflared tunnel --config"
```

```bash
pkill -u "$(id -u)" -f "cloudflared tunnel run --token"
```

### Stop the dormant user LaunchAgent from resurrecting a local-config tunnel

`~/Library/LaunchAgents/com.gi.cloudflared.plist` runs
`cloudflared tunnel --config ~/.cloudflared/config.yml run gi-hub` with
`KeepAlive=true`, so killing its process is not enough:

```bash
launchctl bootout "gui/$(id -u)/com.gi.cloudflared" 2>/dev/null; launchctl disable "gui/$(id -u)/com.gi.cloudflared"
```

### Restart the remotely managed (token) tunnel

```bash
sudo launchctl kickstart -k system/com.cloudflare.cloudflared
```

### Confirm it reconnected

```bash
sudo log show --predicate 'process == "cloudflared"' --last 2m --style compact | tail -30
```

### 🔐 Security note

The managed daemon takes its tunnel token as a **command-line argument**, so the
full token is readable by any local process via `ps aux`. If this machine is ever
shared, rotate the token and move it into the plist's `EnvironmentVariables`
instead of `ProgramArguments`.
