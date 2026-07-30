# Global table filter & sort — run log

**Branch** `feat/global-table-tools` · **Date** 2026-07-29

Project-wide sorting and filtering for every data grid, the SME portal included.

---

## 1. What shipped

`frontend/src/lib/smartTable.tsx` — a drop-in replacement for antd's `Table`.
Every call site imports `Table` from here instead of from `'antd'`; nothing else
at the call site changes. The wrapper walks the column definitions it is handed
and, for each column, derives:

* **a sorter** — on every column with a `dataIndex`, using one numeric-aware
  comparator (`Intl.Collator` with `numeric: true`) that handles numbers,
  numeric strings, `Date`s and text. Blanks sort to the top ascending, so
  clicking a shortfall column surfaces the worst offenders immediately.
* **a checkbox filter** — only where the column turns out to be categorical.

Coverage: **99 `<Table>` instances across 45 files**.

## 2. The four rules, and why

| Rule | Reason |
|---|---|
| No `dataIndex` → no sorter | An "Actions" column of buttons has nothing to sort by. |
| Numeric columns → sorter, no filter | A quantity is a measurement, not a category. A dropdown of 28 distinct shortfall values is noise. |
| Boolean columns → sorter, no filter | A two-option "true/false" dropdown rarely matches what the cell renders (`on`/`off`, a coloured tag), and the sorter already groups them. |
| Server-paginated grids → untouched | Sorting one page of 20 out of 5,000 server-side rows *looks* like it works and silently lies about every other page. |

The last rule is auto-detected, not hand-configured: controlled pagination
(`total` **and** `current` both set) means the server owns paging. Four grids
match — `BrowseTable`, `InventoryAdminPage`, `AuditLogPage`, `MasterDataPage` —
and all four correctly opt out. A call site can force the decision either way
with `smart={true|false}`.

Filters are capped at **30 distinct values**; above **8** the dropdown grows a
search box. Distinct values are sampled from the first 400 rows.

An explicit `sorter` / `filters` / `filterDropdown` on a column is always left
untouched, so any existing hand-tuned column keeps its behaviour.

## 3. UI/UX: zero added chrome

No toolbar, no search bar, no layout change anywhere. The affordances live
entirely inside the header cells antd already draws — a caret pair and, where
warranted, a funnel icon. `showSorterTooltip` is off by default so hovering a
header does not throw a tooltip over the data.

## 4. One thing the screenshots caught

The first visual check showed the Users grid's Role filter listing **raw codes**
(`hod`, `store_keeper`) in a dropdown sitting above cells that render **friendly
labels** ("Head of Department", "Store Keeper"). Derived filter options come from
the raw field, which is right for the other 98 grids but wrong wherever a
`render` maps codes to labels.

Fixed at the source rather than papered over: `UsersPage` now passes an explicit
`filters` array built from the existing `roleOptions`, using the escape hatch the
wrapper documents. The limitation is recorded in the module header so the next
column with a label-mapping render gets the same treatment.

## 5. Verification

| Gate | Before | After |
|---|---|---|
| Playwright E2E | 39 / 39 | **42 / 42** (+3) |
| `backend.api.service_tests` | 921 / 0 | **921 / 0** |
| SME TS↔PY parity | pass | **pass** (954 comparisons) |
| `legacy/bug_check.py` | 599 / 0 | **599 / 0** |
| `tsc --noEmit` · `npm run build` | ✅ | ✅ |
| `oxlint` | warnings only | warnings only (same class as 11 pre-existing files) |
| `gi_database.db` sha256 | `00652932…ba038` | **unchanged** |

New spec: `tests/e2e/specs/table-tools.spec.ts` — three tests on real cloned
data covering sorter attachment (6 of 7 columns; "Actions" excluded), ascending
then descending ordering of the rendered rows, filter presence on text columns
and absence on booleans/action columns, a filter actually reducing the row set,
and the server-paginated opt-out.

### Revert-verification (the tests have teeth)

| Sabotage | Result |
|---|---|
| Never enhance (`enabled = false`) | 2 fail, opt-out test still passes ✅ |
| Always enhance (`enabled = true`) | opt-out test fails ✅ |

The first draft of the spec targeted `/hod/low-stock`, which turned out to have
**zero rows** in the cloned E2E database — it failed loudly rather than passing
vacuously, and was retargeted to `/admin/users`.

## 6. Visually confirmed

Screenshots captured through the E2E harness (`/admin/users` sorted with the
Role dropdown open, and the SME dashboard full-page). Sort carets appear on
every field-backed column of the SME **Full Material Balance** grid; filter
funnels appear on Code / Material Name / UOM and on none of the quantity
columns, as intended.
