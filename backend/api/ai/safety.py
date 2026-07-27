"""
backend/api/ai/safety.py — safe-SQL gate for AI-generated queries.

PG-hardened port of legacy ai/safety.py (the validator that will guard the
Phase AI-5 NL→SQL feature). Same design: intentionally pessimistic pure
functions, no I/O, trivially unit-testable. Structural checks run against a
sanitized copy (comments stripped, string literals blanked) so neither
comment prose nor data values can cause a false reject OR a false accept.

Changes vs the SQLite original:
  - PG-specific destructive/side-effect keywords added (COPY, DO, CALL,
    EXECUTE, PREPARE, LOCK, LISTEN/NOTIFY, RESET, CLUSTER, CHECKPOINT).
  - `auth_sessions` (refresh-token hashes) joins the forbidden tables.
  - pg_catalog / information_schema reads are blocked (schema snooping).
Execution-side defenses (a true read-only PG role + statement_timeout) are
Phase AI-5 concerns; this module is the query-text gate only.
"""
from __future__ import annotations

import re
from typing import Tuple

FORBIDDEN_KEYWORDS = (
    "drop", "delete", "update", "insert", "alter", "attach",
    "pragma", "create", "replace", "vacuum", "reindex",
    "truncate", "merge", "grant", "revoke",
    # PostgreSQL additions
    "copy", "do", "call", "execute", "prepare", "deallocate",
    "lock", "listen", "notify", "reset", "cluster", "checkpoint",
)

# Tables the AI lane must never read. Keep this in sync with the REVOKE/GRANT
# allowlist in backend/scripts/create_ai_readonly_role.sql — this tuple is the
# text-level echo of that database-level wall.
FORBIDDEN_TABLES = (
    # auth / session material
    "users", "pending_users", "auth_sessions", "refresh_sessions", "phone_otp",
    # staff PII (id numbers, names, personal phone numbers)
    "employees", "mh_employees",
    # outbound message bodies + recipient phone numbers / addresses
    "whatsapp_outbox", "email_outbox",
    "app_notifications", "pending_summary_notifications",
    # audit trail and operational internals
    "system_audit_log", "ai_jobs", "bug_reports",
    # uploaded document blobs
    "entry_attachments", "mtc_documents",
)
_FORBIDDEN_TABLE_SET = frozenset(FORBIDDEN_TABLES)

DEFAULT_ROW_LIMIT = 500
HARD_ROW_LIMIT = 2000

# Token boundary so "Created_At" doesn't match "create".
_KW_PATTERN = re.compile(r"(?ix)\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b")

# Table references are RESOLVED, not pattern-matched against bare names. The
# previous `\b(?:from|join)\s+["']?(users|…)` anchored on the name appearing
# immediately after the keyword, so `FROM public.users`, `FROM ONLY users` and
# `FROM "public" . "users"` all sailed through. Capture the whole (optionally
# schema-qualified) reference instead and compare its BASE name.
_IDENT = r'(?:"[^"]+"|[A-Za-z_]\w*)'
_IDENT_RE = re.compile(_IDENT)
_TABLE_REF_PATTERN = re.compile(
    r"(?ix)\b(?:from|join|into|update)\s+(?:only\s+)?"
    r"(?P<ref>" + _IDENT + r"(?:\s*\.\s*" + _IDENT + r")*)")

# PG system catalogs — block schema snooping outright.
_CATALOG_PATTERN = re.compile(r"(?i)\b(?:pg_\w+|information_schema)\b")

# Configuration / environment introspection. These carry no `pg_` prefix, so
# the catalog pattern above never saw them.
_BLOCKED_FUNCS = (
    "current_setting", "set_config", "version", "current_query",
    "inet_server_addr", "inet_server_port", "inet_client_addr", "inet_client_port",
    "txid_current", "dblink", "dblink_connect", "query_to_xml",
    "lo_import", "lo_export",
    "has_table_privilege", "has_database_privilege", "has_schema_privilege",
)
_BLOCKED_FUNC_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(_BLOCKED_FUNCS) + r")\s*\(")
# Niladic forms — valid PostgreSQL with no parentheses at all.
_BLOCKED_NILADIC_PATTERN = re.compile(
    r"(?i)\b(current_user|session_user|current_role|current_catalog"
    r"|current_database|current_schema|current_schemas)\b")

_LIMIT_PATTERN = re.compile(r"(?i)\blimit\s+\d+")


def _base_table_name(ref: str) -> str:
    """Last dotted component of a table reference, unquoted and lowercased:
    `public."users"` → `users`."""
    parts = _IDENT_RE.findall(ref)
    return parts[-1].strip('"').strip().lower() if parts else ""

# Single left-to-right tokenizer: string literal | block comment | line
# comment — whichever matches FIRST wins, so `--` inside a string is string
# content and `'` inside a comment is comment content (correct precedence).
_SQL_TOKEN = re.compile(
    r"""
      (?P<string>'(?:[^']|'')*')   # '...' with '' as the escaped quote
    | (?P<block>/\*.*?\*/)          # /* block comment */
    | (?P<line>--[^\n]*)            # -- line comment
    """,
    re.DOTALL | re.VERBOSE,
)


def _sanitize_for_scan(sql: str) -> str:
    """Copy of `sql` safe to scan: string literals collapsed to '', comments
    replaced by a space. Never executed — only inspected."""
    def _repl(m: "re.Match") -> str:
        return "''" if m.lastgroup == "string" else " "
    return _SQL_TOKEN.sub(_repl, sql)


def is_safe_select(sql: str) -> Tuple[bool, str]:
    """Returns (ok, reason). reason is "" when ok, else a short human-readable
    explanation suitable for showing to the user."""
    if not sql or not sql.strip():
        return False, "Empty query."

    scan = _sanitize_for_scan(sql)

    # Reject multi-statement payloads (trailing semicolon alone is fine).
    parts = scan.split(";")
    significant = [p for p in parts if p.strip()]
    if len(significant) > 1:
        return False, "Multiple statements are not allowed."

    body = significant[0] if significant else scan

    head = body.lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        return False, "Only SELECT queries are allowed."

    m = _KW_PATTERN.search(body)
    if m:
        return False, f"Query contains forbidden keyword: {m.group(1).upper()}."

    for m in _TABLE_REF_PATTERN.finditer(body):
        name = _base_table_name(m.group("ref"))
        if name in _FORBIDDEN_TABLE_SET:
            return False, f"Reads from `{name}` are not allowed via the AI search."

    if _CATALOG_PATTERN.search(body):
        return False, "System catalogs are not accessible via the AI search."

    m = _BLOCKED_FUNC_PATTERN.search(body) or _BLOCKED_NILADIC_PATTERN.search(body)
    if m:
        return False, (f"`{m.group(1)}` (server/configuration introspection) is not "
                       f"available via the AI search.")

    return True, ""


def scrub_sql(sql: str, row_limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Append `LIMIT N` if absent (cap HARD_ROW_LIMIT); strip trailing `;`.
    LIMIT detection runs on the sanitized copy so 'limit' inside a comment or
    string can't fool the row cap. LIMIT lands on a NEW LINE so a trailing
    line comment can't swallow it."""
    body = sql.strip().rstrip(";").strip()
    cap = min(max(1, int(row_limit)), HARD_ROW_LIMIT)
    if _LIMIT_PATTERN.search(_sanitize_for_scan(body)):
        return body
    return f"{body}\nLIMIT {cap}"
