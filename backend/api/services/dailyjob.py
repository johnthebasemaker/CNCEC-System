"""
backend/api/services/dailyjob.py — "exactly one worker runs this today".

⚠️ THE BUG THIS EXISTS TO CLOSE. `report_center.scheduler_loop` claims its work
with an atomic `last_run` UPDATE, and its docstring explains why. The three
DAILY loops did not:

    briefing_loop        07:00  morning health briefing
    digest_loop          16:00  evening notification digest
    weekly_report_loop   Fri 17:00  executive PDF

Each simply slept until its hour and dispatched. `deploy/Dockerfile.api` runs
`uvicorn --workers 4`, so all four woke at the same second and every one of
those messages reached every recipient FOUR TIMES.

It is invisible in development (one worker, one copy) and invisible in the test
suite (the loops are disabled by `GI_SCHEDULER=0`), which is how it survived
three phases of daily WhatsApp messages.

────────────────────────────────────────────────────────────────────────────
HOW THE CLAIM WORKS, and why it is one statement.

    INSERT INTO daily_job_runs (job_key, last_run, last_worker)
    VALUES (:k, :now, :w)
    ON CONFLICT (job_key) DO UPDATE SET last_run = :now, last_worker = :w
     WHERE daily_job_runs.last_run IS NULL
        OR daily_job_runs.last_run < :due
    RETURNING job_key

A row comes back only to the caller whose UPDATE actually moved `last_run` past
the due time. Three other workers running the identical statement in the same
millisecond match the `WHERE` against an already-advanced row and get nothing.

⚠️ IT MUST STAY ONE STATEMENT. The obvious alternative — SELECT the last run,
compare it in Python, then UPDATE — is a check-then-write with a window in it,
and four workers waking on the same clock tick is precisely the case that
window is open for. Postgres decides, not the application.

⚠️ AND `due` IS THE SCHEDULED TIME, NOT `now`. Comparing against `now` would
let a worker that started its tick a second later claim the same run again,
because `last_run` would be a second in the past. `due` is the boundary of the
period being claimed, so everything inside that period is one claim.

────────────────────────────────────────────────────────────────────────────
FAILURE DIRECTION. A storage error here means `claim()` returns False and the
job does not run. That is the OPPOSITE of the rate limiter's fail-open, and
deliberately so: a missed briefing is a message nobody got, while a
double-claimed one is four messages everybody got and learned to ignore. When
the choice is between silence and noise, silence is recoverable.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import socket

from sqlalchemy import text

logger = logging.getLogger("gi.dailyjob")

# Identifies the process that won a claim, for the "did it run, and where?"
# question that is unanswerable after the fact if nobody wrote it down.
WORKER_TAG = f"{socket.gethostname()}/{os.getpid()}"


async def claim(session, job_key: str, due: _dt.datetime,
                result: str | None = None) -> bool:
    """True when THIS process may run `job_key` for the period ending `due`.

    Exactly one caller gets True per (job_key, due), across every worker.
    """
    try:
        row = (await session.execute(text("""
            INSERT INTO daily_job_runs (job_key, last_run, last_worker, last_result)
            VALUES (:k, CURRENT_TIMESTAMP, :w, :r)
            ON CONFLICT (job_key) DO UPDATE SET
                last_run = CURRENT_TIMESTAMP,
                last_worker = :w,
                last_result = :r
             WHERE daily_job_runs.last_run IS NULL
                OR daily_job_runs.last_run < :due
            RETURNING job_key
        """), {"k": job_key, "w": WORKER_TAG, "r": result, "due": due})).first()
        await session.commit()
    except Exception as e:                                  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        # Fail CLOSED — see the module docstring. A missed run is recoverable
        # noise-free silence; a double run is four messages to everybody.
        logger.warning("daily-job claim failed for %r: %s", job_key, e)
        return False
    return row is not None


async def note_result(session, job_key: str, result: str) -> None:
    """Record what the run found, without re-claiming.

    Separate from `claim` because the claim has to happen BEFORE the work (that
    is what makes it a claim) and the result only exists afterwards.
    """
    try:
        await session.execute(text(
            "UPDATE daily_job_runs SET last_result = :r WHERE job_key = :k"),
            {"r": str(result)[:400], "k": job_key})
        await session.commit()
    except Exception:                                       # noqa: BLE001
        try:
            await session.rollback()
        except Exception:                                   # noqa: BLE001
            pass


async def last_run(session, job_key: str) -> dict | None:
    """The recorded run, for the admin console and for tests."""
    try:
        row = (await session.execute(text(
            "SELECT job_key, last_run, last_worker, last_result "
            "FROM daily_job_runs WHERE job_key = :k"), {"k": job_key})).first()
    except Exception:                                       # noqa: BLE001
        return None
    if row is None:
        return None
    return {"job_key": row[0], "last_run": row[1],
            "last_worker": row[2], "last_result": row[3]}
