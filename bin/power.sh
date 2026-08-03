#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# bin/power.sh — put the always-on GI background services to sleep, and wake
# them when you sit down to work. Written for battery life on the laptop.
#
#   ./bin/power.sh sleep     stop Postgres + the cloudflared daemon → 0 CPU
#   ./bin/power.sh wake      bring both back, verified
#   ./bin/power.sh status    what is running, and what it costs you
#   ./bin/power.sh reap      unload the DEAD legacy LaunchAgents (see below)
#   ./bin/power.sh restore   put those agents back
#
# WHAT ACTUALLY DRAINS THE BATTERY — measured 2026-08-03, not assumed:
#
#   idle Postgres        0.0% CPU   six parked backend processes, ~30 MB
#   idle cloudflared     0.1% CPU   one long-lived TLS connection
#   com.gi.whatsapp-worker  ← THE REAL ONE. Its script (whatsapp_worker.py)
#                           was removed in the 2026-07-13 cutover, but the
#                           agent has KeepAlive{Crashed:true} and
#                           ThrottleInterval 30, so launchd has been spawning
#                           a Python interpreter TWICE A MINUTE ever since —
#                           2,880 failed launches a day, still going. Its
#                           error log is 4.1 MB of the same line.
#
# So `sleep` handles the two services you asked about, and `reap` handles the
# four stale agents from the old stack that are the larger cost. `reap` is a
# separate, explicit command because it changes login-time behaviour; `restore`
# undoes it exactly. Neither ever deletes a plist.
#
# ⚠️ While asleep, https://gi.giinventory.com is OFFLINE — the root cloudflared
#    LaunchDaemon is the only thing serving it. `wake` puts it back.
#
# Companion to bin/dev.sh: that script raises the DEV stack (API + Vite) and
# deliberately leaves these shared services alone. This one owns the shared
# services and never touches the dev stack, beyond warning when it is up.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_FORMULA="postgresql@16"
PG_HOST="127.0.0.1"
PG_PORT="5433"
CF_LABEL="com.cloudflare.cloudflared"
CF_PLIST="/Library/LaunchDaemons/${CF_LABEL}.plist"
GI_HOSTNAME="gi.giinventory.com"

# Legacy agents whose programs no longer exist after the cutover restructure.
STALE_AGENTS=(com.gi.whatsapp-worker com.gi.streamlit com.gi.locate-anything com.gi.cloudflared)

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[31m'; grn=$'\033[32m'
ylw=$'\033[33m'; cyn=$'\033[36m'; off=$'\033[0m'
info() { printf '%s▸%s %s\n' "$cyn" "$off" "$*"; }
ok()   { printf '%s✓%s %s\n' "$grn" "$off" "$*"; }
warn() { printf '%s!%s %s\n' "$ylw" "$off" "$*"; }
die()  { printf '%s✗%s %s\n' "$red" "$off" "$*" >&2; exit 1; }

pg_up()  { pg_isready -q -h "$PG_HOST" -p "$PG_PORT" 2>/dev/null; }
cf_pid() { pgrep -f 'cloudflared tunnel run --token' 2>/dev/null | head -1; }
cf_up()  { [ -n "$(cf_pid)" ]; }

# The dev stack keeps its pids in .dev/; refuse to yank Postgres out from
# under a running API rather than letting it fail in a confusing way later.
dev_stack_up() {
  local f p
  for f in api web; do
    p="$(cat "$ROOT/.dev/$f.pid" 2>/dev/null || true)"
    [ -n "$p" ] && kill -0 "$p" 2>/dev/null && return 0
  done
  return 1
}

# --- sudo -------------------------------------------------------------------
# Only the cloudflared LaunchDaemon needs root. Ask once, up front, so the
# password prompt never lands in the middle of a half-finished transition.
need_root() {
  if sudo -n true 2>/dev/null; then return 0; fi
  info "the cloudflared daemon is owned by root — asking for your password once"
  sudo -v || die "no sudo — run the printed launchctl command yourself"
}

# --- commands ---------------------------------------------------------------

cmd_sleep() {
  local skip_dev="${1:-}"
  if dev_stack_up && [ "$skip_dev" != "--force" ]; then
    warn "the dev stack is running (API/Vite) — stopping Postgres would break it."
    printf '   Stop it first:  %s./bin/dev.sh stop%s\n' "$bold" "$off"
    printf '   Or override:    %s./bin/power.sh sleep --force%s\n' "$bold" "$off"
    exit 1
  fi

  if cf_up; then
    need_root
    info "unloading the cloudflared LaunchDaemon…"
    if sudo launchctl bootout "system/$CF_LABEL" 2>/dev/null; then
      # bootout only unloads for now; disable keeps it down across a reboot.
      sudo launchctl disable "system/$CF_LABEL" 2>/dev/null || true
      ok "cloudflared stopped   ${dim}($GI_HOSTNAME is now offline)${off}"
    else
      warn "could not unload $CF_LABEL — try:
   ${bold}sudo launchctl bootout system/$CF_LABEL${off}"
    fi
  else
    ok "cloudflared already stopped"
  fi

  if pg_up; then
    info "stopping $PG_FORMULA…"
    brew services stop "$PG_FORMULA" >/dev/null 2>&1 \
      && ok "Postgres stopped" \
      || warn "could not stop $PG_FORMULA (brew services stop $PG_FORMULA)"
  else
    ok "Postgres already stopped"
  fi

  local stale; stale="$(count_stale)"
  if [ "$stale" -gt 0 ]; then
    printf '\n'
    warn "$stale stale legacy agent(s) are STILL loaded and respawning — they cost
   more than the two services above. See: ${bold}./bin/power.sh reap${off}"
  fi
  printf '\n%s  Asleep. Wake with: %s./bin/power.sh wake%s\n' "$dim" "$bold$off$bold" "$off"
}

cmd_wake() {
  if ! pg_up; then
    info "starting $PG_FORMULA…"
    brew services start "$PG_FORMULA" >/dev/null 2>&1 \
      || die "could not start $PG_FORMULA"
    local i=0
    while [ $i -lt 100 ] && ! pg_up; do sleep 0.25; i=$((i + 1)); done
    pg_up && ok "Postgres up on :$PG_PORT" || die "Postgres never became ready on :$PG_PORT"
  else
    ok "Postgres already up on :$PG_PORT"
  fi

  if ! cf_up; then
    [ -f "$CF_PLIST" ] || die "missing $CF_PLIST — nothing to load"
    need_root
    info "loading the cloudflared LaunchDaemon…"
    sudo launchctl enable "system/$CF_LABEL" 2>/dev/null || true
    sudo launchctl bootstrap system "$CF_PLIST" 2>/dev/null \
      || sudo launchctl kickstart -k "system/$CF_LABEL" 2>/dev/null || true
    local i=0
    while [ $i -lt 40 ] && ! cf_up; do sleep 0.25; i=$((i + 1)); done
    if cf_up; then
      ok "cloudflared up   ${dim}(pid $(cf_pid))${off}"
    else
      warn "cloudflared did not come up — ${bold}sudo launchctl kickstart -k system/$CF_LABEL${off}"
    fi
  else
    ok "cloudflared already up   ${dim}(pid $(cf_pid))${off}"
  fi

  # Registered ≠ routed: probe the real hostname, retrying while the edge
  # catches up (same reasoning as dev.sh start_tunnel).
  if cf_up; then
    local code="000" i=0
    while [ $i -lt 6 ]; do
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://$GI_HOSTNAME/" || echo 000)"
      case "$code" in 2*|3*) break ;; esac
      sleep 2; i=$((i + 1))
    done
    case "$code" in
      2*|3*) ok "https://$GI_HOSTNAME → HTTP $code" ;;
      *) warn "https://$GI_HOSTNAME → HTTP $code (the edge can lag ~30s after a restart)" ;;
    esac
  fi
  printf '\n%s  Awake. Start the dev stack with: %s./bin/dev.sh localhost%s\n' "$dim" "$bold$off$bold" "$off"
}

# How many of the stale agents are still loaded in the GUI domain.
count_stale() {
  local n=0 a
  for a in "${STALE_AGENTS[@]}"; do
    launchctl print "gui/$(id -u)/$a" >/dev/null 2>&1 && n=$((n + 1))
  done
  printf '%s' "$n"
}

cmd_reap() {
  local uid; uid="$(id -u)"
  local done_n=0 a
  for a in "${STALE_AGENTS[@]}"; do
    if launchctl print "gui/$uid/$a" >/dev/null 2>&1; then
      launchctl bootout "gui/$uid/$a" 2>/dev/null || true
      # `disable` is what survives a reboot — the plists have RunAtLoad=true
      # and are left on disk untouched, so `restore` is a clean undo.
      launchctl disable "gui/$uid/$a" 2>/dev/null || true
      ok "unloaded + disabled $a"
      done_n=$((done_n + 1))
    else
      printf '%s·%s %s was not loaded\n' "$dim" "$off" "$a"
    fi
  done
  [ "$done_n" -gt 0 ] \
    && printf '\n%s  %d agent(s) will no longer respawn. Undo: %s./bin/power.sh restore%s\n' \
       "$dim" "$done_n" "$bold$off$bold" "$off" \
    || ok "nothing to reap"
}

cmd_restore() {
  local uid a plist
  uid="$(id -u)"
  for a in "${STALE_AGENTS[@]}"; do
    plist="$HOME/Library/LaunchAgents/$a.plist"
    if [ -f "$plist" ]; then
      launchctl enable "gui/$uid/$a" 2>/dev/null || true
      launchctl bootstrap "gui/$uid" "$plist" 2>/dev/null \
        && ok "restored $a" \
        || warn "could not bootstrap $a (already loaded?)"
    else
      warn "no plist for $a — nothing to restore"
    fi
  done
}

cmd_status() {
  printf '%sGI Hub background services%s\n' "$bold" "$off"
  if pg_up; then
    local n; n="$(pgrep -f "$PG_FORMULA/bin/postgres" 2>/dev/null | wc -l | tr -d ' ')"
    ok  "Postgres      :$PG_PORT   ${dim}$PG_FORMULA, $n process(es)${off}"
  else
    warn "Postgres      :$PG_PORT   asleep"
  fi
  if cf_up; then
    ok  "cloudflared   $GI_HOSTNAME   ${dim}pid $(cf_pid)${off}"
  else
    warn "cloudflared   $GI_HOSTNAME   asleep ${dim}(the site is offline)${off}"
  fi
  dev_stack_up && ok "dev stack     running   ${dim}(./bin/dev.sh status)${off}" \
               || printf '%s·%s dev stack     down\n' "$dim" "$off"

  printf '\n%sLegacy agents from the pre-cutover stack%s\n' "$bold" "$off"
  local uid a n_loaded=0
  uid="$(id -u)"
  for a in "${STALE_AGENTS[@]}"; do
    if launchctl print "gui/$uid/$a" >/dev/null 2>&1; then
      warn "$a   ${dim}loaded — its program no longer exists${off}"
      n_loaded=$((n_loaded + 1))
    else
      printf '%s·%s %s   %sunloaded%s\n' "$dim" "$off" "$a" "$dim" "$off"
    fi
  done
  if [ "$n_loaded" -gt 0 ]; then
    printf '\n   %sThese respawn on a timer and fail instantly — com.gi.whatsapp-worker\n' "$ylw"
    printf '   alone launches Python ~2,880 times a day. Fix: %s./bin/power.sh reap%s\n' "$bold" "$off"
  fi
}

usage() {
  cat <<EOF
${bold}GI Hub — background service power manager${off}

  ${bold}./bin/power.sh sleep${off}     stop Postgres + cloudflared  ${dim}(add --force to ignore a running dev stack)${off}
  ${bold}./bin/power.sh wake${off}      start them back, verified
  ${bold}./bin/power.sh status${off}    what is running, and what it costs
  ${bold}./bin/power.sh reap${off}      unload the dead legacy LaunchAgents ${dim}(the real drain)${off}
  ${bold}./bin/power.sh restore${off}   put those agents back

While asleep, https://$GI_HOSTNAME is offline — the root cloudflared daemon is
the only thing serving it. Postgres is shared with the legacy app and every
test suite, so wake before running them (${bold}./bin/dev.sh${off} starts it for you).
EOF
}

case "${1:-}" in
  sleep|hibernate|down) shift; cmd_sleep "${1:-}" ;;
  wake|up|resume)       cmd_wake ;;
  status|ps)            cmd_status ;;
  reap|clean)           cmd_reap ;;
  restore|unreap)       cmd_restore ;;
  ''|-h|--help|help)    usage ;;
  *) usage; die "unknown command '${1}'" ;;
esac
