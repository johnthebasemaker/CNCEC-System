# Authentication & Session Security Audit

## Summary
- Files scanned: 14 (auth/config/ratelimit/webhook/admin core + deploy manifests consulted for env provenance)
- Findings: 12 (Critical: 0, High: 3, Medium: 6, Low: 3)
- Status: **ISSUES FOUND**

**Headline:** the cryptographic core is sound. There is a production boot check on `JWT_SECRET`, every `jwt.decode()` passes an explicit algorithm list, no call anywhere disables signature verification, bcrypt runs at cost 12, and the RTR family-revoke-on-replay behaviour **does** fire exactly as documented — I traced it line by line. The problems are at the edges: the production guard is keyed to an environment variable that only `docker-compose.prod.yml` sets (so a bare-metal deploy silently runs the dev secret), two different spellings of "production" are checked in two places, the WhatsApp webhook's signature check is fail-open on an unset secret that is still an open operator TODO, and the 2FA management endpoints carry no rate limit.

---

## Part 1 — JWT_SECRET (audited first, per the brief)

**The four questions, answered:**

| Question | Answer |
|---|---|
| What is the default value? | `"dev-insecure-change-me-not-for-production-use-0123456789"` (`config.py:90`) — 56 chars, self-describing, deliberately ≥32 so PyJWT does not emit a key-length warning in dev |
| Is there a startup assertion blocking production boot without an explicit secret? | **Yes.** `config.py:98-113` raises `RuntimeError` when `is_production()` and the secret is missing, equal to the dev default, or `<32` chars. `auth.py:62` resolves it at **import time**, so the failure is a hard boot failure, not a per-request one |
| Is the default logged / exposed via an endpoint? | **No.** It is not returned by `/health` (which exposes dialect, database name, entity list, maintenance flag — see Audit 02 Finding #11), not logged, and not echoed in any error. The `RuntimeError` message names the variable, not the value |
| Is the default a well-known string? | No — it is project-specific and explicitly non-secret by construction. GitHub-dorking risk is limited to *recognising this project*, not to compromising a correctly configured deployment |

**Verdict: not Critical.** The brief's Critical condition ("if there is no production-mode boot check") is **not** met — the check exists and is well written. However the *reachability* of that check is conditional, which is Finding #1 below.

---

## Findings

### Finding #1 — The production secret check depends on `GI_ENV`, which only docker-compose sets
- **Severity:** High
- **File:** `backend/api/config.py` (guard) · `deploy/docker-compose.prod.yml` (only setter) · `deploy/.env.example` (omission)
- **Line(s):** `config.py:93-113`; `docker-compose.prod.yml:66`; `deploy/.env.example:21-22`
- **Category:** Security control gated on an environment variable that is not set on all supported deployment paths
- **Evidence:**

  ```python
  # backend/api/config.py:93-113
  def is_production() -> bool:
      """True when GI_ENV names a production environment."""
      return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")

  def jwt_secret() -> str:
      s = os.environ.get("JWT_SECRET", "").strip()
      if is_production():
          if not s or s == _DEV_JWT_SECRET or len(s) < 32:
              raise RuntimeError(
                  "JWT_SECRET must be set to a strong secret (≥32 chars) when "
                  "GI_ENV=production — refusing to start with an insecure signing key.")
          return s
      return s or _DEV_JWT_SECRET        # ← silent fallback when GI_ENV is unset
  ```

  `GI_ENV` is set in exactly one place in the repository:

  ```yaml
  # deploy/docker-compose.prod.yml:65-67
      environment:
        GI_ENV: production
        JWT_SECRET: ${JWT_SECRET}
  ```

  It is **absent** from `deploy/.env.example` (which carries `JWT_SECRET=CHANGE_ME_run_openssl_rand_hex_32` at line 22 but no `GI_ENV` assignment — only a mention inside a comment), absent from `deploy/Dockerfile.api` (no `ENV GI_ENV`), and absent from `run_api.sh`. Meanwhile `config.py:14-33` deliberately dotenv-loads `deploy/.env` "so a plain `uvicorn backend.api.main:app` sees the same secrets docker-compose injects in production" — i.e. bare-metal production runs are an explicitly supported path, and on that path `GI_ENV` is undefined, `is_production()` returns `False`, and the app boots happily on the dev signing key.
- **Why it's a risk:** anyone who can read this public repository knows the dev secret. If the Hetzner deployment is ever run outside compose — a systemd unit, a manual `uvicorn` for debugging, a one-off migration container — every access, refresh and MFA token becomes forgeable, which is complete authentication bypass including forging `role: admin` claims. The secret's own guard cannot fire because the variable that arms it was never set.
- **Suggested fix (do NOT apply):** invert the default so the insecure path must be opted into — e.g. `GI_ENV` defaulting to `production` with an explicit `GI_ENV=dev` required for the dev fallback, or refuse the dev default whenever `DATABASE_URL` points anywhere other than localhost. At minimum, add `GI_ENV=production` to `deploy/.env.example` and to the cutover runbook's checklist.
- **Effort:** Low

### Finding #2 — WhatsApp webhook signature verification is fail-open when `WHATSAPP_APP_SECRET` is unset
- **Severity:** High
- **File:** `backend/api/webhook.py`
- **Line(s):** 88-95 (verification), 153-171 (`_cmd_reset_password`), 196-237 (handler)
- **Category:** Authentication bypass on an unauthenticated endpoint that performs credential mutations
- **Evidence:**

  ```python
  def _signature_ok(raw: bytes, header: str) -> bool:
      secret = _app_secret()
      if not secret:
          return True  # not configured — local/testing mode
      if not header.startswith("sha256="):
          return False
      digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
      return hmac.compare_digest(header[len("sha256="):], digest)
  ```

  The comparison itself is correct and **constant-time** (`hmac.compare_digest`), as is the GET handshake at line 82. The defect is the `if not secret: return True` short-circuit. `docs/PROJECT_STATUS.md` §3 lists "set `WHATSAPP_WEBHOOK_VERIFY_TOKEN` + `WHATSAPP_APP_SECRET`" as an **operator TODO that is still open**, so the shipped default state of this endpoint is unauthenticated.

  What an unauthenticated POST can reach — the command router runs before any further check, and one command rewrites a credential:

  ```python
  async def _cmd_reset_password(session: AsyncSession, user: dict) -> str:
      temp = "".join(secrets.choice(_TEMP_PW_ALPHABET) for _ in range(10))
      pw_hash = bcrypt.hashpw(temp.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
      await session.execute(users_t.update()
                            .where(users_t.c["username"] == user["username"])
                            .values(password_hash=pw_hash))
      await session.execute(delete(sessions_t).where(...))
      await session.execute(delete(refresh_t).where(...))
  ```

  The victim is selected by the attacker: `_iter_text_messages` reads the sender from the request body (`msg["from"]`), and `_resolve_sender` maps those digits to a user row. A forged payload with `from` set to a known staff phone number and body `RESET PASSWORD` resets that user's password and deletes **all** their sessions.
- **Why it's a risk:** unauthenticated, remote, pre-auth account disruption for any user whose phone number is known — password invalidated and every device signed out, repeatable at will. It is **not** account takeover: the temporary password is sent via `wa.send_session_text(..., to=user["phone"])`, i.e. to the number stored in the database, not to anything the attacker controls; and `STOCK` replies go to the same place, so there is no data exfiltration path. The `PenaltyBox` does not help — it only counts *invalid signature* strikes, and with no secret configured no request is ever invalid.
- **Suggested fix (do NOT apply):** fail closed — return `False` from `_signature_ok` when the secret is unset, and gate the whole route on `_app_secret()` being configured (503 otherwise) so an unconfigured deployment refuses inbound webhooks rather than trusting them. Keep the permissive branch behind the existing hermetic-test switch (`strict_limits_enabled()`) rather than behind "secret is empty".
- **Effort:** Low

### Finding #3 — 2FA enrollment, verification and disable endpoints have no rate limit
- **Severity:** High
- **File:** `backend/api/auth.py`
- **Line(s):** 630-675 (`/2fa/enroll`, `/2fa/verify`, `/2fa/disable`)
- **Category:** Missing brute-force protection on an authentication endpoint
- **Evidence:** every other auth route carries an explicit dependency; these three do not.

  ```python
  @router.post("/2fa/verify", summary="Confirm a code to enable 2FA")
  async def twofa_verify(body: CodeIn, user: dict = Depends(get_current_user), ...):

  @router.post("/2fa/disable", summary="Disable 2FA (requires a valid current code)")
  async def twofa_disable(body: CodeIn, user: dict = Depends(get_current_user), ...):
      ...
      if not _verify_totp(row.totp_secret, body.code):
          raise HTTPException(400, "invalid 2FA code")
  ```

  Compare the guarded routes: `/login` `rate_limit(10, 60)`, `/login/2fa` `rate_limit(10, 60)`, `/refresh` `rate_limit(30, 60)`, `/register` `rate_limit(5, 60)`, `/phone/request-otp` `rate_limit(5, 60)` **plus** `check_bucket` per-IP and per-phone, `/phone/verify-otp` `rate_limit(10, 60)` **plus** a persisted `attempts` counter with `_OTP_MAX_ATTEMPTS = 5`.

  `/2fa/disable` has neither a rate limit nor an attempt counter, and `_verify_totp` runs `valid_window=1`, so three 6-digit codes are acceptable at any instant — roughly 3 in 10⁶ per guess with unlimited guesses.
- **Why it's a risk:** an attacker holding a stolen access token (15-minute window, or a hijacked browser session) can brute-force the TOTP code offline-fast and permanently strip 2FA from the account, converting a temporary session compromise into durable single-factor access. The precondition is an authenticated session, which is why this is High rather than Critical.
- **Suggested fix (do NOT apply):** add `dependencies=[rate_limit(5, 60)]` to all three routes and a persisted per-user failure counter on `/2fa/disable` mirroring the `phone_otp.attempts` pattern already implemented in this same file.
- **Effort:** Low

### Finding #4 — `GI_ENV=prod` enforces the strong secret but leaves the refresh cookie insecure
- **Severity:** Medium
- **File:** `backend/api/auth.py` vs `backend/api/config.py`
- **Line(s):** `auth.py:165`; `config.py:95`
- **Category:** Inconsistent environment detection between two security controls
- **Evidence:**

  ```python
  # config.py:95 — accepts BOTH spellings
  return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")
  ```
  ```python
  # auth.py:165 — accepts ONLY "production", and does not .strip()
  production = os.environ.get("GI_ENV", "").lower() == "production"
  response.set_cookie(
      REFRESH_COOKIE, raw,
      max_age=int(ttl.total_seconds()),
      httponly=True, samesite="none" if production else "lax",
      secure=production, path="/")
  ```

  With `GI_ENV=prod` (or `GI_ENV=production ` with trailing whitespace, or `Production`), `is_production()` is `True` — so the app correctly demands a strong `JWT_SECRET` and the operator gets every signal that production hardening is active — while `_set_refresh_cookie` computes `production = False` and issues the 90-day refresh cookie **without `Secure`** and with `SameSite=lax`.
- **Why it's a risk:** a refresh cookie without `Secure` can be transmitted over plaintext HTTP and captured; `SameSite=lax` additionally breaks silent refresh for the native shells (a functional regression that would likely be "fixed" by loosening something else). The two controls disagreeing is the underlying defect — one spelling silently produces a half-hardened deployment.
- **Suggested fix (do NOT apply):** replace the inline check with `from .config import is_production` so a single function defines "production" for the whole codebase.
- **Effort:** Low

### Finding #5 — Production CORS silently falls back to the dev origin list
- **Severity:** Medium
- **File:** `backend/api/config.py` · `deploy/docker-compose.prod.yml`
- **Line(s):** `config.py:72-86`; `docker-compose.prod.yml:71`
- **Category:** Credentialed CORS allowlist wider than intended in production
- **Evidence:**

  ```python
  _env_cors = os.environ.get("CORS_ORIGINS", "").strip()
  CORS_ORIGINS = (
      [o.strip() for o in _env_cors.split(",") if o.strip()] if _env_cors else [
          "http://localhost:5173", "http://127.0.0.1:5173",
          "http://localhost:3000", "http://127.0.0.1:3000",
          "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
          "capacitor://localhost", "https://localhost",
          "http://localhost",
      ]
  )
  ```
  ```yaml
  # docker-compose.prod.yml:70-71
        # Single-origin behind nginx → CORS unused; set only if you split origins.
        CORS_ORIGINS: ${CORS_ORIGINS:-}
  ```

  Compose passes `CORS_ORIGINS` as an **empty string** when the operator has not set it, `.strip()` makes it falsy, and the dev list applies in production. Combined with `main.py:143-149`:

  ```python
  app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                     allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
  ```

  **Explicitly checked and NOT present:** `allow_origins=["*"]`. The brief's Critical condition (wildcard + credentials) does **not** apply, and there are no wildcard-subdomain patterns either.
- **Why it's a risk:** `http://localhost`, `https://localhost`, `http://localhost:3000` and `http://localhost:5173` become credentialed origins against the production API. Any other application, dev server, or locally-installed tool serving content from a localhost origin on a staff machine can issue credentialed cross-origin requests and **read the responses** — the refresh cookie is `SameSite=None; Secure` in production, so it rides along. This is a narrower blast radius than a wildcard, but it is not the intended production posture.
- **Suggested fix (do NOT apply):** make the fallback environment-aware — when `is_production()`, default to an empty list (single-origin behind nginx needs no CORS at all) and require `CORS_ORIGINS` to be set explicitly for the native shell origins.
- **Effort:** Low

### Finding #6 — Rate-limit keys derive from client-controllable headers
- **Severity:** Medium
- **File:** `backend/api/ratelimit.py`
- **Line(s):** 33-50, 121-123
- **Category:** Bypassable throttling (spoofable key) + per-process counter
- **Evidence:**

  ```python
  def _client_ip(request: Request) -> str:
      cf = request.headers.get("cf-connecting-ip")
      if cf:
          return cf.strip()
      xri = request.headers.get("x-real-ip")
      if xri:
          return xri.strip()
      return request.client.host if request.client else "unknown"
  ```

  Both headers are attacker-supplied on any request that reaches the origin without traversing Cloudflare or nginx. Rotating `CF-Connecting-IP: <random>` yields a fresh bucket per request, defeating `/login` (10/60), `/register` (5/60), the OTP toll-fraud budget (`otp:ip:` 3/hour) and the webhook `PenaltyBox` simultaneously. The module documents the trade-off honestly at lines 14-16, and the OTP limiter is the one control that survives — it *also* keys per target phone number (`check_bucket(f"otp:phone:{number}", 3, 3600)`), which no header can influence.

  A second, documented limitation (lines 18-21): the store is per-process, and `deploy/Dockerfile.api` runs **4 uvicorn workers**, so every configured limit is effectively 4× in production.
- **Why it's a risk:** the deployment plan in `docs/NATIVE_APPS.md` §6 adds a Cloudflare Access **Bypass** for `/api/*`, so `/api` becomes reachable by anything that can route to the origin. Unless the Hetzner box's firewall restricts ingress to Cloudflare's ranges, header spoofing turns the login limiter into a no-op.
- **Suggested fix (do NOT apply):** only trust `CF-Connecting-IP`/`X-Real-IP` when the peer address is in a configured trusted-proxy allowlist, falling back to `request.client.host` otherwise; and firewall the origin to Cloudflare IP ranges as part of the deployment runbook. For the multi-worker ceiling, a shared store (Redis) is the documented long-term fix.
- **Effort:** Medium

### Finding #7 — TOTP secrets are stored in plaintext
- **Severity:** Medium
- **File:** `backend/models.py` · `backend/api/auth.py`
- **Line(s):** `models.py:565-566`; `auth.py:639-642`
- **Category:** Sensitive credential material unencrypted at rest
- **Evidence:**

  ```python
  # models.py:559-566
  password_hash = Column(Text, nullable=False)
  ...
  totp_secret  = Column(Text)
  totp_enabled = Column(Integer, server_default=text('0'))
  ```
  ```python
  # auth.py:639-642
  secret = pyotp.random_base32()
  uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name="GI Hub")
  await session.execute(update(users_t).where(users_t.c["username"] == user["username"])
                        .values(totp_secret=secret))
  ```

  Against the brief's criterion: the secret **is** in a column separate from `password_hash` (the minimum bar is met), but it is not encrypted or otherwise protected. Anyone with read access to the `users` table — a database backup, the `pg_dump` produced by `POST /admin/backup`, a SQL-injection read, or a misconfigured `gi_ai_ro` grant (Audit 01 Finding #3) — recovers every enrolled user's TOTP seed and can generate valid codes indefinitely.
- **Why it's a risk:** the second factor becomes worthless against any adversary who has already obtained a database read, which is precisely the scenario 2FA is meant to survive.
- **Suggested fix (do NOT apply):** encrypt `totp_secret` with a key held outside the database (an app-level Fernet/AES-GCM key from the environment, or PostgreSQL `pgcrypto`), decrypting only inside `_verify_totp`. Note that `users` is already REVOKEd from `gi_ai_ro` and excluded from the generic CRUD router, so this is defence-in-depth rather than an open path today.
- **Effort:** Medium

### Finding #8 — 2FA enrollment does not require re-authentication
- **Severity:** Medium
- **File:** `backend/api/auth.py`
- **Line(s):** 630-645
- **Category:** Sensitive account change without step-up authentication
- **Evidence:**

  ```python
  @router.post("/2fa/enroll", summary="Begin 2FA enrollment → secret + QR (not enabled yet)")
  async def twofa_enroll(user: dict = Depends(get_current_user), ...):
      ...
      secret = pyotp.random_base32()
      ...
      .values(totp_secret=secret)
  ```

  A bearer token alone is sufficient; no password re-entry and no existing-code check. The design does guard against lockout correctly — the secret is written on enroll but `totp_enabled` only flips after `/2fa/verify` confirms a code (`auth.py:648-660`), and `/2fa/disable` **does** require a valid current code — so this is specifically about the enroll step.
- **Why it's a risk:** an attacker with a stolen 15-minute access token can enroll *their own* authenticator, then complete `/2fa/verify` to enable it, establishing persistence on the account. The legitimate user is not prompted and would only notice at their next login. Combined with Finding #3 (no rate limit) the same attacker can also strip an existing enrollment.
- **Suggested fix (do NOT apply):** require the current password (or, when already enrolled, a current TOTP code) in the `/2fa/enroll` body, verified with `_verify_password` before the secret is written.
- **Effort:** Low

### Finding #9 — Role, site and warehouse changes do not invalidate outstanding tokens
- **Severity:** Medium
- **File:** `backend/api/admin.py`
- **Line(s):** 180-214 (`update_user`)
- **Category:** Stale authorization claims (privilege lock-in until expiry)
- **Evidence:** `admin.py` imports `revoke_all_sessions` and calls it on password reset (line 229) and user delete (line 262) — but **not** on `update_user`:

  ```python
  await session.execute(update(users_t)
                        .where(users_t.c["username"] == username).values(**values))
  await write_audit(session, actor["username"], "UPDATE_USER", "users", ...)
  # ← no revoke_all_sessions
  ```

  The claims in question are baked into the access token at mint time (`auth.py:118-126`) and read straight back out without a database lookup:

  ```python
  async def get_current_user(cred=Depends(_bearer)) -> dict:
      p = _decode(cred.credentials, "access")
      return _public(p["sub"], p.get("role"), p.get("site_id"), p.get("warehouse_id", ""))
  ```
- **Why it's a risk:** demoting a compromised or departing admin, or re-pinning a user to a different site, leaves their existing access token fully potent for up to 15 minutes with the *old* role, site and warehouse. The window is bounded and the refresh path self-heals (`auth.py:494` re-reads the user row before minting the next access token), which keeps this Medium rather than High. `notifications.py:27-39` (`_ctx`) already demonstrates the correct pattern of re-reading bindings from the live `users` row.
- **Suggested fix (do NOT apply):** call `revoke_all_sessions(session, username, "role-changed")` inside `update_user` whenever `role`, `site_id` or `warehouse_id` actually changes, forcing an immediate re-login.
- **Effort:** Low

### Finding #10 — AI query endpoints write no audit record
- **Severity:** Medium
- **File:** `backend/api/ai/router.py`
- **Line(s):** 440-451 (`/nl-search`), 469-495 (`/query`)
- **Category:** Missing security-relevant logging on a high-risk data path
- **Evidence:** grepping `write_audit` / `_audit` across `ai/router.py`, `ai/analytics.py` and `ai/query_router.py` returns **no audit calls at all** — only comments at `ai/router.py:125-127` noting that *write* actions deliberately flow through the audited services. Reads are unlogged: neither the natural-language question, the generated or templated SQL, nor the row count is recorded, and `system_audit_log` therefore contains no trace of any AI-lane query.

  This directly answers the brief's question 9. The role gates themselves re-verified and correct: `/nl-search` is `require_level(3)`; `/query` is `require_level(2)` with the NL fallback additionally requiring `scope is None and user["level"] >= 3` (`ai/router.py:488`). An expired JWT fails in `get_current_user` before the handler runs, so a mid-query expiry cannot occur — the SSE streams open only after the dependency resolves, and no background continuation re-checks or re-uses the token.
- **Why it's a risk:** Audit 01 established that this lane can reach `phone_otp`, `employees`, `whatsapp_outbox` and the audit log itself, and that the forbidden-table regex is bypassable with schema-qualified names. Without audit rows, a successful exfiltration attempt through that lane is **undetectable and uninvestigable** after the fact.
- **Suggested fix (do NOT apply):** write a `system_audit_log` row per AI query with the username, lane (template/NL), the question, the executed SQL and the row count — the `write_audit` helper is already imported throughout the codebase and audit rows are never deleted (`docs/PROJECT_STATUS.md` §4).
- **Effort:** Low

### Finding #11 — Minimum password length is 6 characters with no complexity or breach check
- **Severity:** Low
- **File:** `backend/api/admin.py` · `backend/api/auth.py`
- **Line(s):** `admin.py:39` (`MIN_PW = 6`), `admin.py:221`; `auth.py:554-555`
- **Category:** Weak credential policy
- **Evidence:**

  ```python
  MIN_PW = 6  # minimum password length for create / reset
  ```
  ```python
  if len(body.password or "") < 6:
      raise HTTPException(422, "password must be at least 6 characters")
  ```

  No complexity requirement, no denylist of common passwords, no breach-corpus check, and no password history. The bcrypt cost of 12 (see Reviewed) means online guessing is slow, and `/login` is rate-limited — but Finding #6 undermines the latter.
- **Why it's a risk:** six-character passwords are trivially guessable from common-password lists; NIST SP 800-63B recommends a minimum of 8 with a breached-password screen.
- **Suggested fix (do NOT apply):** raise `MIN_PW` to 12 for new credentials and screen against a common-password list; keep the existing bcrypt cost.
- **Effort:** Low

### Finding #12 — Logout is best-effort and silently succeeds when revocation fails
- **Severity:** Low
- **File:** `backend/api/auth.py`
- **Line(s):** 500-522
- **Category:** Failure-masking on a session-termination path
- **Evidence:**

  ```python
  @router.post("/logout", summary="Revoke the current refresh-token family")
  async def logout(response: Response, gi_refresh: str | None = Cookie(...), ...):
      if gi_refresh:
          try:
              p = jwt.decode(gi_refresh, JWT_SECRET, algorithms=[JWT_ALG])
              row = (await session.execute(select(refresh_t.c["family_id"]).where(
                  refresh_t.c["refresh_token_jti"] == p.get("jti", "")))).first()
              if row is not None:
                  await _revoke_family(session, row.family_id, "logout")
          except jwt.PyJWTError:
              ... # legacy opaque-cookie path
          await session.commit()
      _clear_refresh_cookie(response)
      return {"logged_out": True}
  ```

  The family-wide revoke is correct (confirmed in Reviewed below). The issues are cosmetic-but-real: when `row is None` — a valid-signature token whose row was already deleted — nothing is revoked yet the response still reports `{"logged_out": True}`; and there is no rate limit on the route. Note this `jwt.decode` **does** pass `algorithms=[JWT_ALG]`, so it is not an `alg:none` vector; it merely omits the `scope` check that `_decode()` performs, meaning an *access* token presented in the refresh cookie would be decoded here (harmless — it carries no `jti`, so the lookup misses).
- **Why it's a risk:** a user told they are signed out may not be, and the outstanding access token remains valid for up to 15 minutes regardless (inherent to stateless JWTs, documented). Low impact.
- **Suggested fix (do NOT apply):** use `_decode(gi_refresh, "refresh")` for consistency and return an explicit `{"logged_out": bool}` reflecting whether a family was actually revoked.
- **Effort:** Low

---

## Long-lived session feature — the 90-day native refresh family (audited separately, per the brief)

There is **no "remember me" checkbox** and no separate persistent-session mechanism — grepping `remember.?me|long.?lived|persistent.?session|keep.?me.?signed` across `backend/api` and `frontend/src` returns only comments describing the RTR design. The long-lived session in this system is the `client_type: "native"` refresh family, audited here on its own terms:

| Property | Implementation | Verdict |
|---|---|---|
| TTL selection | `REFRESH_TTLS = {"web": 7 days, "native": 90 days}` (`auth.py:72`), selected by `LoginIn.client_type: Literal["web","native"]` (line 321) | Client-declared, but **only** a TTL choice — it grants no additional authority. Pydantic `Literal` rejects any other value with a 422 |
| Can a web client claim 90 days? | Yes — `client_type` comes from the login body and is not validated against the User-Agent or origin | **Accepted risk, noted.** The worst case is a browser session lasting 90 days instead of 7; the family is still revocable server-side |
| MFA path | The MFA token carries `extra={"client": body.client_type}` and `login_2fa` reads it back with a whitelist fallback: `client_type = p.get("client") if p.get("client") in REFRESH_TTLS else "web"` (`auth.py:434`) | **Correct** — the claim rides inside the *signed* MFA token, so a client cannot upgrade its TTL between the two legs |
| Rotation | Every `/auth/refresh` mints a new `jti` in the same family and marks the old row `revoked / "rotated" / replaced_by` (`auth.py:486-493`) | Correct — the 90-day window is sliding, not a static 90-day bearer |
| Revocation reach | Logout, admin single-session revoke, admin password reset, user delete and WhatsApp `RESET PASSWORD` all revoke family-wide or user-wide | Correct |
| Storage | Signed JWT in an httpOnly cookie; the DB row stores only the `jti`, never token material (`models.py:576` comment: "sha256 hex, never the raw token" for the legacy table) | Correct |

**No finding** — the mechanism is well constructed. The one item worth the operator's awareness is that `client_type` is self-asserted; because it cannot escalate privilege, it does not meet the bar for a finding.

---

## Reviewed — No Finding

### JWT algorithm and validation (brief item 2 — every call site checked)

There are exactly **three** `jwt.decode`/`jwt.encode` call sites in the backend, plus one in the test harness. All were inspected individually:

| Site | Call | Verdict |
|---|---|---|
| `auth.py:126` | `jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)` | HS256 explicit |
| `auth.py:131` (`_decode`) | `jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])` | **Explicit algorithm list** — `alg:none` and algorithm-confusion are rejected. No `options=` override, so PyJWT's defaults apply: `verify_signature=True`, `verify_exp=True`. Additionally enforces `p.get("scope") != scope → 401`, so access/refresh/MFA tokens are not interchangeable |
| `auth.py:506` (`logout`) | `jwt.decode(gi_refresh, JWT_SECRET, algorithms=[JWT_ALG])` | Explicit algorithm list; omits only the scope check (see Finding #12) |
| `service_tests.py:6542` | `_jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG])` | Test harness |

**Explicitly searched for and NOT found anywhere in the repository:** `options={"verify_signature": False}`, `verify=False`, a `jwt.decode` without `algorithms=`, or any use of `jwt.get_unverified_header`/`decode_complete` to select an algorithm dynamically. The brief's no-exceptions Critical condition does **not** apply.

Background daemons (`scheduler_loop`, `digest_loop`, `weekly_report_loop`) decode no tokens — they operate as system actors on the database directly and expose no HTTP surface (confirmed in Audit 02).

### RTR replay handling (brief item 3 — traced end to end)

The family-revoke-on-replay claim **is** implemented as documented. Path, in order:

1. `/auth/refresh` reads the `gi_refresh` cookie; absent → 401 (`auth.py:449-450`).
2. `_decode(gi_refresh, "refresh")` verifies signature, expiry **and** `scope == "refresh"`; failure clears the cookie and re-raises (lines 453-457).
3. Row lookup by `jti` (line 458-459); no row → clear cookie + 401.
4. **Replay branch** (lines 463-473) — reached when the presented `jti` maps to a row already marked revoked:

   ```python
   if row.is_revoked:
       n = await _revoke_family(session, row.family_id, "reuse-detected")
       await _audit(session, row.username, "SESSION_REUSE",
                    f"refresh-token replay → revoked family "
                    f"{row.family_id.hex[:8]}… ({n} tokens)")  # commits
       _clear_refresh_cookie(response)
       raise HTTPException(401, "refresh token reuse detected — session family revoked")
   ```

   ```python
   async def _revoke_family(session, family_id, reason) -> int:
       res = await session.execute(
           update(refresh_t)
           .where(refresh_t.c["family_id"] == family_id,
                  refresh_t.c["is_revoked"].is_(False))
           .values(is_revoked=True, revoked_at=_now(), revoke_reason=reason))
       return res.rowcount or 0
   ```

   The `WHERE family_id = ... AND is_revoked IS FALSE` predicate catches the **live successor** (the replayed row is already revoked, which is what triggered detection), so the legitimate holder is signed out too — the documented and correct behaviour. Scoping by `family_id` means the user's other devices, which are separate families, are untouched. `_audit()` ends with `session.commit()` (`auth.py:351`), so the revocation is durable before the 401 returns.
5. Expiry check (474-476), user-existence check with `revoke_all_sessions` on a deleted user (477-482), maintenance-mode gate for non-admins (483-484).
6. Rotation (486-497): new `jti`, same `family_id`, old row → `is_revoked / "rotated" / replaced_by`, inheriting `client_type` so a native family keeps its 90-day window.

TTLs confirmed empirically in code: `ACCESS_TTL = timedelta(minutes=15)`, `REFRESH_TTLS = {"web": 7 days, "native": 90 days}` (`auth.py:71-72`) — matching discovery. Access-token expiry is well under the brief's 24-hour threshold.

**Refresh signing key:** the refresh token is signed with the **same** `JWT_SECRET` as the access token, distinguished by the `scope: "refresh"` claim which `_decode` enforces on every use. Documenting the choice as the brief requests: this is defensible — scope separation is cryptographically enforced by the signature, and a distinct key would add key-management burden without changing the threat model, since compromise of the single secret is already total.

**Cookie flags** (`auth.py:157-171`): `httponly=True` always ✓; `secure` and `samesite` are environment-dependent — production sets `Secure` with `SameSite=None` (deliberate, so the Tauri/Capacitor shells' cross-site refresh works), dev sets `SameSite=lax` without `Secure`. The dev posture is **not** a finding per the brief; the production override exists and works, subject to Finding #4's spelling caveat. `SameSite=None` is a conscious trade-off the code documents at lines 158-164; the residual CSRF exposure is limited to forcing a token rotation, since `/auth/refresh` returns the new access token only in a response body that CORS keeps unreadable to non-allowlisted origins.

### Password storage (brief item 4)

- **bcrypt used on every write path** — verified all six call sites: `auth.py:581` (registration), `auth.py:692` (OTP code hashing), `admin.py:119` (`_hash`, used by user create and password reset), `webhook.py:155` (WhatsApp temp password). Verification uses `bcrypt.checkpw` (`auth.py:103`, `auth.py:788`).
- **Cost factor ≥ 12** — `bcrypt.gensalt()` is called without arguments at every site; verified against the installed library (bcrypt 5.0.0) that the default emits `$2b$12$`, i.e. **cost 12**. Meets the bar.
- **No legacy plaintext or weak-hash column** — `models.py` exposes only `password_hash` on `users` (line 559) and `pending_users` (line 308). No `password`, `md5`, `sha1` or unsalted-digest column exists anywhere in the schema. The only other digests are `auth_sessions.refresh_hash` (sha256 of a high-entropy random token — correct use; not a password) and `generated_reports.token_hash` (same).
- **Timing-safe failure path on login** — `auth.py:394-397` runs `_verify_password(body.password, _DUMMY_HASH)` for an unknown username so the response time does not distinguish "no such user" from "wrong password", and both return the same generic 401.
- **No self-service password change endpoint exists.** Grepping `change-password|change_password|new_password|old_password` across `backend/api` returns nothing. The reset paths are admin-driven (`POST /admin/users/{username}/reset-password`) and WhatsApp self-service — both of which revoke sessions. The brief's "password change revokes all outstanding refresh families" condition is therefore satisfied for every path that exists:
  - `admin.py:229` → `revoke_all_sessions(session, username, "admin-reset")`
  - `admin.py:262` (user delete) → `revoke_all_sessions(..., "user-deleted")`
  - `webhook.py:161-165` → hard `DELETE` from both `auth_sessions` and `refresh_sessions`
  - `revoke_all_sessions` itself (`auth.py:214-229`) spans **all** families for the user plus the legacy table.

  The `frontend` does reference "Profile → Change password" in the WhatsApp reset message copy (`webhook.py:169`); since no such endpoint exists in the backend, this is a documentation/UX mismatch rather than a security issue, noted here for the operator.

### TOTP 2FA (brief item 5)

- **Drift window** — `pyotp.TOTP(secret).verify(code, valid_window=1)` (`auth.py:113`). Meets the `≤ 1` requirement (accepts the previous, current and next 30-second step).
- **Storage** — separate column from `password_hash`; **not** encrypted → Finding #7.
- **Enrollment re-authentication** — absent → Finding #8.
- **Lockout safety** — correct: the secret is stored at enroll but `totp_enabled` only flips after a verified code (`auth.py:648-660`), so an abandoned enrollment never locks a user out, because `/login` challenges only on `totp_enabled`.
- **Disable requires a current code** (`auth.py:663-675`) — correct, though unthrottled (Finding #3).
- **Backup / recovery codes** — **not implemented.** No finding: the documented recovery path is the admin-gated `POST /admin/users/{username}/reset-2fa` (`admin.py:235+`), which clears the enrollment under `require_level(4)` and is audited. Since no recovery codes exist, the brief's "hashed, single-use" criteria are not applicable.
- **Exception handling** — `_verify_totp` and `_verify_password` both return `False` on any exception rather than propagating, so a malformed stored secret fails closed.

### Rate limiting coverage (brief item 6)

| Endpoint | Limit | Verdict |
|---|---|---|
| `POST /auth/login` | `rate_limit(10, 60)` | Present |
| `POST /auth/login/2fa` | `rate_limit(10, 60)` | Present |
| `POST /auth/refresh` | `rate_limit(30, 60)` | Present |
| `POST /auth/register` | `rate_limit(5, 60)` | Present |
| `GET /auth/register/sites` | `rate_limit(30, 60)` | Present |
| `POST /auth/phone/request-otp` | `rate_limit(5, 60)` + `check_bucket` per-IP **and** per-phone, 3/hour | Present — the strongest control in the file; the per-phone key is unspoofable |
| `POST /auth/phone/verify-otp` | `rate_limit(10, 60)` + persisted `attempts` counter (max 5, then the code is burned) | Present |
| `POST /whatsapp/webhook` | `PenaltyBox(5 strikes / 10 min → 15 min ban)` | Present, but see Finding #2 — it only counts *invalid signature* strikes |
| `POST /auth/2fa/{enroll,verify,disable}` | **none** | → Finding #3 |
| `POST /auth/logout` | none | Low risk — requires a valid cookie and only revokes the caller's own family |

There is no `/password-reset` or `/totp-verify` route in this API (the brief's names); the equivalents are `/admin/users/{username}/reset-password` (admin-gated, `require_level(4)`) and `/auth/login/2fa` + `/auth/2fa/verify`, all covered above.

### Other items confirmed clean

- **No manual `Authorization` header parsing anywhere.** Grepping `headers.get("Authorization")` / `headers["Authorization"]` / `authorization` across `backend/api` returns nothing — every route obtains identity through the FastAPI `HTTPBearer` dependency (`auth.py:97`, `_bearer` with `auto_error=False`, then an explicit 401 at line 236-237). The brief's Medium condition does not apply.
- **Unauthenticated endpoints, each justified:**
  - `POST /auth/login`, `/login/2fa`, `/register`, `GET /register/sites` — necessarily public, all rate-limited.
  - `POST /auth/refresh`, `/logout` — authenticated by the cookie itself.
  - `GET|POST /whatsapp/webhook` — Meta verify-token + HMAC by design (Finding #2 concerns the unset-secret case only).
  - `GET /reports/weekly-exec/{token}` — capability URL, audited below.
  - `GET /health`, `GET /` — see Audit 02 Finding #11 for the disclosure note.
- **Tokenized weekly-report download** (`weekly_report.py:110-119`, `154-171`): token is `secrets.token_urlsafe(32)` (**256 bits** of entropy from a CSPRNG), stored **only** as sha256 (`token_hash`, unique), with a hard 72-hour expiry enforced on read (`410` when stale) and expired rows purged on each run (line 105-107). Scope is baked into the artifact at render time, so a leaked link cannot be pivoted to another site's data. Not single-use — deliberate, since the same WhatsApp message may be opened repeatedly — which is acceptable given the entropy and TTL. **No finding.**
- **Maintenance mode** correctly refuses non-admin `login`, `login/2fa` and `refresh` (`auth.py:402-403, 432-433, 483-484`) while leaving existing access tokens valid for ≤15 minutes, as documented.
- **JWT claim contents** — `sub`, `role`, `site_id`, `warehouse_id`, `scope`, `iat`, `exp` (`auth.py:118-126`). No secrets, no PII beyond the username. The staleness consequence is Finding #9; the re-fetch pattern to emulate is `notifications.py:27-39`.
- **`_public()` unknown-role fallback** assigns `level: 0` — fail-closed for the level ladder. (Its fail-**open** interaction with `warehouse_scope()` was reported as Audit 02 Finding #9 and is not re-litigated here.)

## Files Reviewed
- `backend/api/auth.py` (complete — 812 lines)
- `backend/api/config.py` (secret resolution, `is_production`, CORS origins)
- `backend/api/ratelimit.py` (complete — 124 lines)
- `backend/api/webhook.py` (handshake, HMAC, command router, password-reset command)
- `backend/api/admin.py` (user create/update/delete, password reset, 2FA reset)
- `backend/api/main.py` (CORS middleware, router mounting, unauthenticated routes, lifespan)
- `backend/api/notifications.py` (as the reference for live-DB binding re-reads)
- `backend/api/ai/router.py` (auth gates on the AI lane, audit-logging check)
- `backend/api/weekly_report.py` (capability-URL token generation and validation)
- `backend/models.py` (credential, session and token columns)
- `backend/requirements.txt` (PyJWT ≥2.8, bcrypt)
- `deploy/docker-compose.prod.yml`, `deploy/Dockerfile.api`, `deploy/.env.example`, `run_api.sh` (environment provenance for `GI_ENV` / `JWT_SECRET` / `CORS_ORIGINS`)

## Files Skipped and Why
- `backend/api/service_tests.py` — test harness. Noted in passing that suite behaviour around `GI_ENV`/`JWT_SECRET` (lines 2017-2022) confirms the production guard is exercised by tests.
- `backend/api/services/whatsapp.py`, `emailer.py` — outbound delivery; relevant to secrets handling (Audit 04), not to session security. The OTP send path was followed only far enough to confirm the code is bcrypt-hashed before dispatch.
- All route modules other than those listed — their authorization gates were the subject of Audit 02 and are not re-audited here.
- `frontend/src/api/client.ts` — read-only reference to confirm no "remember me" mechanism and that the access token is held in `localStorage` while the refresh token stays in an httpOnly cookie. Frontend token handling is out of Phase 1 scope; the `localStorage` choice is worth revisiting in the frontend phase (XSS-readable), and is noted here rather than filed as a backend finding.
- `legacy/**` — frozen Streamlit app with its own separate auth; out of scope. Note that `legacy/services/whatsapp_webhook.py` contains a parallel `verify_signature` implementation, unrelated to the new stack's endpoint.

---

## Tooling Recommendation

Not installed, not run. Checked `backend/requirements.txt`, `requirements.txt` and `frontend/package.json` — none of the following are present.

- **`bandit`** — `B105`/`B106` (hardcoded password strings) would flag `_DEV_JWT_SECRET` in `config.py:90` and `_DUMMY_HASH` in `auth.py:98`. Both are **intentional and safe** (a self-labelled dev placeholder refused in production, and a constant-time comparison decoy respectively), so they belong in a `# nosec` annotation or a bandit baseline rather than being "fixed". `B324` (weak hash) would flag the `hashlib.sha256` uses in `auth.py:152-154`, `webhook.py:92` and `weekly_report.py:113` — all correct applications (HMAC comparison and hashing of high-entropy random tokens, not passwords), so they are also baseline material. The real value of adding bandit here is catching a *future* hardcoded secret, not the current ones.
- **`semgrep` `p/jwt`** — the highest-value ruleset for this codebase. It detects missing `algorithms=` on `jwt.decode`, `verify=False`, `options={"verify_signature": False}` and `alg:none` acceptance. Today it would find nothing (all three call sites are already correct), which is exactly why it is worth wiring into CI: it locks in a property that is easy to regress when someone adds a fourth decode site.
- **`semgrep` `p/secrets`** — generic hardcoded-credential detection across Python, YAML and shell; would cover `deploy/` and `.github/workflows/` as well as the application code.
- **`trufflehog`** — git-history secret scanning, the one tool here that examines what the current tree cannot show. This repository is **public** (`johnthebasemaker/GI_Hub_Project`), and `PROJECT_STATUS.md` §4 records that a real Meta token once briefly reached `.env.example` before being blanked pre-commit — meaning at least one live credential may exist in history even though the working tree is clean. `trufflehog git file://. --since-commit <first>` over the full history, with particular attention to the `EAA…` Meta token prefix and the WhatsApp phone-number ID, is the recommended follow-up. **This is the single most important tooling recommendation in this report** and it belongs to Audit 04 (Secrets), where I will examine git history within the read-only constraints.

---

*Audit 03 complete. Nothing outside `docs/security/reports/` was created or modified; no code, git, database, service, or package operations were performed. Per your instruction, Audit 01's Findings #1/#2 and Audit 02's Findings #1/#2 are not re-litigated here and remain queued for their own remediation tracks.*
