"""ai_jobs: index the two scans, and stop keeping finished images forever

Revision ID: b8d3f1a72c94
Revises: a2c9f5e81b43
Create Date: 2026-09-01 10:00:00

TWO PROBLEMS ON ONE TABLE, both of which get worse the more the OCR features
are used — which is to say, both of which were invisible during development and
would have surfaced only in production.

**1. Two sequential scans over rows that hold base64 images.**

`ai_jobs` had exactly one index, on `actor`. Neither hot query used it:

  · the startup orphan sweep — `WHERE status IN ('queued','running')` — runs on
    every boot of every worker and scanned the whole table;
  · the submission-summary cache — `WHERE kind = … AND status = 'done' AND
    payload_json = … AND created_at >= …` — runs whenever a review screen opens.

A scan is cheap over narrow rows. These rows are not narrow: `payload_json`
carries a base64 photograph, so every scanned row drags its image through
shared buffers to answer a question about a status column.

**2. The images were never released.**

`payload_json` was written at queue time and never cleared, so a finished job
kept its photograph indefinitely. The form lane made this concrete: the image is
ALSO stored on `sme_execution_entry.OCR_Image` (where the row-crop viewer needs
it), so every uploaded form was held twice, one copy in a text column nothing
would ever read again.

`data_upgrade` reclaims the existing ones. Going forward the workers clear the
payload as they finish — see `ai/jobs.py` and `ai/form_jobs.py`.

⚠️ `kind = 'submission_summary'` IS EXCLUDED FROM THE PURGE, and that exclusion
is load-bearing rather than cautious. That job kind stores no image; it stores
its cache KEY in `payload_json` and matches on it later (`ai/router.py`). Purging
it would not save a byte and would silently disable the cache — every review
screen would recompute forever, and nothing would look broken.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8d3f1a72c94'
down_revision = 'a2c9f5e81b43'
branch_labels = None
depends_on = None

_IMAGE_KINDS = ("ocr_consumption", "ocr_delivery_note", "ocr_purchase_doc",
                "tool_identify", "ocr_consumption_form")


def upgrade() -> None:
    op.create_index("ix_ai_jobs_kind_status_created", "ai_jobs",
                    ["kind", "status", "created_at"], unique=False)
    op.create_index("ix_ai_jobs_status", "ai_jobs", ["status"], unique=False)


def data_upgrade(conn) -> None:
    """Release the images already held by finished jobs.

    Replayed by `run_data_migrations` on a cut-over database (suite BW asserts
    that mechanism), and idempotent — a second run matches nothing because the
    payloads are already NULL.
    """
    conn.execute(sa.text(
        "UPDATE ai_jobs SET payload_json = NULL "
        "WHERE status IN ('done', 'error') "
        "  AND payload_json IS NOT NULL "
        "  AND kind = ANY(:kinds)"), {"kinds": list(_IMAGE_KINDS)})


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_status", table_name="ai_jobs")
    op.drop_index("ix_ai_jobs_kind_status_created", table_name="ai_jobs")
