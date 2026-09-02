"""ai_jobs: worker ownership + heartbeat, so the orphan sweep stops killing live jobs

Revision ID: c4a7e2b81f36
Revises: b8d3f1a72c94
Create Date: 2026-09-02 09:00:00

THE BUG THIS CLOSES.

`ai/jobs.py:fail_orphans()` ran in the FastAPI lifespan and executed, with no
filter for who owned the row:

    UPDATE ai_jobs SET status='error' WHERE status IN ('queued','running')

That is correct reasoning for a single-process server — if the process died,
its in-flight jobs died with it, because the worker is an in-process
`asyncio.create_task`. It is wrong for `uvicorn --workers 4`, which is what
`deploy/Dockerfile.api` runs. When one worker crashed and uvicorn respawned it,
the new process's startup sweep failed the in-flight OCR jobs belonging to the
three workers that were still happily running them.

The symptom was a supervisor five minutes into a six-minute form read being
told "server restarted while this job was in flight — please resubmit the
photo" by a server that had not restarted. It is invisible on a normal deploy,
because at boot all four workers start before any job exists; it only bites on
a respawn, which is exactly when nobody is watching.

WHY OWNERSHIP ALONE IS NOT ENOUGH, and why there is a heartbeat.

`worker_id` says who claimed the row. It does not say whether that process is
still alive — and a sweeping process cannot ask, because the other workers are
separate OS processes with no shared memory and no registry. A liveness signal
has to be written down where every worker can read it, which is this table.

So the owner touches `heartbeat_at` every 30 s for as long as it is working,
and the sweep fails only rows that have not been touched for minutes. A slow
job (a 900 s vision read, or one waiting on the 2-permit generation semaphore)
keeps beating and is left alone; a job whose owner is gone stops beating and is
reaped. `worker_id` is kept because "which process was this on" is the first
question asked when one worker misbehaves, and it is unanswerable afterwards if
nobody recorded it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4a7e2b81f36'
down_revision = 'b8d3f1a72c94'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ai_jobs', sa.Column('worker_id', sa.Text(), nullable=True))
    op.add_column('ai_jobs', sa.Column('heartbeat_at', sa.DateTime(), nullable=True))
    op.create_index("ix_ai_jobs_heartbeat", "ai_jobs",
                    ["status", "heartbeat_at"], unique=False)


def data_upgrade(conn) -> None:
    """Reap the rows that were already stranded when this shipped.

    Anything still `queued`/`running` at migration time cannot be alive: the
    process holding it is, by definition, not the one running this migration,
    and no pre-upgrade row has ever had a heartbeat. Seeding `heartbeat_at`
    instead would make them look freshly alive to the new sweep and they would
    sit unfinished forever.

    Idempotent — a second run matches nothing, because the first moved every
    row out of the two unfinished states.
    """
    conn.execute(sa.text(
        "UPDATE ai_jobs "
        "   SET status = 'error', "
        "       finished_at = CURRENT_TIMESTAMP, "
        "       payload_json = NULL, "
        "       error = 'this job was stranded by an older server version — "
        "please resubmit the photo' "
        " WHERE status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index("ix_ai_jobs_heartbeat", table_name="ai_jobs")
    op.drop_column('ai_jobs', 'heartbeat_at')
    op.drop_column('ai_jobs', 'worker_id')
