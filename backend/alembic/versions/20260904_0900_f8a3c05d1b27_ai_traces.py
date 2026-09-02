"""ai_traces — the retrieval telemetry the assistant never had

Revision ID: f8a3c05d1b27
Revises: e7f2a4c916b8
Create Date: 2026-09-04 09:00:00

⚠️ THE BLIND SPOT THIS CLOSES IS SPECIFIC AND HAS COST A PHASE BEFORE.

`ai/manual_index.Index.score()` computes a BM25 score for every candidate chunk
on every question, `search()` sorts by it, and then every one of those numbers
is thrown away. So when the Hub Assistant answers badly there is no way to tell
whether BM25 retrieved the wrong passage or the model ignored the right one —
a good answer and a bad answer leave identical evidence, which is none.

That is not hypothetical. The 800-character head-truncation meant §2's "Page
access matrix" — which begins about 1,900 characters in — was in NO non-admin
prompt, so the assistant inferred that HODs could not open the Manpower portal.
It was a RETRIEVAL failure, it looked like a model failure, and it survived a
whole phase because nothing recorded what the model had actually been shown.

Since 2026-09-02 the retrieval span records the allowed chapter set, the number
of candidates the fence left, and the per-chunk score, rank and size. A
regression is then a number that moved rather than a story about a bad answer.

────────────────────────────────────────────────────────────────────────────
WHY POSTGRES AND NOT A HOSTED TRACER (ruling P11-1)

A hosted span carries the PROMPT. Ours contain manual chapters, the results of
the five live SQL probes behind `/ai/insights`, generated SQL from the NL lane,
and — on the OCR lanes — signed delivery notes with a driver's name and thirty
employees' names on them. Shipping that to a third party is a decision far
above an observability upgrade, and it contradicts every other choice in this
system (`gi_ai_ro`, `safety.FORBIDDEN_TABLES`, the PDF-only board brief).

Postgres is already deployed, already backed up, already in the runbook, and
this table sits beside `system_audit_log` and `ai_jobs` where an investigation
starts anyway. Same reasoning as ruling P10-1 chose it over Redis.

────────────────────────────────────────────────────────────────────────────
WHAT IS STORED, AND THE TWO THINGS THAT ARE NOT

STORED: the question (operator ruling Q11 — the twenty questions people
actually ask are the most valuable eval cases in the system and nothing was
retaining them), the role, the lane, per-stage timings, retrieval scores, guard
decisions, token counts, which provider answered.

NOT STORED: the ANSWER, and the retrieved chunk TEXT. Both are deliberate.
Keeping passages here would copy the manual into a table whose read path is
laxer than the manual's own, which is a way of undoing rule 9 by accident —
the whole security property is that a role's context cannot contain a chapter
it may not see, and a trace viewer that shows the context to an admin is fine
while one that shows it to everybody is not. Chapter numbers, headings, scores
and character counts answer every diagnostic question without carrying the text.

────────────────────────────────────────────────────────────────────────────
⚠️ FOUR WORKERS

`uvicorn --workers 4` has already produced three production bugs in this
codebase. Two consequences here, both in `ai/trace.py` rather than in the
schema: writes go through a BOUNDED per-worker queue drained by one task (a
synchronous INSERT in the request path would put the observability layer on the
critical path of the thing it observes), and the retention sweep takes the
`daily_job_runs` claim so four workers do not each delete the same rows.

Retention is a DELETE by age, not a partition: this table takes a handful of
rows per question on a system with a few hundred questions a day.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f8a3c05d1b27'
down_revision = 'e7f2a4c916b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_traces',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        # Shared by every span of one request. Text rather than a UUID type —
        # it is grouped and equality-matched, never generated in SQL.
        sa.Column('trace_id', sa.Text(), nullable=False),
        sa.Column('span', sa.Text(), nullable=False),
        sa.Column('lane', sa.Text()),
        sa.Column('role', sa.Text()),
        sa.Column('username', sa.Text()),
        sa.Column('Site_ID', sa.Text()),
        sa.Column('started_at', sa.DateTime()),
        sa.Column('duration_ms', sa.Integer()),
        sa.Column('ok', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('outcome', sa.Text()),
        # JSON object as TEXT, matching every other JSON column in this schema
        # (OCR_Raw_JSON, payload_json, result_json). One column with a
        # different storage type would be a trap for the next migration author
        # rather than a feature anybody asked for.
        sa.Column('attrs_json', sa.Text()),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    # ⚠️ DECLARED IN models.py TOO (rule 15's second half). `cutover_migrate.py`
    # builds production from `metadata.create_all`, so an index that lives only
    # in a revision is absent from every production box — that is exactly how
    # `ux_asset_transfer_open` nearly shipped without the guard that stops two
    # sites claiming one asset.
    op.create_index("ix_ai_traces_created", "ai_traces", ["created_at"])
    op.create_index("ix_ai_traces_lane_created", "ai_traces",
                    ["lane", "created_at"])
    op.create_index("ix_ai_traces_trace", "ai_traces", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_traces_trace", table_name="ai_traces")
    op.drop_index("ix_ai_traces_lane_created", table_name="ai_traces")
    op.drop_index("ix_ai_traces_created", table_name="ai_traces")
    op.drop_table('ai_traces')
