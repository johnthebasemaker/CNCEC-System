"""
backend/api/config.py — API configuration.

The API is Postgres-first (async). It reads DATABASE_URL and normalises it to the
asyncpg driver, so the same env var used by the migration/dual-CI tooling (which
uses the sync psycopg2 driver) also works here without editing.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_env_files() -> list[str]:
    """Bare-metal convenience: load repo-root `.env` then `deploy/.env` so a
    plain `uvicorn backend.api.main:app` sees the same WhatsApp/SMTP secrets
    docker-compose injects in production. Variables already present in the
    process environment ALWAYS win (override=False), so compose/systemd/CLI
    settings are never clobbered. Set GI_DOTENV=0 to skip entirely —
    service_tests do, so CI never depends on a developer's local secrets."""
    if os.environ.get("GI_DOTENV", "1").strip().lower() in ("0", "false", "no"):
        return []
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []
    root = Path(__file__).resolve().parents[2]
    loaded: list[str] = []
    for p in (root / ".env", root / "deploy" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)
            loaded.append(str(p))
            _warn_if_world_readable(p)
    return loaded


def _warn_if_world_readable(path: "Path") -> None:
    """Audit A04-F3: deploy/.env ships live Meta credentials and was mode 0644 —
    readable by every account on the host, including the production box where it
    sits alongside other services. `chmod 600` is an operator ritual nobody is
    reminded of, so say it out loud at load time. Never fatal: a wrong mode must
    not stop the app from serving.
    """
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            print(f"[config] WARNING: {path} is mode {mode:04o} — readable beyond "
                  f"its owner. It holds live credentials; run: chmod 600 {path}")
    except OSError:
        pass


# Runs at import time, BEFORE any os.environ reads below (and before the other
# api modules read WHATSAPP_*/SMTP_*/JWT_SECRET lazily at request time).
LOADED_ENV_FILES = _load_env_files()

# Local default: the throwaway Postgres 16 cluster on port 5433 (trust auth, no
# password), database `gihub` — the one the migration/dual-CI already populate.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres@127.0.0.1:5433/gihub"


def async_database_url() -> str:
    """Return an asyncpg SQLAlchemy URL, normalising common Postgres URL forms.

    Accepts the sync forms that the rest of the tooling uses (psycopg2 / bare
    postgres://) and rewrites them onto the async driver. A URL that already
    names an async driver is passed through untouched.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return DEFAULT_DATABASE_URL
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    # Anything else (e.g. an explicit async URL for another dialect) is honoured
    # as-is; the API is designed and verified against Postgres.
    return url


# CORS origins. In production behind a single-origin reverse proxy (nginx serves
# the SPA and proxies /api → the API), CORS isn't needed at all. If you ever split
# origins, set CORS_ORIGINS as a comma-separated env var; otherwise the dev
# defaults (the Vite/CRA dev servers) apply.
def _is_production_env() -> bool:
    """Module-level twin of is_production() — CORS_ORIGINS is computed at import
    time, before the function below is defined."""
    return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")


_env_cors = os.environ.get("CORS_ORIGINS", "").strip()
# Audit A03-F5: docker-compose passes CORS_ORIGINS as an EMPTY string when the
# operator hasn't set it, which .strip() makes falsy — so the dev list below
# applied in production, making http://localhost{,:3000,:5173} credentialed
# origins against the live API. Behind the single-origin nginx proxy no CORS is
# needed at all, so production defaults to nothing and must opt in explicitly
# (the native shells' fixed origins go in CORS_ORIGINS on the deploy box).
CORS_ORIGINS = (
    [o.strip() for o in _env_cors.split(",") if o.strip()] if _env_cors
    else [] if _is_production_env() else [
        "http://localhost:5173", "http://127.0.0.1:5173",   # Vite default
        "http://localhost:3000", "http://127.0.0.1:3000",   # CRA / Next default
        # Native app shells (built with VITE_API_URL → cross-origin calls).
        # These are fixed webview origins, not attacker-choosable ones:
        "tauri://localhost", "http://tauri.localhost",       # Tauri macOS/Linux · Windows
        "https://tauri.localhost",
        "capacitor://localhost", "https://localhost",        # Capacitor iOS · Android
        "http://localhost",                                  # Capacitor androidScheme=http builds
    ]
)


# --- environment + secrets ---------------------------------------------------
# The dev JWT signing key. Deliberately long (≥32 bytes) so PyJWT doesn't warn
# about HMAC key length in local dev — but it is refused in production.
_DEV_JWT_SECRET = "dev-insecure-change-me-not-for-production-use-0123456789"

# Audit A04-F4: the production guard rejected a missing, short, or dev-default
# secret — but the CI/test key is 43 chars and none of those, so it PASSED. That
# string appears in five docs and two workflows and is the one every developer
# copy-pastes, which makes it the value most likely to be pasted into a .env
# "just to get the server up". Any published constant is refused in production
# regardless of length; add new ones here rather than trusting the length check.
_PUBLISHED_SECRETS = frozenset({
    _DEV_JWT_SECRET,
    "ci-only-service-test-secret-key-32bytes-min",   # docs §8 + CI workflows
    "CHANGE_ME",                                     # deploy/.env placeholder
    "CHANGE_ME_run_openssl_rand_hex_32",             # .env.example placeholder
    "jwt_secret",
    "changeme", "change-me", "secret", "password",
})


def is_production() -> bool:
    """True when GI_ENV names a production environment."""
    return os.environ.get("GI_ENV", "dev").strip().lower() in ("prod", "production")


def jwt_secret() -> str:
    """Resolve the JWT signing key.

    In production (GI_ENV=production) a strong secret is MANDATORY: a missing,
    too-short (<32 chars), publicly-published, or dev-default key raises at
    startup — the app refuses to boot with an insecure signing key. In dev it
    falls back to a long-but-obvious placeholder so local runs work without any
    setup.
    """
    s = os.environ.get("JWT_SECRET", "").strip()
    if is_production():
        if not s or len(s) < 32:
            raise RuntimeError(
                "JWT_SECRET must be set to a strong secret (≥32 chars) when "
                "GI_ENV=production — refusing to start with an insecure signing key.")
        if s in _PUBLISHED_SECRETS:
            raise RuntimeError(
                "JWT_SECRET is a publicly published placeholder/test value — "
                "refusing to start. Generate a real one: openssl rand -hex 32")
        return s
    return s or _DEV_JWT_SECRET


def public_base_url() -> str:
    """Base URL for outbound links (weekly-report capability URLs, etc).

    Audit A04-F7: this silently fell back to http://localhost:8000, so an unset
    variable in production produced WhatsApp links that resolve to the
    RECIPIENT'S own device — the link fails quietly and a 256-bit capability
    token has been broadcast for nothing. Fail fast in production instead,
    mirroring the JWT_SECRET pattern.
    """
    v = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if is_production():
        if not v:
            raise RuntimeError(
                "PUBLIC_BASE_URL must be set when GI_ENV=production — outbound "
                "report links would otherwise point at localhost.")
        if "localhost" in v or "127.0.0.1" in v:
            raise RuntimeError(
                f"PUBLIC_BASE_URL={v!r} points at localhost — outbound links "
                "must use the public hostname in production.")
    return v or "http://localhost:8000"
