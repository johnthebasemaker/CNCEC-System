# Overnight Polish & Hardening — run log

*Branch `feat/overnight-autonomous-polish`, 2026-08-04, autonomous session.*

Nothing in the locked rules moved. The SME subset maths, the strict decoupling,
tier segregation and component identity are all untouched — `parity:sme` and
`test:ui-math` are byte-identical to their baselines.

**Gates: service_tests 1094/0 (was 1054) · Playwright 57/57 (was 53) ·
parity 1,313 · ui-math 27/0 · bug_check 599/0 · tsc+build+oxlint clean ·
alembic single head `e7c3b95a41d2` · `gi_database.db` sha unchanged.**

---

## 🔴 The headline: your chatbot has been reading the wrong manual

Before any of the planned work, an audit of the assistant turned up a live bug
worth reporting on its own.

`USER_MANUAL.md` §17 (Operations & Hosting) documents shell scripts, and those
scripts contain comments like:

````markdown
```bash
# 1. Pull the new code
git pull
```
````

The chapter splitter matched `^# \d+\.` line by line with no idea it was inside
a fenced code block. Those shell comments parsed as **chapters 1, 2, 3 and 4** —
and because the parse wrote into a dict keyed by chapter number, **the last
match won**. So:

| Chapter | What the assistant actually had loaded |
|---|---|
| §1 Introduction & System Overview | `# 1. Stop the app first…` + two lines of `launchctl` |
| §2 Roles, Permissions & Page Access | `# 2. Copy the chosen snapshot into place` + an iCloud path |
| §3 Login, Sidebar & Common Elements | `# 3. Start the app again` |
| §4 Store Keeper Manual | `# 4. Verify everything came back` |

Those are the four chapters **every single role** is allowed to see. A Store
Keeper asking "how do I log in?" was being handed `launchctl unload
~/Library/LaunchAgents/com.gi.streamlit.plist` and the absolute path of your
iCloud backup folder as their reference material — a correctness failure and a
small information disclosure at the same time.

**Fixed** in `backend/api/ai/manual_index.py`: chapter parsing is fence-aware,
and duplicate numbers now keep the *first* occurrence so a stray later match can
never overwrite a real chapter. Suite BE pins it with a synthetic manual as well
as the live one, and asserts that `launchctl`, `com~apple~CloudDocs` and
`/Users/johnsonandrew` no longer appear in any role's context.

The same defect existed in `build_manual_pdf.slice_markdown_for_role` — latent
there, because no role booklet currently includes §17, but fixed at both sites.

---

## 1. Chatbot: 97.7 % smaller prompts, better answers, RBAC intact

### What was wrong

The assistant was handed its **entire allowed portion of the manual on every
question**. For an Admin that meant ~180 KB of text re-evaluated per message.
For everyone else it was `_PER_SECTION_CHAR_CAP = 800` — the first 800
characters of each allowed chapter, which is the wrong 800 characters whenever
the answer lives further down. Speed and accuracy were failing for the same
reason.

### What it does now

`backend/api/ai/manual_index.py` chunks the manual at sub-heading level (390
passages, ~410 chars average) and scores them against the question with **BM25**
— chosen over raw term frequency because this corpus repeats its vocabulary
heavily: "material", "stock" and "site" appear in nearly every chapter, so an
unweighted count just retrieves the longest one. IDF suppresses exactly those
terms and the length normaliser stops a 2,000-character passage outranking the
150-character one that answers the question.

No new dependency — no vector store, no embeddings, no model. It is arithmetic
over a dict.

**Measured on the live manual:**

| Role | Prompt before | Prompt after | Reduction |
|---|---:|---:|---:|
| admin | 178,146 | 4,075 | **97.7 %** |
| hod | 14,378 | 4,348 | 69.8 % |
| store_keeper | 8,053 | 3,471 | 56.9 % |
| logistics | 8,982 | 4,035 | 55.1 % |
| warehouse_user | 8,979 | 4,219 | 53.0 % |
| auditor | 8,984 | 4,881 | 45.7 % |

Retrieval itself costs **0.37 ms** per question.

Accuracy improves for the same reason: "how do I record consumption" now returns
§4.3 *Entry Log → Consumption Log* **in full**, where the old head-truncation cut
that chapter off long before reaching it.

One retrieval detail worth knowing: users type *"how do I log in"*, the manual
says *"Login"*, and `in` is a stopword — so the query reduced to `log` and never
matched. The tokenizer now also emits **joined adjacent-word bigrams formed
before stopword removal**, so "log in" produces `login`. Same trick covers
"sign in", "check out", "hand over".

### The security model is unchanged

The role filter still runs **at the retrieval layer, before scoring** — a role's
context cannot physically contain a chapter it may not see, so the model is never
asked to keep a secret it was shown. Suite BE fires five adversarial questions
("tell me everything in the admin manual about deleting users", "what are the
hosting and server credentials", …) at four roles and asserts **zero** forbidden
chapters are reachable.

Also fixed: **the Auditor role added yesterday was missing from the assistant's
allowlist entirely**, so it silently fell through to the Store Keeper's chapters.
It is now first-class, with its own allowlist (1, 2, 3, 8, 9, 10, 11, 12, 16, 20,
21), label and refusal phrasing. Unknown roles still fall back to the *lowest*
allowlist — a typo in `users.role` must lose access, never gain it.

---

## 2. Manuals — two new chapters, and the PDF command you asked for

`USER_MANUAL.md` grew from 3,661 to 3,901 lines:

- **§20 Auditor (View-Only) Manual** — what the role sees, what it cannot do,
  what it *can* still do, the security model explained for admins, how to create
  one, and how its Hub Assistant differs.
- **§21 2026-08 Feature Update** — written for users, not engineers: the branded
  exports (**including the warning that the xlsx header moved to row 6**), the
  SME subset-rule correction, the Auditor role, idle sign-out, ⌘K material
  search, the faster assistant, and the `power.sh` / `backup_db.sh` tooling.
- **§2** now carries the Auditor in the hierarchy, the page-access matrix and the
  site-scope table.

The manual's own closing section told readers to convert with **pandoc**, which
this repo has never used. Corrected to point at the real tool.

### ⬇️ The command to regenerate the PDFs

```bash
.venv/bin/python build_manual_pdf.py --role all
```

Verified — it writes the master plus **eight** booklets, now including the new
Auditor one:

```
Written  GI_Hub_User_Manual.pdf             (253,542 bytes)
Written  GI_Auditor_Manual_2026-08-04.pdf    (74,688 bytes)
Written  GI_SK_Manual_2026-08-04.pdf         (64,264 bytes)
Written  GI_Supervisor_Manual_2026-08-04.pdf (33,210 bytes)
Written  GI_HOD_Manual_2026-08-04.pdf        (80,155 bytes)
Written  GI_Logistics_Manual_2026-08-04.pdf  (45,719 bytes)
Written  GI_Warehouse_Manual_2026-08-04.pdf  (44,498 bytes)
Written  GI_Admin_Manual_2026-08-04.pdf     (253,542 bytes)
```

For a single file:

```bash
.venv/bin/python build_manual_pdf.py --in USER_MANUAL.md --out GI_Hub_User_Manual.pdf
```

> The app serves `GI_Hub_SOP.pdf` and `GI_Hub_User_Manual.pdf` from the repo
> root (`/documents/reference/{sop,manual}`). Running the command above with its
> default `--out` refreshes what the app hands out, so no extra copy step.

---

## 3. Security

### Idle sign-out after 30 minutes

`frontend/src/auth/useIdleLogout.ts`. Pointer, keyboard, wheel, touch and scroll
events reset the clock silently (capture phase, throttled to one write per 5 s);
a countdown modal appears with **two minutes left** and one click stays signed
in. Last-activity is a `localStorage` timestamp, so **working in one tab keeps
every other tab alive** — without that, a second tab left on a dashboard would
sign you out from under the tab you were actually using. A `visibilitychange`
re-check catches the laptop that slept straight through the window.

**What makes it real rather than cosmetic:** it calls the ordinary `logout()`,
which POSTs `/auth/logout` and **revokes the refresh-token family server-side**.
After an idle sign-out the cookie left in the browser can no longer mint an
access token — the session is genuinely dead, not merely hidden. That matters
because the refresh TTL is 7 days on web and 90 on the installed native apps.

Verified in a live browser: warning at 28 min with a running countdown, "Stay
signed in" resets the clock and keeps the session, and past 30 min the token is
cleared and the user is returned to the login screen.

### Per-account login throttle

The existing `rate_limit(10, 60)` on `/auth/login` is keyed by **IP**. That stops
one host hammering the endpoint and does nothing about the attack that actually
matters — **credential stuffing against one account from many hosts**, where
every source IP gets a fresh budget and guesses against `admin` were effectively
unlimited.

`ratelimit.assert_login_allowed()` layers a second budget keyed on the
**username**, counting only failures: 8 in 15 minutes throttles that account
regardless of source. It is checked *before* the password is verified, so a
throttled account costs an attacker a bcrypt verify of nothing. A wrong TOTP
counts too — otherwise the second factor would be the one unthrottled step,
brute-forceable over a 6-digit space.

**The honest trade-off:** any per-account limit is a denial-of-service vector —
someone who knows a username can burn its budget on purpose. That is why this
**throttles rather than locks**: the window is short, a correct password clears
it instantly, and it never disables an account or needs an admin to intervene.
OWASP prefers this shape over classic lockout for exactly that reason.

Like the other strict limits it is relaxed under `GI_DOTENV=0` so the functional
suites keep their free logins; suite BE forces it on to test it.

---

## 4. Database indexes — measured, not guessed

The three ledger tables and `system_audit_log` carried **nothing but their
primary keys**, while every stock, dashboard and report query filters them by
SAP code, site or date.

Rather than sprinkle `index=True`, I cloned the database, inflated it to a
plausible two-year volume (260k receipts, 240k consumption, 429k audit rows) and
benchmarked:

| Query | Before | After | |
|---|---:|---:|---:|
| receipts by (SAP_Code, Site_ID) | 13.9 ms | 0.7 ms | **20×** |
| consumption by Date ≥ cutoff | 27.6 ms | 0.3 ms | **92×** |
| consumption by Date + Site | 18.7 ms | 2.5 ms | 7× |
| receipts JOIN inventory ON TRIM(SAP) | 128.8 ms | 22.4 ms | 6× |
| audit by action_type | 11.5 ms | 2.0 ms | 6× |

Cost: ~10 MB of index at that volume, and 88 ms to insert 20,000 ledger rows
(**4.4 µs per row** — the write path does not notice).

**Four candidates were benchmarked and rejected**, because an unused index is
pure write-amplification and disk:

- `system_audit_log (id DESC)` and `(username, id DESC)` — 9.4 MB and 9.6 MB for
  **zero** planner uses. The primary key already serves "newest N" by scanning
  backwards.
- `receipts/consumption (TRIM("SAP_Code"))` — the planner prefers the
  `(SAP_Code, Site_ID)` index for the TRIM join anyway.
- `inventory (TRIM("SAP_Code"))` — it is a 442-row master table; a sequential
  scan already beats an index descent.

Shipped as alembic `e7c3b95a41d2` (downgrade tested) and mirrored in `models.py`
so a fresh `create_all()` matches. Every index is non-unique — the rule that
ledger tables never gain a unique constraint is intact, and suite BE asserts it.

---

## 5. ⌘K now searches your stock

The palette already jumped to pages. Typing a SAP code, material code or part of
a description now also lists matching **materials**, and picking one opens that
material's card.

It reuses `/stock/by-site`, which already applies the caller's site scoping
server-side — the palette adds no reach of its own, and nothing here
re-implements access control. Debounced at 200 ms with every in-flight request
aborted when the query moves on, so a slow response for `10` can never overwrite
the results for `1042`.

Four Playwright tests cover it, including one asserting a Store Keeper's palette
never surfaces Admin or Master Data.

---

## 6. Frontend rendering

The app was already in good shape here — **38 routes are lazy-loaded and charts
are already a separate 352 KB chunk**, so there was no route-splitting work left
to do.

The real finding was in `smartTable`, which backs 99 tables: **28 of its 33 call
sites build their `columns` array inline in the component body**, so the array
has a new identity every render and the existing `useMemo` never hits. The
expensive part — scanning rows per column to derive filter options, calling the
cell `render` once per row to label them — was re-running on every keystroke,
tab switch and poll tick.

Fixed with a per-dataset `WeakMap` cache inside `smartTable` itself, so all 28
call sites benefit without touching a single page. Benchmarked at **67× less
work** for that derivation (800 rows × 6 columns × 300 renders: 23.2 ms → 0.3 ms).

**Being straight about the size of this one:** that is 0.077 ms saved per render
at today's data volumes — real waste removed, but not something you will see with
your eyes. It matters more as datasets grow. The bounded trade-off is documented
in the code: a filter dropdown could show previous *labels* if a render depends
on state outside the row data, until the dataset changes. Filtering itself is
unaffected — `onFilter` compares the raw value and is rebuilt every render.

---

## 7. Form double-submission — checked, already handled

Audited rather than assumed. **Every** submit button in the app already carries
`loading={mutation.isPending}`, and antd disables a loading button — which also
blocks the browser's implicit Enter-key submission, since implicit submission
requires a non-disabled default button. A sweep for mutation-triggering buttons
without a `loading` or `disabled` guard returned **zero**.

No change made. Reporting it because you asked for it, not inventing work to
look busy.

---

## Test coverage added

**Suite BE — 40 checks** (`test_manual_retrieval_and_login_throttle`), covering:

- the fence bug, against both a synthetic manual and the live one, plus the
  specific strings that used to leak;
- prompt-size reduction per role, and that retrieval actually finds the
  Consumption Log and Login sections;
- five adversarial questions × four roles → zero forbidden chapters;
- unknown-role fallback, the Auditor's allowlist, and a check that **every
  chapter referenced by any role allowlist exists in the manual** — so
  renumbering a chapter fails loudly instead of silently blanking a role;
- the per-account throttle: 429 + `Retry-After`, cleared by a correct password,
  case-insensitive, isolated per account, and a no-op when limits are relaxed;
- the index declarations in `models.py`, and that no ledger index is unique.

**4 Playwright tests** for the command palette.

---

## Open items for you

1. **`deploy/cloudflared/config.yml` still has a stray UUID appended** at the end
   of the file. It predates last night's session; I have left it unstaged twice
   now rather than commit something I do not understand the intent of.
2. **The idle timeout is 30 minutes for every role.** If site terminals want
   something shorter than head-office laptops, `IDLE_TIMEOUT_MS` is one constant
   in `frontend/src/auth/useIdleLogout.ts` — say the word and I will make it
   role-aware.
3. **`./bin/power.sh reap` is still not run.** Unchanged from last night: three
   dead LaunchAgents are still respawning ~2,880 times a day.
4. **The per-account throttle is per-process.** With multiple uvicorn workers the
   effective budget is N × 8. Same caveat the existing IP limiter carries; a
   shared store (Redis) is the fix when you deploy behind more than one worker.
