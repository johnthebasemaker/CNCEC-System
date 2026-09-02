"""
backend/api/ai/jobs.py — async AI job queue over the ai_jobs table (Phase AI-3).

Why a job table instead of awaiting inline: vision OCR takes 5–120 s
(qwen2.5vl cold start ~30–90 s) — longer than proxy timeouts and mobile
patience. POST /ai/jobs inserts a row and spawns an in-process
asyncio.create_task; React polls GET /ai/jobs/{id} every ~2 s. Jobs survive
page reloads and locked phones because state lives in Postgres, not the
connection.

Concurrency discipline (same as the report scheduler's last_run claim):
the queued→running transition is a single atomic UPDATE guarded on
status='queued' — if two workers ever race one job, exactly one wins.
Orphan sweep: on startup, rows still 'queued'/'running' from a previous
process are failed with a clear message (their in-process task died with
the old server; the user just resubmits the photo).

The worker calls the Ollama client through the module object (`aic.…`) —
the same monkeypatch seam the assistant tests use, so the suite runs the
FULL job lifecycle with a fake model and no Ollama.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as _dt
import json
import logging
import os
import uuid

from sqlalchemy import select, update

from ..db import SessionLocal
from ..services.ledger import _MD
from . import client as aic
from . import fuzzy, ocr

logger = logging.getLogger("gi.ai.jobs")

ai_jobs_t = _MD.tables["ai_jobs"]
inventory_t = _MD.tables["inventory"]

# ── ⚠️ WHO OWNS A JOB, AND HOW A SWEEP KNOWS (2026-09-02) ────────────────────
#
# THE BUG. `fail_orphans` used to fail EVERY queued/running row at startup, on
# the reasoning that a job in flight died with the process that was running it.
# That holds for one process and breaks for `uvicorn --workers 4`, which is what
# production runs: when a single worker crashed and uvicorn respawned it, the
# new process's lifespan swept away the in-flight jobs of the three workers that
# were still running them. A supervisor five minutes into a six-minute form read
# was told "server restarted while this job was in flight" by a server that had
# not restarted. Invisible at deploy (all four workers boot before any job
# exists); it only bites on a respawn.
#
# WHY OWNERSHIP ALONE DOES NOT FIX IT. `WORKER_ID` says who claimed a row. It
# cannot say whether that process is still alive — the other workers are
# separate OS processes with no shared memory and nothing to ask. Liveness has
# to be written somewhere every worker can read, and the row is that place.
#
# So the owner beats every HEARTBEAT_SECONDS while it works, and the sweep
# reaps only rows that have not been touched for ORPHAN_STALE_SECONDS. A slow
# job keeps beating and is left alone; a dead owner's job stops and is reaped.
#
# ⚠️ THE STALE WINDOW IS NOT THE JOB TIMEOUT, and must not be "fixed" to match
# it. A vision read can legitimately run 900 s (client.VISION_TIMEOUT_S) and
# spend much of that queued on the 2-permit generation semaphore. Sizing the
# sweep off the job's DURATION would mean waiting 15 minutes to reap a corpse;
# sizing it off the BEAT means five missed beats, whatever the job's length.
WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
HEARTBEAT_SECONDS = float(os.environ.get("GI_AI_HEARTBEAT_S", "30"))
ORPHAN_STALE_SECONDS = float(os.environ.get("GI_AI_ORPHAN_STALE_S", "180"))
ORPHAN_SWEEP_SECONDS = float(os.environ.get("GI_AI_ORPHAN_SWEEP_S", "300"))

JOB_KINDS = ("ocr_consumption", "ocr_delivery_note", "tool_identify",
             # QSEP slice 6 — a SCANNED PR/PO. Needed because a real
             # purchase order in this project (PO#4710003121) is a scan with
             # zero extractable text, which pdfplumber can never read.
             "ocr_purchase_doc")

# ⚠️ THE OUTPUT BUDGET IS PER LANE, AND ONE NUMBER FOR ALL OF THEM WAS A BUG.
#
# Every image lane used to share `num_predict=1024`, which is a property of the
# ANSWER, not of the model — and the four lanes have answers of wildly different
# size. Measured 2026-09-01 on the operator's own files:
#
#   a delivery note ....... 4 items x 3 fields ... ~350 tokens ... finished
#   a consumption log .... 30 rows x 9 fields ... ~2,400 tokens ... CUT at 13
#
# So the DN lane always fitted and the consumption lane never could, which is
# precisely why the failure presented as "the model cannot read free-form
# tables" and survived a whole phase. It read them fine; it was interrupted.
#
# `ocr.salvage_truncated_json` now rescues a clipped reply rather than throwing
# the whole page away, and these budgets are what stop it being needed. Both
# halves matter: a budget alone still loses the tail of an unusually long sheet,
# and salvage alone silently drops rows nobody knows are missing.
NUM_PREDICT = {
    "ocr_consumption": 3072,    # the 30-row daily log, with headroom
    "ocr_delivery_note": 1536,
    "ocr_purchase_doc": 2560,   # a PO line table plus its header block
    "tool_identify": 384,       # a name and two alternatives
}
DEFAULT_NUM_PREDICT = 1024


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


async def create_job(session, *, kind: str, actor: str, site_id: str | None,
                     image_b64: str) -> int:
    """Insert a queued job row. The caller commits and then spawns run_job.

    ⚠️ THE IMAGE LIVES ONLY UNTIL THE JOB FINISHES. `run_job` sets
    `payload_json = NULL` on both terminal transitions, because this column
    holds a base64 photograph and nothing reads it after the worker has. Kept,
    it made `ai_jobs` grow by the size of every photo ever OCR'd — and it made
    every sequential scan of the table (the orphan sweep, the summary cache)
    drag those photographs through shared buffers to read a status column.
    Alembic b8d3f1a72c94 indexes those scans and reclaims the existing rows.
    """
    from sqlalchemy import insert
    payload = json.dumps({"image_b64": image_b64})
    return (await session.execute(insert(ai_jobs_t).values(
        kind=kind, status="queued", actor=actor, Site_ID=site_id,
        payload_json=payload).returning(ai_jobs_t.c["id"]))).scalar_one()


async def _vision_preflight() -> str | None:
    """Friendly error string when image OCR can't run, else None — port of the
    legacy two-check preflight (server reachable, vision model pulled)."""
    if not await aic.health():
        return (f"Local AI is offline (Ollama not reachable at {aic.OLLAMA_HOST}). "
                "Ask your admin to start the Ollama service, or use the Paste tab.")
    installed = await aic.list_models()
    if installed and aic.MODEL_VISION not in installed:
        return (f"Vision model {aic.MODEL_VISION} is not installed on the AI "
                f"server — ask your admin to run `ollama pull {aic.MODEL_VISION}`. "
                "The Paste tab works meanwhile.")
    return None


async def _resolve(kind: str, parsed: dict, session) -> dict:
    """Fuzzy-match every material_text against the inventory master so the
    review grid opens with auto/pick/unknown states already computed."""
    inv_rows = (await session.execute(select(
        inventory_t.c["SAP_Code"], inventory_t.c["Equipment_Description"],
        inventory_t.c["Material_Code"], inventory_t.c["UOM"]))).mappings().all()
    inventory = [dict(r) for r in inv_rows]
    if kind == "ocr_consumption":
        return {"rows": fuzzy.resolve_rows(parsed["rows"], inventory)}
    if kind == "ocr_purchase_doc":
        # A scanned purchase document carries the EXACT material code, so it
        # is matched on the code first and only falls back to fuzzy text.
        # That ordering matters: fuzzy-matching a description when a code was
        # printed on the page throws away the one unambiguous key the
        # document had, and "ELECTRIC INSULATION" resembles several rows in
        # the master while GI-7000009 resembles exactly one.
        by_code = {str(r["Material_Code"] or "").strip().upper(): r
                   for r in inventory if r["Material_Code"]}
        exact, needs_fuzzy = [], []
        for it in parsed["items"]:
            hit = by_code.get(it.get("material_code", ""))
            if hit:
                exact.append({**it, "SAP_Code": hit["SAP_Code"],
                              "Equipment_Description": hit["Equipment_Description"],
                              "uom": it.get("uom") or hit["UOM"] or "",
                              "match_state": "auto", "match_source": "code"})
            else:
                needs_fuzzy.append(it)
        resolved = fuzzy.resolve_rows(needs_fuzzy, inventory) if needs_fuzzy else []
        for r in resolved:
            r.setdefault("match_source", "text")
        return {"doc_type": parsed.get("doc_type", ""),
                "header": parsed["header"], "items": exact + resolved}
    return {"header": parsed["header"],
            "items": fuzzy.resolve_rows(parsed["items"], inventory)}


async def run_job(job_id: int) -> None:
    """The worker. Own sessions (the request that spawned us is long gone);
    never raises — every failure lands in ai_jobs.error for the poller."""
    async with SessionLocal() as s:
        claimed = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["id"] == job_id,
            ai_jobs_t.c["status"] == "queued",
        ).values(**await claim_values()))
        if claimed.rowcount == 0:  # raced by another worker — theirs now
            await s.rollback()
            return
        row = (await s.execute(select(ai_jobs_t.c["kind"], ai_jobs_t.c["payload_json"])
                               .where(ai_jobs_t.c["id"] == job_id))).first()
        await s.commit()

    kind = row.kind
    # ⚠️ THE BEAT STARTS BEFORE THE SEMAPHORE, not after it. A third concurrent
    # job waits on `GEN_SEMAPHORE` (2 permits) for as long as the two ahead of
    # it take — minutes — with status already 'running'. Starting the heartbeat
    # after the wait would let the sweep reap a job that is patiently queued,
    # which is the same class of mistake as the bug this replaced.
    beat = asyncio.create_task(_beat(job_id))
    try:
        err = await _vision_preflight()
        if err:
            raise RuntimeError(err)
        image_b64 = json.loads(row.payload_json or "{}").get("image_b64", "")
        # Measure the image ONCE and size the context window from it. Decoding
        # a <=1.4 MB base64 payload costs about a millisecond; getting num_ctx
        # wrong costs the Ollama runner (see client.vision_num_ctx).
        img_tokens = ocr.estimate_image_tokens(
            base64.b64decode(image_b64)) if image_b64 else None

        if kind == "tool_identify":
            # Smart Scan tier-2 (AI-4): catalogue-constrained when the
            # tool_catalogue has rows, freeform naming when it's empty.
            async with SessionLocal() as s:
                cat_t = _MD.tables["tool_catalogue"]
                catalogue = [dict(m) for m in (await s.execute(select(
                    cat_t.c["class_name"], cat_t.c["display_name"]))).mappings()]
            async with aic.GEN_SEMAPHORE:
                raw = await aic.generate(
                    aic.MODEL_VISION, "Identify the tool.",
                    system=ocr.tool_prompt(catalogue), images=[image_b64],
                    temperature=0.1,
                    num_predict=NUM_PREDICT["tool_identify"],
                    timeout_s=aic.VISION_TIMEOUT_S,
                    num_ctx=aic.vision_num_ctx(
                        NUM_PREDICT["tool_identify"], img_tokens))
            result = ocr.parse_tool_reply(raw, catalogue)
        else:
            async with aic.GEN_SEMAPHORE:
                # ⚠️ THE VISION BUDGET, NOT THE CHAT ONE. A full page of tabular
                # handwriting runs for minutes; `GEN_TIMEOUT_S` is sized for a
                # chat reply and killed every long read at 240 s mid-generation.
                # Nothing waits on this call — the browser is polling ai_jobs —
                # so the only cost of the longer ceiling is a row sitting at
                # status='running'.
                raw = await aic.generate(
                    aic.MODEL_VISION, ocr.USER_PROMPTS[kind],
                    system=ocr.SYSTEM_PROMPTS[kind], images=[image_b64],
                    temperature=0.1,
                    num_predict=NUM_PREDICT.get(kind, DEFAULT_NUM_PREDICT),
                    timeout_s=aic.VISION_TIMEOUT_S,
                    num_ctx=aic.vision_num_ctx(
                        NUM_PREDICT.get(kind, DEFAULT_NUM_PREDICT), img_tokens))
            parsed = ocr.parse_vision_reply(kind, raw)

        async with SessionLocal() as s:
            if kind != "tool_identify":
                result = await _resolve(kind, parsed, s)
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="done", finished_at=_now(),
                                    payload_json=None,
                                    result_json=json.dumps(result, ensure_ascii=False)))
            await s.commit()
    except Exception as e:
        logger.warning("ai job %s failed: %s", job_id, e)
        async with SessionLocal() as s:
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="error", finished_at=_now(),
                                    payload_json=None,
                                    error=str(e)[:500]))
            await s.commit()
    finally:
        await stop_beat(beat)


def spawn(job_id: int) -> None:
    """Fire-and-forget worker task (kept referenced so GC can't collect it)."""
    task = asyncio.create_task(run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


_RUNNING: set[asyncio.Task] = set()


async def claim_values() -> dict:
    """The columns every worker stamps when it wins the queued→running race.

    Shared by `jobs.run_job` and `form_jobs.run_job` so the two cannot drift:
    a lane that claimed a row without stamping a heartbeat would be reaped by
    the sweep 180 s later, mid-read, which is the original bug wearing the
    fix's clothes.
    """
    now = _now()
    return {"status": "running", "started_at": now,
            "worker_id": WORKER_ID, "heartbeat_at": now}


async def _beat(job_id: int) -> None:
    """Touch `heartbeat_at` while this process is still working on `job_id`.

    ⚠️ NEVER RAISES, and never lets a database hiccup end the job it is
    describing. A missed beat costs nothing until five of them accumulate; a
    heartbeat task that propagated an exception would cancel the read it exists
    to protect, which is strictly worse than the condition it reports.

    The `status = 'running'` guard means a beat that lands after the job has
    finished updates nothing, so a late beat cannot resurrect a terminal row.
    """
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with SessionLocal() as s:
                await s.execute(update(ai_jobs_t).where(
                    ai_jobs_t.c["id"] == job_id,
                    ai_jobs_t.c["status"] == "running",
                ).values(heartbeat_at=_now()))
                await s.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.debug("heartbeat for job %s missed: %s", job_id, e)


async def stop_beat(task: asyncio.Task | None) -> None:
    """Cancel a heartbeat and wait for it to actually stop.

    Awaited in a `finally`, so it runs on every exit path including the ones
    that raise. A beat left running would keep a finished job looking alive to
    the sweep — harmless, since the beat's own `status = 'running'` guard makes
    it a no-op on a terminal row, but it would also leak a task per job for the
    life of the process.
    """
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def sweep_orphans() -> int:
    """Fail jobs whose owner is gone. NOT jobs that are merely slow.

    ⚠️ THIS REPLACED A SWEEP THAT FAILED EVERY UNFINISHED ROW. See the module
    header for why that was wrong under `--workers 4`. The predicate is now
    "nobody has touched this for ORPHAN_STALE_SECONDS", which is true of a
    dead owner and false of a live one, however long its job runs.

    `COALESCE(heartbeat_at, started_at, created_at)` covers all three ages a
    row can have: beating (running normally), claimed but not yet beaten (the
    first 30 s), and never claimed at all (a `queued` row whose process died
    between the commit and `spawn`, which nothing else would ever reap).

    Runs at startup AND on a timer, because a worker that dies while its
    siblings stay up leaves an orphan no startup ever sees — uvicorn respawns
    the dead worker, but the respawn happens in seconds, long before the row
    goes stale. Without the timer the fix would trade one silent failure for
    another.
    """
    from sqlalchemy import func as _func
    cutoff = _now() - _dt.timedelta(seconds=ORPHAN_STALE_SECONDS)
    async with SessionLocal() as s:
        res = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["status"].in_(["queued", "running"]),
            _func.coalesce(ai_jobs_t.c["heartbeat_at"],
                           ai_jobs_t.c["started_at"],
                           ai_jobs_t.c["created_at"]) < cutoff,
        ).values(status="error", finished_at=_now(), payload_json=None,
                 error="this job stopped responding (the process running it "
                       "went away) — please resubmit the photo"))
        await s.commit()
        return res.rowcount


# Back-compat alias. `fail_orphans` was the startup-sweep name for two phases
# and reads as "fail them all", which is precisely the behaviour that was
# wrong; the new name says what the function now decides.
fail_orphans = sweep_orphans


async def orphan_sweep_loop() -> None:
    """Periodic sweep. One per worker; the UPDATE is idempotent and guarded,
    so four workers racing it is harmless — whoever runs first reaps, the
    others match nothing."""
    while True:
        try:
            await asyncio.sleep(ORPHAN_SWEEP_SECONDS)
            n = await sweep_orphans()
            if n:
                logger.info("orphan sweep failed %s stranded job(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.warning("orphan sweep loop: %s", e)


def to_b64(prepped_jpeg: bytes) -> str:
    return base64.b64encode(prepped_jpeg).decode("ascii")
