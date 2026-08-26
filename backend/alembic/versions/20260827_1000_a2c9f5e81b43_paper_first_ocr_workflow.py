"""paper first — the OCR workflow, and the four people who touch a number

THE WORKFLOW INVERTS. Phase 5 ran SK -> supervisor -> HOD, and the supervisor
was forbidden from touching a material line because the store keeper had counted
it: "a supervisor whose numbers look bad has both the motive and the opportunity
to adjust the consumption they are being measured against". Phase 9d runs
supervisor -> SK -> HOD, and the supervisor now AUTHORS those numbers, because
the paper genuinely originates in the field.

⚠️ THAT CONTROL IS NOT BEING DROPPED, IT IS BEING REPLACED. The operator's brief
asked for one colour — the SK's edits in red, so an HOD can see the store keeper
altered the supervisor's claim. That is half a control. The other half is that
nothing would show what the SUPERVISOR changed from what the camera read, so a
supervisor could simply overwrite the machine's reading of their own handwriting
and no one could tell. Hence four layers, each with an owner and a timestamp:

    OCR_Qty          what the camera saw          grey    never editable
    Supervisor_Qty   what the supervisor filed    amber   when != OCR_Qty
    SK_Qty           what the store keeper set    red     when != Supervisor_Qty
    Actual_Qty       what the HOD settled         purple  when != SK_Qty

`Original_Qty` (Phase 5) stays as it was: the first number ever written on the
row. The new columns are the chain, not a replacement for it.

────────────────────────────────────────────────────────────────────────────
⚠️ APPROVAL NOW MOVES STOCK, WHICH IT NEVER DID BEFORE (ruling Q1-b). Phase 5's
`post_progress` posted AREA to `sme_sqm_progress` and nothing else; material
left the shelf through the separate `pending_issues -> consumption` path. From
9d the execution entry is the ONLY way lining material is deducted, so approval
writes `consumption` rows too. `Consumption_ID` on each material line is the
handle that makes that idempotent — a second approval finds the row already
posted rather than deducting twice, which is the single highest-severity risk in
this phase.

⚠️ AND THE QSEP GATE MOVES WITH IT (ruling Q2-D). MTC and QC clearance were
enforced on `stage_consumption`, i.e. before material left the store. On a
paper-first flow the drum was emptied days ago, so a hard block can only strand
the record — stock then silently overstates. The gate therefore blocks by
DEFAULT and an HOD may override it explicitly, with a written reason and a
notification to the Head of Qualities. `QSEP_Override_*` is that record.

────────────────────────────────────────────────────────────────────────────
⚠️ THE LEGACY STATES ARE DRAINED, NOT DELETED (ruling Q3). `DRAFT_SK` and
`PENDING_SUPERVISOR` rows belong to a workflow that no longer exists — there is
no step that can advance them. Deleting the states would strand any live row
forever, so `data_upgrade` REJECTS them with a reason that names the change, and
the state machine simply stops issuing new ones. Zero such rows exist in the
local mirror; production may differ, and the count is printed.

Revision ID: a2c9f5e81b43
Revises: f4b8e2c07d15
Create Date: 2026-08-27 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2c9f5e81b43"
down_revision: Union[str, None] = "f4b8e2c07d15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENTRY_COLS = [
    # ── provenance: which paper, which photo, what the model actually said ──
    ("Form_UUID", sa.Text(), True, None),
    ("OCR_Job_ID", sa.Integer(), True, None),
    ("OCR_Image", sa.LargeBinary(), True, None),
    ("OCR_Image_Mime", sa.Text(), True, None),
    # The model's own output, verbatim. When a quantity is disputed six weeks
    # later, "what did the machine read" is answerable only if it was kept.
    ("OCR_Raw_JSON", sa.Text(), True, None),
    ("OCR_Confidence", sa.Float(), True, None),
    ("OCR_Model", sa.Text(), True, None),
    # ⚠️ 'ocr' | 'manual' | 'legacy'. A hand-typed entry must never be mistaken
    # for a scanned one: the grey OCR layer is empty for both, and only this
    # says whether that means "the model read nothing" or "there was no model".
    ("Entry_Origin", sa.Text(), False, sa.text("'manual'")),
    # ── the SK's verification step (new middle of the chain) ────────────────
    ("sk_verified_at", sa.DateTime(), True, None),
    ("sk_edited", sa.Boolean(), False, sa.text("false")),
    ("SK_Edit_Reason", sa.Text(), True, None),
    # ── the QSEP override (ruling Q2-D) ─────────────────────────────────────
    ("QSEP_Override", sa.Boolean(), False, sa.text("false")),
    ("QSEP_Override_Reason", sa.Text(), True, None),
    ("QSEP_Override_By", sa.Text(), True, None),
    ("QSEP_Override_At", sa.DateTime(), True, None),
    # ── stock, which approval now posts ─────────────────────────────────────
    ("Stock_Posted_At", sa.DateTime(), True, None),
    ("WBS_Number", sa.Text(), True, None),
]

_MATERIAL_COLS = [
    # ⚠️ Row_Index IS THE MAPPING. The QR carries no material list, so a
    # handwritten quantity is matched to a material by its POSITION on the
    # printed page. Without this column a re-read of the same photo could not
    # be checked against the sheet it came from.
    ("Row_Index", sa.Integer(), True, None),
    ("OCR_Qty", sa.Float(), True, None),
    # The raw string, kept beside the number: "2+3" and "~4" are answerable
    # questions, and a null quantity with no text is an unanswerable one.
    ("OCR_Qty_Text", sa.Text(), True, None),
    ("OCR_Lot_Text", sa.Text(), True, None),
    ("Supervisor_Qty", sa.Float(), True, None),
    ("SK_Qty", sa.Float(), True, None),
    ("sk_edited", sa.Boolean(), False, sa.text("false")),
    # The `consumption` row this line became. The double-deduction guard.
    ("Consumption_ID", sa.Integer(), True, None),
    # Set when the benchmark says this quantity is implausible for the area
    # reported — a 10x reading is far likelier a misread digit than real use.
    ("Plausibility_Flag", sa.Text(), True, None),
]


def upgrade() -> None:
    for name, type_, nullable, default in _ENTRY_COLS:
        op.add_column("sme_execution_entry",
                      sa.Column(name, type_, nullable=nullable,
                                server_default=default))
    for name, type_, nullable, default in _MATERIAL_COLS:
        op.add_column("sme_execution_entry_material",
                      sa.Column(name, type_, nullable=nullable,
                                server_default=default))

    # The entry a photo belongs to, and the guard against filing one sheet
    # twice, are the same lookup.
    op.create_index("ix_execution_entry_form", "sme_execution_entry",
                    ["Form_UUID"])
    data_upgrade(op.get_bind())


def data_upgrade(conn) -> None:
    """⚠️ DRAIN THE LEGACY STATES (ruling Q3), and SAY HOW MANY.

    `cutover_migrate.py` replays this step when it builds a schema from
    `models.py` and stamps alembic, so it must be idempotent and must not
    assume the rows exist. Rejecting is the only honest terminal move: these
    entries belong to a workflow with no remaining step that can advance them,
    and leaving them in a state nothing can action is how a queue quietly
    accumulates work nobody can finish.
    """
    stranded = conn.execute(sa.text(
        "SELECT COUNT(*) FROM sme_execution_entry "
        "WHERE status IN ('DRAFT_SK', 'PENDING_SUPERVISOR')")).scalar() or 0
    if stranded:
        conn.execute(sa.text(
            "UPDATE sme_execution_entry SET status = 'REJECTED', "
            '"Reject_Reason" = COALESCE("Reject_Reason", \'\') || '
            "'[Phase 9d] The store-keeper-first workflow was replaced by the "
            "paper-first one on 2026-08-27. This entry was still waiting on a "
            "step that no longer exists; raise it again from a printed "
            "consumption form.', updated_at = CURRENT_TIMESTAMP "
            "WHERE status IN ('DRAFT_SK', 'PENDING_SUPERVISOR')"))
        print(f"[9d] drained {stranded} entry/entries out of the retired "
              f"DRAFT_SK / PENDING_SUPERVISOR states")

    # Everything that already exists predates the OCR lane. Marking it 'legacy'
    # rather than leaving the default 'manual' keeps a real distinction: these
    # were never offered a camera, and a report that counts "manual entries"
    # should not silently absorb them.
    conn.execute(sa.text(
        "UPDATE sme_execution_entry SET \"Entry_Origin\" = 'legacy' "
        "WHERE \"Entry_Origin\" = 'manual' AND created_at < CURRENT_TIMESTAMP"))


def downgrade() -> None:
    op.drop_index("ix_execution_entry_form", table_name="sme_execution_entry")
    for name, *_ in _MATERIAL_COLS:
        op.drop_column("sme_execution_entry_material", name)
    for name, *_ in _ENTRY_COLS:
        op.drop_column("sme_execution_entry", name)
