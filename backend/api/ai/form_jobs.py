"""
backend/api/ai/form_jobs.py — Phase 9d: the worker that reads a photographed form.

Same discipline as `ai/jobs.py`, and for the same reasons: the queued→running
transition is a single atomic UPDATE guarded on `status='queued'`, so two
workers racing one job produce exactly one winner; the worker owns its sessions
because the request that spawned it is long gone; and it NEVER raises — every
failure lands in `ai_jobs.error` where the poller can show it.

⚠️ THE WORKER CREATES THE ENTRY, and the poller returns its id. The obvious
alternative — return the extraction and let the browser POST it back — puts the
model's reading of a digit on a round trip through a client that could alter it
between reading and creation, and leaves the `Form_UUID` unconsumed in the gap
so a second upload of the same sheet could race the first. One transaction, one
sheet, one draft.

⚠️ FAILURE IS LOUD AND NAMED. A photo that cannot be read must not produce an
empty entry: a blank draft looks merely unfilled, and a blank draft submitted is
a consumption of zero silently recorded. Every refusal here carries the sentence
a supervisor standing in a plant can act on.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select, update

from ..db import SessionLocal
from ..services import form_intake
from ..services.ledger import _MD
from . import client as aic
from . import ocr_form as OF
from .jobs import _now

logger = logging.getLogger("gi.ai.form_jobs")

ai_jobs_t = _MD.tables["ai_jobs"]

_RUNNING: set[asyncio.Task] = set()


async def run_job(job_id: int) -> None:
    async with SessionLocal() as s:
        claimed = await s.execute(update(ai_jobs_t).where(
            ai_jobs_t.c["id"] == job_id,
            ai_jobs_t.c["status"] == "queued",
        ).values(status="running", started_at=_now()))
        if claimed.rowcount == 0:          # raced by another worker — theirs
            await s.rollback()
            return
        row = (await s.execute(
            select(ai_jobs_t.c["payload_json"], ai_jobs_t.c["actor"],
                   ai_jobs_t.c["Site_ID"])
            .where(ai_jobs_t.c["id"] == job_id))).first()
        await s.commit()

    try:
        payload = json.loads(row.payload_json or "{}")
        image = base64.b64decode(payload.get("image_b64", ""))
        if not image:
            raise HTTPException(422, "that upload contained no image data")

        # A PDF is rasterised to its first page before anything else touches
        # it: everything downstream — the QR detector, the rectifier, the
        # vision model — takes pixels.
        if payload.get("mime") == "application/pdf":
            image = _pdf_first_page(image)

        read = await OF.read_form(image)

        async with SessionLocal() as s:
            async with s.begin():
                result = await form_intake.build_entry(
                    s, read, site_id=row.Site_ID, username=row.actor,
                    role=payload.get("role") or "supervisor",
                    image_bytes=image, job_id=job_id)
            # The photograph is released here. It is NOT lost: `build_entry`
            # has just stored it on `sme_execution_entry.OCR_Image`, which is
            # where the row-crop viewer reads it from. Keeping the base64 copy
            # too meant every uploaded form was held twice, one copy in a text
            # column nothing would ever open again.
            await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                            .values(status="done", finished_at=_now(),
                                    payload_json=None,
                                    result_json=json.dumps(result,
                                                           ensure_ascii=False,
                                                           default=str)))
            await s.commit()
    except HTTPException as e:
        # ⚠️ THE USER-FACING SENTENCE SURVIVES. `form_intake` and `ocr_form`
        # raise HTTPException specifically so the reason ("print a fresh form",
        # "the whole page has to be in frame") reaches the poller intact. Losing
        # it to a generic "job failed" would strand somebody with a phone and no
        # idea what to do differently.
        await _fail(job_id, str(e.detail))
    except Exception as e:                          # noqa: BLE001 — see above
        logger.warning("form job %s failed: %s", job_id, e)
        await _fail(job_id, f"{type(e).__name__}: {e}")


async def _fail(job_id: int, message: str) -> None:
    async with SessionLocal() as s:
        await s.execute(update(ai_jobs_t).where(ai_jobs_t.c["id"] == job_id)
                        .values(status="error", finished_at=_now(),
                                payload_json=None,
                                error=str(message)[:900]))
        await s.commit()


def _pdf_first_page(data: bytes) -> bytes:
    """First page of a PDF as JPEG bytes.

    Scanned at 200 dpi: enough for the QR's module size and for a legible row
    crop, without turning an A4 page into a 30 MB array. Only the FIRST page —
    one form is one photo (ruling Q8), and a multi-page PDF is somebody
    scanning a stack, which needs one upload each.
    """
    try:
        import io

        import pypdfium2 as pdfium
    except ImportError as e:
        raise HTTPException(
            415, "this server cannot read PDFs (pypdfium2 is not installed). "
                 "Send a photograph instead, or ask your admin to install it."
        ) from e
    doc = pdfium.PdfDocument(io.BytesIO(data))
    if len(doc) == 0:
        raise HTTPException(422, "that PDF has no pages")
    buf = io.BytesIO()
    doc[0].render(scale=200 / 72).to_pil().convert("RGB").save(
        buf, format="JPEG", quality=88)
    return buf.getvalue()


def spawn(job_id: int) -> None:
    """Fire-and-forget worker task (kept referenced so GC cannot collect it)."""
    task = asyncio.create_task(run_job(job_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def preflight() -> str:
    """'' when a form can be read right now, else why not.

    Checked before the upload button is offered, because a supervisor who has
    already photographed a form and walked away deserves to have been told the
    model was down while they were still standing at the desk.
    """
    if aic.vision_provider() == "anthropic":
        return ""
    if not await aic.health():
        return ("Local AI is offline (Ollama is not reachable). You can still "
                "type the entry in by hand.")
    models = await aic.list_models()
    if models and not any(m.split(":")[0] == aic.MODEL_VISION.split(":")[0]
                          for m in models):
        return (f"The vision model {aic.MODEL_VISION} is not installed on this "
                f"server. Ask your admin to pull it, or type the entry in.")
    return ""
