"""ai_answer_cache — a repeated question answered once

Revision ID: a1c9e64b3d70
Revises: f8a3c05d1b27
Create Date: 2026-09-05 09:00:00

⚠️ THE KEY IS THE WHOLE SECURITY OF THIS TABLE.

Rule 9's guarantee is that a role's CONTEXT differs — a Store Keeper's prompt
physically cannot contain the Admin chapter. Two people can therefore type a
byte-identical question and be entitled to different answers. A cache keyed on
the question alone would serve one of them the other's answer, undoing from the
side the boundary the retrieval fence enforces so carefully from the front. It
would also be invisible: the answer would look plausible, be well-formed, and
cite chapters the reader has never been shown.

So `key_hash` covers, and must always cover:

    normalised question · role · manual content hash · prompt template hash

⚠️ THE MANUAL HASH IS NOT OPTIONAL EITHER. This manual gains a chapter almost
every phase — §24 in slice 10b, §25 in 11c. An answer cached against the
previous edition is a confident description of a screen that has since changed,
and it is worse than no answer because it carries no sign of being stale.
Hashing the corpus means a manual edit retires every entry automatically, which
is the only invalidation rule nobody has to remember to apply.

⚠️ AND ONLY THE MANUAL ASSISTANT IS CACHED. `/ai/query`, `/ai/nl-search`,
`/ai/insights` and `/ai/eod-summary` answer from LIVE STOCK; a cached "you have
40 drums" is a wrong number with a timestamp on it. `lane` is stored so that
adding a second cached lane is a decision somebody writes down rather than a
default somebody inherits.

────────────────────────────────────────────────────────────────────────────
WHY EXACT-MATCH FIRST, AND NOT SEMANTIC

Rule 11 — indexes are benchmarked before they are added — applies to caches
too. `nomic-embed-text` is already pulled locally, so a semantic cache is
buildable without a new service; what is missing is evidence that it would earn
its correctness risk. An exact-match cache costs ~60 lines, no new dependency,
and makes the HIT RATE measurable, which is the number that decides whether
stage 2 is worth anything.

The correctness risk is real and specific: "what can a supervisor approve?" and
"what can a supervisor NOT approve?" are about 0.95 cosine apart and have
opposite answers. A semantic threshold is a correctness knob wearing a
performance knob's clothes, and it should not be turned until a curated set of
near-miss pairs proves where it can safely sit.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1c9e64b3d70'
down_revision = 'f8a3c05d1b27'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_answer_cache',
        # sha256 over (question_norm, role, manual_hash, prompt_hash). The
        # composite is hashed rather than stored as separate key columns so
        # that adding a factor is a change to one function instead of a
        # migration — and so that a lookup cannot accidentally omit one.
        sa.Column('key_hash', sa.Text(), primary_key=True),
        sa.Column('lane', sa.Text(), nullable=False,
                  server_default=sa.text("'assistant'")),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('question_norm', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('model', sa.Text()),
        sa.Column('manual_hash', sa.Text(), nullable=False),
        sa.Column('prompt_hash', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_hit_at', sa.DateTime()),
        sa.Column('hit_count', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
    )
    # ⚠️ DECLARED IN models.py TOO (rule 15's second half): cutover builds
    # production from `metadata.create_all`, so an index living only here is
    # absent from every production box.
    op.create_index("ix_ai_answer_cache_created", "ai_answer_cache",
                    ["created_at"])
    op.create_index("ix_ai_answer_cache_role", "ai_answer_cache",
                    ["role", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_answer_cache_role", table_name="ai_answer_cache")
    op.drop_index("ix_ai_answer_cache_created", table_name="ai_answer_cache")
    op.drop_table('ai_answer_cache')
