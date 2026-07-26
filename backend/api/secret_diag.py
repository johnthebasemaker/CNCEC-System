"""
backend/api/secret_diag.py — startup secret diagnostic (Phase 2 Theme D).

Logs a names-only set/placeholder/empty/unset table of every documented
operator-pending secret at boot, so the running process — not a
hand-maintained status document — is the source of truth for which
security controls are configured (Audit 04 Finding #2: PROJECT_STATUS.md
drifted from deploy/.env in both directions and mis-prioritised a High).

Values NEVER appear in the output. The only derived detail printed is
whether PUBLIC_BASE_URL points at localhost, because that specific
misconfiguration silently breaks outbound report links (Finding #7).
"""
from __future__ import annotations

import os

from .config import LOADED_ENV_FILES, is_production

# Values published in this repo's docs/workflows prove nothing about the
# environment being configured — report them as placeholders. Compared,
# never printed.
_PLACEHOLDER_PREFIXES = ("CHANGE", "REPLACE", "TODO", "FIXME", "XXX")
_PUBLISHED_VALUES = frozenset({
    "dev-insecure-change-me-not-for-production-use-0123456789",
    "ci-only-service-test-secret-key-32bytes-min",
})

# (env key, required in production) — consolidated from Audit 04
# "Operator-Pending Secrets". Required-flag drives the WARNING line only;
# fail-fast enforcement stays with the dedicated guards (jwt_secret(),
# public_base_url()).
KEYS: tuple[tuple[str, bool], ...] = (
    ("JWT_SECRET", True),
    ("POSTGRES_PASSWORD", True),
    ("PUBLIC_BASE_URL", True),
    ("WHATSAPP_TOKEN", False),
    ("WHATSAPP_PHONE_NUMBER_ID", False),
    ("WHATSAPP_APP_SECRET", False),
    ("WHATSAPP_WEBHOOK_VERIFY_TOKEN", False),
    ("SMTP_HOST", False),
    ("SMTP_USER", False),
    ("SMTP_PASS", False),
    ("EMAIL_LOGISTICS_TO", False),
    ("GI_AI_RO_URL", False),
    ("CORS_ORIGINS", False),
    ("AWS_ACCESS_KEY_ID", False),
    ("AWS_SECRET_ACCESS_KEY", False),
    ("AWS_S3_BUCKET", False),
)


def state_of(value: str | None) -> str:
    """Classify one env value. Returns 'unset' | 'empty' | 'placeholder' | 'set'."""
    if value is None:
        return "unset"
    v = value.strip()
    if not v:
        return "empty"
    if v in _PUBLISHED_VALUES or v.upper().startswith(_PLACEHOLDER_PREFIXES):
        return "placeholder"
    return "set"


def secret_states() -> list[tuple[str, str]]:
    """(key, state) for every documented key, in KEYS order."""
    out: list[tuple[str, str]] = []
    for key, _required in KEYS:
        state = state_of(os.environ.get(key))
        if key == "PUBLIC_BASE_URL" and state == "set":
            v = os.environ[key]
            if "localhost" in v or "127.0.0.1" in v:
                state = "set (localhost!)"
        out.append((key, state))
    return out


def log_secret_diagnostic() -> None:
    """Print the boot-time secret-state table. Names and states only."""
    env_name = "production" if is_production() else \
        os.environ.get("GI_ENV", "").strip() or "dev"
    print(f"[config] environment: {env_name} · "
          f"env files loaded: {len(LOADED_ENV_FILES)}")
    print("[config] secret state (names only, values never logged):")
    states = dict(secret_states())
    for key, _required in KEYS:
        print(f"[config]   {key:<32} {states[key]}")
    if is_production():
        missing = [k for k, required in KEYS
                   if required and states[k] not in ("set",)]
        if missing:
            print(f"[config] WARNING: required production secret(s) not "
                  f"properly set: {', '.join(missing)}")
