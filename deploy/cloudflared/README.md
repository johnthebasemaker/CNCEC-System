# Cloudflare Tunnels — the developer workflow

> **Start here: [THE WORKFLOW](#the-workflow-2026-08-01) below.** It is the
> definitive, measured answer to "which command do I run for which environment".
> The sections after it are the original notes plus the Error 1033 post-mortem.

---

## THE WORKFLOW (2026-08-01)

### What actually exists on this Mac

Measured, not assumed (`cloudflared tunnel list` + `ps aux` + live probes):

| Tunnel | ID | Connector | Serves |
|---|---|---|---|
| **GI-MacBook-Local** | `9a68ed28…` | **root LaunchDaemon, always up** (`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`) | ingress is **dashboard-managed** (remotely configured) |
| **Local-Tunnel-gi-hub** | `40802134…` | none — only when you run its `--token` by hand | this is why `local.giinventory.com` needs that terminal open |
| **gi-hub** | `8e2f8d9d…` | none — started by `config.yml` in this folder | credentials on disk at `~/.cloudflared/8e2f8d9d….json` |
| Hetzner-Production | `ccf809f6…` | none (server not provisioned) | the future production box |

**The trap this file used to hide:** `config.yml` says `tunnel: 8e2f8d9d`
(**gi-hub**) but its only ingress rule is `local.giinventory.com`. Those are two
different tunnels. Proven 2026-08-01: with the gi-hub connector fully registered
(4 QUIC connections) and Vite live on :5173, `https://local.giinventory.com`
still returned **530 / Error 1033** — the hostname is not routed to gi-hub. That
is exactly why only the raw `--token` command works today.

### Pick one of these two, once

**Option A — recommended: make the config file authoritative.** One DNS command
(it rewrites the CNAME for that hostname; run it yourself, it changes your
Cloudflare account):

```bash
cloudflared tunnel route dns gi-hub local.giinventory.com
```

After that the token is never needed again: ingress lives in git, the tunnel
runs from `config.yml`, and `local.giinventory.com` is served by **gi-hub**.

**Option B — keep the token.** Leave the DNS alone and configure
`Local-Tunnel-gi-hub`'s public hostname in the Zero Trust dashboard
(`local.giinventory.com` → `http://localhost:5173`). The ingress then lives in
the dashboard, not in this repo, and you keep running its `--token` command.

### The two environments, end to end

Each needs **three things up: the API, Vite, and exactly one connector.**

#### Local development → `https://local.giinventory.com`

```bash
./run_api.sh
```

```bash
npm run dev:local --prefix frontend
```

```bash
cloudflared tunnel --config deploy/cloudflared/config.yml run gi-hub
```

(That third command is the Option-A form. On Option B it stays your
`cloudflared tunnel run --token …` line — same slot, same rules.)

#### The `gi.giinventory.com` mirror

`gi.giinventory.com` is **already served by the root LaunchDaemon**, which is
always running and is the one connector you must not duplicate. So there is **no
tunnel command to run** — you only repoint what the dev server answers with:

```bash
npm run dev:gi --prefix frontend
```

⚠️ `gi.giinventory.com` becomes the **production** hostname the moment the
Hetzner box goes live. From that day, serving it from this Mac is no longer a
mirror — it is a hijack of production. Use `local.giinventory.com` day to day.

### Why port 5173 can never be fought over

`vite.config.ts` now sets **`strictPort: true`**. A tunnel's ingress points at a
fixed port, so the old failure mode was silent: start a second dev server, Vite
quietly takes **5174**, the terminal looks healthy, and the tunnel keeps serving
the first one (or nothing at all). With `strictPort` the second start dies
immediately with `EADDRINUSE`. **Run one dev server at a time** — `dev:local`
and `dev:gi` are alternatives, never concurrent.

Related: `npm run dev` (plain) is for `http://localhost:5173` and is the only
one whose HMR websocket works locally. `dev:local` / `dev:gi` point HMR at the
tunnel's TLS port, so browsing localhost while running them logs
`[vite] failed to connect to websocket` and live-reload stops — harmless, but
use plain `dev` when you are not testing through a tunnel.

### The rule that prevents Error 1033

**One hostname → one tunnel → one connector.** 1033 means the hostname's tunnel
has no healthy connector; on this Mac it has always been several connectors
racing across *different* tunnel IDs. Before starting anything:

```bash
ps aux | grep -i "[c]loudflared tunnel"
```

Expect exactly one line — the root daemon. If you see your own
`--config` or `--token` process from an earlier session, kill it first:

```bash
pkill -f "cloudflared tunnel --config"
```

---

## Original notes

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
