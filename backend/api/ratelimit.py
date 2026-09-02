"""
backend/api/ratelimit.py — a tiny in-memory rate limiter for the public auth
endpoints (login / register / 2fa), which become internet-facing after deploy.

Dependency-free on purpose (a per-endpoint FastAPI dependency). Keyed by client
IP, resolved in priority order:
  1. `CF-Connecting-IP` — the real client IP Cloudflare injects when traffic
     comes through a Cloudflare Tunnel. WITHOUT this every remote tester shares
     the tunnel's single egress IP and trips the limit together.
  2. `X-Real-IP` — set by our nginx deploy (`proxy_set_header X-Real-IP
     $remote_addr`); the client can't forge it through the proxy.
  3. the TCP peer — for a direct/no-proxy run.

Both proxy headers are trusted because the only public path to this service is
through Cloudflare (tunnel) or nginx; a direct-to-origin caller on the LAN could
spoof them, which is an accepted local-network trade-off (noted in the backlog).

CAVEAT: the store is per-process. With N uvicorn workers the effective ceiling
is N × the configured limit — fine as basic brute-force/abuse protection, but
for a hard cross-worker limit use a shared store (e.g. Redis). Good enough for
a single-box deploy; noted in the improvement backlog.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)


# Peers whose forwarded-IP headers we believe (audit A03-F6). CF-Connecting-IP
# and X-Real-IP are attacker-supplied on any request that reaches the origin
# without traversing Cloudflare/nginx, and rotating one yields a fresh bucket
# per request — defeating the login, register, OTP and PenaltyBox limiters at
# once. Trust them only from a configured proxy peer.
#
# Accepted values:
#   ""   (unset)  — trust the headers from any peer. The pre-Phase-2 behaviour,
#                   kept as the default because the correct peer address differs
#                   per deployment and a wrong one is an outage (see below).
#   "*"           — EXPLICITLY trust any peer. Same effect as unset, but states
#                   the intent. Correct for the Cloudflare Tunnel topology,
#                   where the box publishes no host ports: the only route to the
#                   API is edge → cloudflared → nginx, so there is no path for a
#                   client to reach the origin directly and forge the header.
#                   Do NOT use this if any port is ever published to the host.
#   "a.b.c.d,…"   — trust only these peers.
#
# ⚠️ A non-empty value that never matches (e.g. a stale container IP) is the
# dangerous case: every request then keys on the proxy's own address, so all
# users share ONE bucket and /auth/login locks out globally at 10/min. That is
# why `*` is a first-class value rather than something to approximate with a
# guessed IP.
_TRUSTED_PROXY_WILDCARD = "*"
_TRUSTED_PROXIES = {p.strip() for p in
                    os.environ.get("GI_TRUSTED_PROXIES", "").split(",") if p.strip()}


def _peer_trusted(request: Request) -> bool:
    if not _TRUSTED_PROXIES:
        return True          # unconfigured → legacy behaviour, documented above
    if _TRUSTED_PROXY_WILDCARD in _TRUSTED_PROXIES:
        return True          # explicit "trust any peer"
    peer = request.client.host if request.client else ""
    return peer in _TRUSTED_PROXIES


def _client_ip(request: Request) -> str:
    # Cloudflare Tunnel first — otherwise all tunnelled testers key on one IP.
    if _peer_trusted(request):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()
    return request.client.host if request.client else "unknown"


def rate_limit(max_calls: int, window_seconds: int):
    """FastAPI dependency: at most `max_calls` per `window_seconds` per client
    IP per endpoint path. Raises 429 (with Retry-After) when exceeded.

    TWO LAYERS since 2026-09-02. The in-memory bucket trips first and costs
    nothing; the `rate_buckets` row is what makes the ceiling true across all
    four uvicorn workers instead of 4x the configured limit. The shared half is
    a no-op unless `strict_limits_enabled()` — see check_bucket_shared.
    """
    async def _dep(request: Request):
        check_bucket(f"{_client_ip(request)}:{request.url.path}",
                     max_calls, window_seconds)
        if strict_limits_enabled():
            # Imported lazily: db.py imports config, and a module-level import
            # here would put the whole database stack behind every import of
            # this module — including the ones in scripts that never open a
            # connection.
            from .db import SessionLocal
            async with SessionLocal() as s:
                await check_bucket_shared(s, ip_bucket_key(request),
                                          max_calls, window_seconds)
    return Depends(_dep)


def check_bucket(key: str, max_calls: int, window_seconds: int,
                 message: str = "too many requests — please slow down") -> None:
    """Sliding-window check on an ARBITRARY key (IP, phone number, …).
    Raises 429 with Retry-After when the bucket is full; otherwise records
    the hit. Phase 8-2: lets endpoints layer identity-keyed limits (e.g. one
    OTP budget per PHONE NUMBER regardless of source IP) on top of the
    per-IP dependency."""
    now = time.monotonic()
    cutoff = now - window_seconds
    dq = _hits[(key, "")]
    while dq and dq[0] < cutoff:
        dq.popleft()
    if len(dq) >= max_calls:
        retry_after = int(dq[0] + window_seconds - now) + 1
        raise HTTPException(429, message, headers={"Retry-After": str(retry_after)})
    dq.append(now)


# ── Phase 8-2: strict abuse rules (OTP toll fraud, webhook HMAC probing) ──────
# These are HARD limits meant for the internet-facing deploy. In hermetic test
# environments (GI_DOTENV=0 — service_tests, Playwright, CI) they are relaxed
# so functional suites can exercise the OTP/webhook flows freely; the suites
# that test THE LIMITS THEMSELVES force them on via GI_FORCE_STRICT_LIMITS=1.
def strict_limits_enabled() -> bool:
    import os
    if os.environ.get("GI_FORCE_STRICT_LIMITS") == "1":
        return True
    return os.environ.get("GI_DOTENV", "").strip() != "0"


class PenaltyBox:
    """Strike-based temporary IP ban: `threshold` strikes inside `window`
    seconds ⇒ the IP is banned for `ban_seconds`. Used for sources that keep
    sending invalid HMAC signatures to the WhatsApp webhook — after the ban
    trips, requests are refused before any body parsing happens."""

    def __init__(self, threshold: int, window_seconds: int, ban_seconds: int):
        self.threshold = threshold
        self.window = window_seconds
        self.ban = ban_seconds
        self._strikes: dict[str, deque[float]] = defaultdict(deque)
        self._banned_until: dict[str, float] = {}

    def banned_for(self, ip: str) -> int | None:
        """Seconds remaining on an active ban, else None."""
        until = self._banned_until.get(ip)
        if until is None:
            return None
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._banned_until.pop(ip, None)
            self._strikes.pop(ip, None)
            return None
        return int(remaining) + 1

    def strike(self, ip: str) -> bool:
        """Record one violation; returns True when this strike trips the ban."""
        now = time.monotonic()
        dq = self._strikes[ip]
        while dq and dq[0] < now - self.window:
            dq.popleft()
        dq.append(now)
        if len(dq) >= self.threshold:
            self._banned_until[ip] = now + self.ban
            return True
        return False


def client_ip(request: Request) -> str:
    """Public alias — same CF-Connecting-IP → X-Real-IP → peer resolution."""
    return _client_ip(request)


# ── Per-ACCOUNT login throttle (2026-08-04) ──────────────────────────────────
# `rate_limit(10, 60)` on /auth/login is keyed by IP, which stops one host
# hammering the endpoint but does nothing about the attack that actually
# matters: credential stuffing against ONE account from many hosts. Every
# source IP gets its own fresh budget, so guesses against `admin` were
# effectively unlimited given enough addresses.
#
# This layers a second budget keyed on the USERNAME, counting only FAILURES.
# A user who types their own password wrong a few times is unaffected; an
# attacker working through a password list is stopped at the account boundary
# no matter how many IPs they have.
#
# ⚠️ The honest trade-off: any per-account throttle is a denial-of-service
# vector — someone who knows a username can burn its budget deliberately. That
# is why this THROTTLES rather than LOCKS: the window is short, it clears the
# moment a correct password arrives, and it never disables the account or
# requires an admin to intervene. OWASP prefers exactly this shape over
# classic account lockout for the same reason.
LOGIN_FAIL_MAX = 8            # failures per account…
LOGIN_FAIL_WINDOW = 900       # …within 15 minutes → throttled
_login_fails: dict[str, deque[float]] = defaultdict(deque)


def _account_key(username: str) -> str:
    return (username or "").strip().lower()


def assert_login_allowed(username: str) -> None:
    """Raise 429 when this ACCOUNT has too many recent failures.

    Called before the password is checked, so a throttled account costs an
    attacker a bcrypt verify of nothing. No-op when strict limits are relaxed
    (hermetic test runs) — see strict_limits_enabled().
    """
    if not strict_limits_enabled():
        return
    key = _account_key(username)
    if not key:
        return
    now = time.monotonic()
    dq = _login_fails[key]
    while dq and dq[0] < now - LOGIN_FAIL_WINDOW:
        dq.popleft()
    if len(dq) >= LOGIN_FAIL_MAX:
        retry = int(dq[0] + LOGIN_FAIL_WINDOW - now) + 1
        raise HTTPException(
            429,
            "too many failed sign-in attempts for this account — "
            "please wait a few minutes and try again",
            headers={"Retry-After": str(retry)})


def note_login_failure(username: str) -> None:
    """Record one failed attempt against the account."""
    if not strict_limits_enabled():
        return
    key = _account_key(username)
    if key:
        _login_fails[key].append(time.monotonic())


def clear_login_failures(username: str) -> None:
    """A correct password ends the throttle immediately — the legitimate owner
    is never left waiting out a window an attacker filled."""
    _login_fails.pop(_account_key(username), None)


# ─── the SHARED budget (2026-08-05) ──────────────────────────────────────────
#
# Everything above is per PROCESS, so N uvicorn workers means N × LOGIN_FAIL_MAX
# — the same caveat the per-IP limiter carries, and harmless on a single-worker
# box but a silent multiplier on anything larger.
#
# These three are the cross-worker authority, backed by one `login_attempts`
# row per account (alembic f3c81d5a97e2). They run ALONGSIDE the in-process
# budget rather than replacing it: the memory check costs nothing and trips
# first inside a hot worker, and the row is what makes the ceiling true across
# all of them.
#
# POSTGRES, NOT REDIS — the counter ticks a few times a minute, and Postgres is
# already deployed, already backed up, already in the runbook and already holds
# the users table this protects. One atomic `INSERT … ON CONFLICT DO UPDATE …
# RETURNING` per failure, one `SELECT` per attempt.
#
# ⚠️ STILL THROTTLES, NEVER LOCKS (rule 10). The window rolls forward on its
# own and a correct password DELETES the row. No administrator is ever in the
# recovery path, because a budget a stranger can burn on your behalf must not
# need a support ticket to undo.
#
# Every one of these is a no-op when strict limits are relaxed, and every one
# swallows database errors: a throttle that takes sign-in down when its own
# storage hiccups is worse than the attack it prevents.
_SHARED_SQL_TOUCH = """
    INSERT INTO login_attempts (username_lc, window_start, failures)
    VALUES (:u, CURRENT_TIMESTAMP, 1)
    ON CONFLICT (username_lc) DO UPDATE SET
        -- A stale window is not decayed, it is RESTARTED: the budget is
        -- "8 failures within 15 minutes", not a leaky bucket.
        window_start = CASE
            WHEN login_attempts.window_start < CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')
            THEN CURRENT_TIMESTAMP ELSE login_attempts.window_start END,
        failures = CASE
            WHEN login_attempts.window_start < CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')
            THEN 1 ELSE login_attempts.failures + 1 END
    RETURNING failures
"""

_SHARED_SQL_READ = """
    SELECT failures,
           EXTRACT(EPOCH FROM (window_start + (:w * INTERVAL '1 second')
                               - CURRENT_TIMESTAMP))::int AS retry_after
    FROM login_attempts
    WHERE username_lc = :u
      AND window_start > CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')
"""


async def assert_login_allowed_shared(session, username: str) -> None:
    """Cross-worker half of `assert_login_allowed`. Raises the same 429."""
    if not strict_limits_enabled():
        return
    key = _account_key(username)
    if not key:
        return
    from sqlalchemy import text as _text
    try:
        row = (await session.execute(_text(_SHARED_SQL_READ),
                                     {"u": key, "w": LOGIN_FAIL_WINDOW})).first()
    except Exception:
        return      # storage trouble must not deny sign-in
    if row and row[0] >= LOGIN_FAIL_MAX:
        raise HTTPException(
            429,
            "too many failed sign-in attempts for this account — "
            "please wait a few minutes and try again",
            headers={"Retry-After": str(max(1, int(row[1] or 1)))})


async def note_login_failure_shared(session, username: str) -> None:
    key = _account_key(username)
    if not strict_limits_enabled() or not key:
        return
    from sqlalchemy import text as _text
    try:
        await session.execute(_text(_SHARED_SQL_TOUCH),
                              {"u": key, "w": LOGIN_FAIL_WINDOW})
        await session.commit()
    except Exception:
        await session.rollback()


async def clear_login_failures_shared(session, username: str) -> None:
    key = _account_key(username)
    if not key:
        return
    from sqlalchemy import text as _text
    try:
        await session.execute(
            _text("DELETE FROM login_attempts WHERE username_lc = :u"), {"u": key})
        await session.commit()
    except Exception:
        await session.rollback()

# ─── the SHARED bucket store (Phase 10 Track 4→1, 2026-09-02) ────────────────
#
# ⚠️ EVERYTHING ABOVE IS PER PROCESS, and `deploy/Dockerfile.api` runs
# `uvicorn --workers 4`. So the real ceiling on each in-memory limiter was 4x
# its configured limit:
#
#   rate_limit(n, w) per-IP dependency ....... 4 x n
#   check_bucket() identity-keyed budgets .... 4 x n
#   PenaltyBox webhook bans .................. the ban held on 1 worker of 4
#   auth._totp_failures (2FA attempts) ....... 4 x 5 = 20 codes per window
#
# The last is the worst: it is the ceiling on brute-forcing the SECOND FACTOR,
# and `_verify_totp` runs at valid_window=1, so three 6-digit codes are
# acceptable at any instant.
#
# `login_attempts` solved exactly this for the per-account login budget and its
# migration recorded WHY Postgres rather than Redis. This generalises that
# mechanism rather than introducing a second idea of what a shared counter is.
#
# ⚠️ TWO LAYERS, NOT A REPLACEMENT. The in-memory check still runs first: it
# costs nothing and trips inside a hot worker before any query happens. The row
# is what makes the ceiling true across all four workers.
#
# ⚠️ FAILS OPEN (operator ruling Q1.2, 2026-09-02). Every function here
# swallows storage errors and ALLOWS the request. A throttle that takes sign-in
# down when its own storage hiccups is worse than the attack it prevents. Note
# this is deliberately the opposite of the access matrix, which fails CLOSED —
# an unknown route is refused, an unavailable throttle is not enforced. They are
# different decisions about different things.
#
# ⚠️ AND IT IS GATED BY `strict_limits_enabled()`, like everything else here.
# In hermetic runs (GI_DOTENV=0 — service_tests, Playwright, CI) the shared
# layer is a no-op, so 2,100 committing tests do not each write a bucket row.
# The suites that test THE LIMITS force it on with GI_FORCE_STRICT_LIMITS=1.

_BUCKET_TOUCH = """
    INSERT INTO rate_buckets (bucket_key, window_start, hits)
    VALUES (:k, CURRENT_TIMESTAMP, 1)
    ON CONFLICT (bucket_key) DO UPDATE SET
        -- A stale window is RESTARTED, not decayed: the budget is "n hits
        -- within w seconds", not a leaky bucket. Same semantics as
        -- login_attempts, so the two cannot drift in behaviour.
        window_start = CASE
            WHEN rate_buckets.window_start < CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')
            THEN CURRENT_TIMESTAMP ELSE rate_buckets.window_start END,
        hits = CASE
            WHEN rate_buckets.window_start < CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')
            THEN 1 ELSE rate_buckets.hits + 1 END
    RETURNING hits,
        EXTRACT(EPOCH FROM (window_start + (:w * INTERVAL '1 second')
                            - CURRENT_TIMESTAMP))::int AS retry_after
"""


async def check_bucket_shared(session, key: str, max_calls: int,
                              window_seconds: int,
                              message: str = "too many requests — please slow down"
                              ) -> None:
    """Cross-worker sliding window on an arbitrary key. Raises 429 when full.

    ⚠️ COUNTS THE CURRENT REQUEST, then refuses if that put it over. The
    in-memory `check_bucket` checks-then-records, which is the same ordering
    seen from outside: `max_calls` succeed and the next one is refused.
    """
    if not strict_limits_enabled() or not key:
        return
    from sqlalchemy import text as _text
    try:
        row = (await session.execute(_text(_BUCKET_TOUCH),
                                     {"k": key, "w": window_seconds})).first()
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        return          # fail open — storage trouble must not deny the request
    if row and row[0] > max_calls:
        raise HTTPException(429, message,
                            headers={"Retry-After": str(max(1, int(row[1] or 1)))})


async def read_bucket_shared(session, key: str,
                             window_seconds: int) -> tuple[int, int] | None:
    """(hits, seconds_remaining) for an OPEN window, else None. Reads only.

    ⚠️ THIS EXISTS BECAUSE `check_bucket_shared` INCREMENTS. Using the counting
    function to ASK "is this IP banned?" creates the ban it was asking about —
    caught by suite `limits` on 2026-09-02, where the first invalid webhook
    signature answered 429 instead of 403 because the ban check had opened the
    ban. A test and a tally are different operations and need different verbs.
    """
    if not key:
        return None
    from sqlalchemy import text as _text
    try:
        row = (await session.execute(_text(
            "SELECT hits, EXTRACT(EPOCH FROM (window_start + "
            "(:w * INTERVAL '1 second') - CURRENT_TIMESTAMP))::int "
            "FROM rate_buckets WHERE bucket_key = :k "
            "  AND window_start > CURRENT_TIMESTAMP - (:w * INTERVAL '1 second')"),
            {"k": key, "w": window_seconds})).first()
    except Exception:
        return None          # fail open, like everything else here
    return (int(row[0]), max(1, int(row[1] or 1))) if row else None


async def open_bucket_shared(session, key: str) -> None:
    """Start (or restart) a window on `key` without counting a hit.

    Used for a BAN, where the row's existence is the state rather than a tally.
    """
    if not key:
        return
    from sqlalchemy import text as _text
    try:
        await session.execute(_text(
            "INSERT INTO rate_buckets (bucket_key, window_start, hits) "
            "VALUES (:k, CURRENT_TIMESTAMP, 1) "
            "ON CONFLICT (bucket_key) DO UPDATE SET "
            "  window_start = CURRENT_TIMESTAMP, hits = 1"), {"k": key})
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass


async def clear_bucket_shared(session, key: str) -> None:
    """Drop a bucket. Used where success ends a throttle immediately — a
    correct TOTP code, like a correct password, must not leave the legitimate
    owner waiting out a window an attacker filled."""
    if not key:
        return
    from sqlalchemy import text as _text
    try:
        await session.execute(
            _text("DELETE FROM rate_buckets WHERE bucket_key = :k"), {"k": key})
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass


async def sweep_rate_buckets(session, older_than_seconds: int = 86400) -> int:
    """Delete buckets whose window closed long ago.

    Called from the scheduler loop. Without it the table grows by one row per
    distinct IP//path pair ever seen and never shrinks — which is fine for a
    month and not fine for a year.
    """
    from sqlalchemy import text as _text
    try:
        res = await session.execute(_text(
            "DELETE FROM rate_buckets WHERE window_start < "
            "CURRENT_TIMESTAMP - (:s * INTERVAL '1 second')"),
            {"s": older_than_seconds})
        await session.commit()
        return res.rowcount or 0
    except Exception:
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        return 0


def ip_bucket_key(request: Request) -> str:
    """The key `rate_limit` uses, exposed so tests can compute it."""
    return f"ip:{_client_ip(request)}:{request.url.path}"
