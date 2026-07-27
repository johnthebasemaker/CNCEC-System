-- backend/scripts/create_ai_readonly_role.sql — Phase AI-5
-- ============================================================================
-- The NL→SQL feature executes LLM-generated SELECTs. The application-level
-- safety gate (backend/api/ai/safety.py) is the first wall; this role is the
-- second: a TRUE PostgreSQL read-only login the AI engine connects as, so
-- even a gate bypass physically cannot write, and a runaway query dies at
-- the role-level statement_timeout.
--
-- Run once per database (idempotent):
--   psql "$DATABASE_URL" -f backend/scripts/create_ai_readonly_role.sql
--
-- Local dev (trust auth): no password needed. Production: set one —
--   ALTER ROLE gi_ai_ro PASSWORD '...';
-- and point GI_AI_RO_URL at it (postgresql+asyncpg://gi_ai_ro:...@host/db).
-- ============================================================================

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gi_ai_ro') THEN
      CREATE ROLE gi_ai_ro LOGIN;
   END IF;
END
$$;

-- Hard runtime caps for every connection this role makes.
ALTER ROLE gi_ai_ro SET statement_timeout = '5s';
ALTER ROLE gi_ai_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE gi_ai_ro SET default_transaction_read_only = 'on';

GRANT CONNECT ON DATABASE gihub TO gi_ai_ro;
GRANT USAGE ON SCHEMA public TO gi_ai_ro;

-- ---------------------------------------------------------------------------
-- ALLOWLIST (Phase 2 · Theme B, audit A01-F2 / A01-F3)
--
-- This used to be `GRANT SELECT ON ALL TABLES` + `ALTER DEFAULT PRIVILEGES …
-- GRANT SELECT` minus a five-table REVOKE. That model failed open twice over:
-- every table a future migration adds was readable automatically, and the
-- REVOKE line silently omitted phone_otp, employees, whatsapp_outbox,
-- email_outbox, app_notifications and system_audit_log — all of which the AI
-- lane could read outright, no gate bypass required.
--
-- Inverted: nothing is readable unless it is named below. Keep this list in
-- sync with SCHEMA_HINT in backend/api/ai/analytics.py (what the model is told
-- it may query) and with FORBIDDEN_TABLES in backend/api/ai/safety.py (the
-- text-level echo of this wall).
-- ---------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM gi_ai_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM gi_ai_ro;

GRANT SELECT ON
    inventory,
    receipts,
    consumption,
    returns,
    pr_master,
    purchase_orders,
    sme_recipe,
    sme_equipment,
    sme_sqm_progress
TO gi_ai_ro;
