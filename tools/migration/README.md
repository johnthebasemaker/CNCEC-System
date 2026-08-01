# Production Cutover — legacy SQLite → PostgreSQL

> ⏸️ **The Hetzner deployment is PAUSED by decision (2026-07-30)** — the next
> phase is Feature Fine-Tuning and UI Polish. This runbook stays current and
> ready; nothing here is blocked. See
> [`PROJECT_HANDOVER.md`](../../PROJECT_HANDOVER.md).

`cutover_migrate.py` is the one-shot, heavily-verified data migration for
cutover day. It wraps the proven core copier
(`backend/migrate_sqlite_to_postgres.py` — the same code `dual_ci` exercises on
every CI reload) with production pre-flight, post-load fixes and a verification
battery.

## What it does

| Stage | Detail |
|---|---|
| **Pre-flight** | SQLite `integrity_check`; target reachable; **refuses a non-empty target without `--wipe`** |
| **Load** | Full schema from `backend/models.py` (the contract) + all data, chunked. The 3 rowid-ledger tables (`receipts`/`consumption`/`returns`) keep `id := sqlite rowid` so `posted_txn_ref` (`C:{rowid}`/`R:{rowid}`) stays valid. SQLite's loose typing is coerced (junk in numeric/date columns → NULL, counted). PG sequences reset past `MAX(id)`. Source-only columns are reported loudly. |
| **Post-load** | `alembic_version` stamped to the current head (future Alembic migrations apply cleanly). Phone columns (`users`/`pending_users`/`employees`) normalised to the **+E.164** project rule — unparseable values are left untouched and listed. |
| **Verify** | Per-table row-count parity · dual_ci semantic aggregates (stock identity, valuation, FEFO lots, man-hours, SME SQM) · **UOM-conversion integrity** (no zero/NULL factors, no duplicate `SAP+From_UOM` mappings, orphan SAPs listed) · soft-FK orphan scan (ledger→inventory, po_items→PO, dn_items→DN, smr_items→SMR) — advisory unless `--strict` because the legacy ledger never enforced them. |

Exit code `0` only when every blocking check passes.

## Cutover-day runbook

1. **Freeze legacy writes** — stop the Streamlit app (and the legacy WhatsApp
   worker if running): `pkill -f streamlit` on the host.
2. **Final backup** of the source:
   `cp gi_database.db gi_database.cutover-$(date +%Y%m%d).db`
3. **Provision the target** (Hetzner `deploy/` kit): `docker compose up -d db`
   — an EMPTY `gihub` database.
4. **Run the migration** (from the repo root, venv active):

   ```bash
   .venv/bin/python tools/migration/cutover_migrate.py \
       --source gi_database.db \
       --target postgresql+psycopg2://gihub:<pw>@localhost:5432/gihub \
       --wipe
   ```

5. **Manual follow-ups** (the script reminds you):
   - `psql … -f scripts/create_ai_readonly_role.sql` (re-run after ANY reload —
     the REVOKEs are wiped by a wipe-load),
   - **⚠️ Excel data injection (2026-07-13 + 2026-07-18)** — the CNCEC
     workbook data (inventory 306→442 + full ledger backfill + the
     SAP-mapped, RENUMBERED SME masters) lives ONLY in PostgreSQL, never in
     `gi_database.db`. After the load, re-run the FULL chain against the
     target (needs the operator's four workbooks; the tools default to
     reading them from the repo root):

     ```bash
     # preferred since 2026-07-27 — ONE atomic transaction, dry-run by default
     DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC
     DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC --commit
     DATABASE_URL=… .venv/bin/python tools/pg_excel_sync.py --site CNCEC \
         --sme-reseed --commit                       # SME: wholesale replace
     ```

     <details><summary>the older per-kind chain (still supported)</summary>

     ```bash
     DATABASE_URL=… .venv/bin/python tools/excel_sync.py \
         --site CNCEC --commit                       # inventory + ledger (+ SME upserts)
     DATABASE_URL=… .venv/bin/python tools/excel_sync_reconcile.py --commit
     DATABASE_URL=… .venv/bin/python tools/excel_sync.py \
         --site CNCEC --kinds sme-equipment,sme-recipes,sme-materials \
         --sme-reseed --commit                       # SME trio: wholesale replace
     ```
     </details>

     **⚠️ THE SME RESEED IS REQUIRED — two independent reasons.**

     1. The fresh load restores the legacy SQLite's OLD system-code
        numbering; the workbooks carry the renumbered codes (1–10) and the
        exact recipe SAP joins.
     2. **The frozen legacy SQLite has NO `SAP_Code` column on either SME
        table** (`sme_recipe`, `sme_inventory_seed`). A cutover therefore
        lands **86 blank-SAP recipe rows** and one **blank-SAP seed row per
        material**, while the workbook carries 41 SAP-coded recipe rows and
        32 per-component seed rows. An *additive* sync onto that state leaves
        both sets side by side.

     What the additive path does if you run it anyway (it is safe, just
     mixed): the seed loader retires a blank-SAP placeholder ONLY when the
     workbook supplied a real SAP for that `Material_Code` **and** no
     blank-SAP *recipe* line still references it. On a fresh cutover that
     retires 2 genuinely orphaned rows and **holds 20**, printing the reason
     and pointing at the reseed. **The 86 blank-SAP recipe rows are never
     deleted** — they are real recipe data the workbook does not cover
     (measured: zero overlap with the workbook's coded pairs), and removing
     them collapsed SQM coverage to 0.0% across all 29 equipment when it was
     tried.

     After `--sme-reseed --commit` the target converges to the known-good
     shape: **`sme_recipe` 41 rows / 0 blank SAP · `sme_inventory_seed` 32
     rows / 0 blank SAP**. Re-running is a no-op (`+0 new ~0 changed
     =32 unchanged`).

     The reseed aborts if recorded `Done_SQM` would be lost (override:
     `--force-drop-progress` after reading the printout). Confirm the closing
     line reads **`STOCK VERIFICATION: 429/429`** (or the current workbook row
     count). ⚠️ `pg_excel_sync.py` exits **1** when that verification finds
     mismatches even though the commit succeeded — that is a signal, not a
     failed sync.
   - `VACUUM ANALYZE;`,
   - confirm `deploy/.env` secrets on the server (`JWT_SECRET`, `WHATSAPP_*`,
     `SMTP_*`, `EMAIL_LOGISTICS_TO`) and that the file is **`chmod 600`** —
     it holds live Meta credentials and the API prints a warning at boot if it
     is readable beyond its owner.
   - **`PUBLIC_BASE_URL`** must be set to the public origin and must NOT be
     localhost — the API now refuses to boot otherwise (audit A04-F7), because
     the weekly-report WhatsApp links are built from it.
   - **`CORS_ORIGINS`** — blank in production means *no* cross-origin access.
     Browser-only is fine blank; the **native Tauri/Capacitor apps need their
     webview origins listed** or every native API call fails:
     `tauri://localhost,http://tauri.localhost,https://tauri.localhost,capacitor://localhost,https://localhost`
   - **`GI_TRUSTED_PROXIES=*`** — under the Cloudflare Tunnel the box publishes
     no host ports, so the only route in is edge → cloudflared → nginx → api and
     no client can reach the origin to forge `CF-Connecting-IP`. `*` means
     "trust any peer", which is correct here and avoids pinning a container IP
     that changes on every recreate. ⚠️ Never set a *specific* address you are
     unsure of: a non-matching value makes every request key on the proxy's own
     IP, so all users share ONE bucket and `/auth/login` locks out globally.
     If a host port is ever published again, narrow this to the real peer.
   - **`TUNNEL_TOKEN`** — from Zero Trust → Networks → Tunnels → Install
     connector. The stack refuses to start without it. Route the tunnel's public
     hostname to `http://web:80` (the service name, not localhost).
   - re-run `backend/scripts/create_ai_readonly_role.sql` after the final load
     (the AI read-only GRANTs are wiped by every reload); the API logs
     `[ai] read-only wall: OK|DEGRADED` at boot so you can confirm it took.
6. **Point the API at the target** (`DATABASE_URL` in `deploy/.env`), start the
   stack, and run the smoke gates against production:

   ```bash
   DATABASE_URL=… JWT_SECRET=… .venv/bin/python -m backend.api.service_tests   # expect 951/0
   # NOTE: tools/parity_check.py is NOT a production smoke gate — it compares
   # against the frozen SQLite and fails BY DESIGN once the Excel injection
   # is applied. The stock-verification line from excel_sync.py is the
   # production data oracle instead.
   ```

7. **Re-verify any time** without reloading:

   ```bash
   .venv/bin/python tools/migration/cutover_migrate.py --verify-only \
       --source gi_database.cutover-YYYYMMDD.db --target postgresql+psycopg2://…
   ```

## Notes

- The script **never writes to the source** SQLite file (opened read-only).
- New-stack-only tables (`auth_sessions`, `ai_jobs`, `whatsapp_outbox`,
  `email_outbox`, `phone_otp`, `sla_dismissals`, `mh_*`) are created empty —
  they have no SQLite counterpart by design.
- Legacy SQLite **views are not migrated** to Postgres: the FastAPI layer
  computes those aggregations itself (`backend/api/stock.py` — parity-checked
  against the SQLite views by `backend.api.parity_check`).
- SME S6 (master-data CRUD) shipped on cutover day 2026-07-13; this script
  moves the data either way (sme_* tables are ordinary tables).
- **`sme_inventory_seed`'s primary key is `(Material_Code, SAP_Code)`** since
  alembic `a4e9b1c73f28` (2026-07-30). The migration collapses any legacy
  comma-list SAP (`"1041, 1041-1, …"`) to its first SAP and widens the key; it
  **cannot** recover the per-component quantities from a pooled row — nothing
  can, that information was destroyed on load. The workbook reload above is
  what converges them.
