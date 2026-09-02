"""
backend/api/ai/trace.py — spans for the AI request path (Phase 11, slice 11c).

⚠️ THE PROBLEM IS NOT THAT THE ASSISTANT IS UNOBSERVABLE. IT IS THAT THE ONE
NUMBER WORTH HAVING WAS BEING COMPUTED AND THROWN AWAY.

`manual_index.Index.score()` produces a BM25 score for every candidate chunk on
every question. `search()` sorts by it, keeps six, and discards all of them. So
a good answer and a bad answer leave the same evidence — none — and "did BM25
fetch the wrong page, or did the model ignore the right one?" has never been
answerable. The 800-character head-truncation that put §2's access matrix in no
non-admin prompt (and made the assistant tell HODs they could not open the
Manpower page) was a RETRIEVAL failure wearing a model failure's clothes, and it
survived a whole phase for exactly this reason.

────────────────────────────────────────────────────────────────────────────
THE SPAN VOCABULARY

    ai.request        one per call. lane, role, outcome, total ms.
    ai.guard.input    pattern hits, score, decision            (slice 11d)
    ai.retrieve       ⚠️ allowed chapters, candidate count, per-chunk
                      (chapter, heading, score, rank, chars), fallback used
    ai.cache          key hash, hit/miss, similarity, age      (slice 11e)
    ai.generate       provider, model, num_ctx/num_predict, queue-wait,
                      generate ms, retries, fallback
    ai.guard.output   redactions, canary hits, defusals        (slice 11d)

`ai.request` is always written; the rest appear when that stage runs. A request
refused by the input guard has two rows and a complete one has four or five —
which is itself the diagnostic.

────────────────────────────────────────────────────────────────────────────
⚠️ WHAT IS NEVER RECORDED

The ANSWER, and the retrieved chunk TEXT.

The question IS recorded (operator ruling Q11, 2026-09-02: the twenty questions
people actually ask are the most valuable eval cases in the system, and nothing
was retaining them). The answer is not, and neither are the passages — storing
those would copy the manual into a table with a laxer read path than the manual
itself has, which is how rule 9 gets undone by accident. Chapter numbers,
headings, scores and character counts answer every diagnostic question the text
would, without carrying it.

────────────────────────────────────────────────────────────────────────────
⚠️ AND IT MUST NEVER COST THE THING IT MEASURES

Two rules, both absolute:

  1. NOTHING HERE RAISES. Every public function swallows its own failures. An
     observability layer that can break a request is worse than no
     observability layer, because it converts a diagnostic into an outage.
  2. NOTHING HERE BLOCKS. Spans go onto a BOUNDED in-process queue and one
     drain task writes them. A synchronous INSERT would put the tracer on the
     critical path of the request it is describing — and under
     `uvicorn --workers 4` it would do so four times over.

When the queue is full spans are DROPPED and counted. A dropped span is a
missing diagnostic; a blocked request is a missing answer, and the second is
worse. `stats()` reports the drop count so a full queue is visible rather than
silent — the same reasoning that made a skipped check stop counting as a pass.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

from sqlalchemy import delete, insert

from ..db import SessionLocal
from ..services.ledger import _MD

logger = logging.getLogger("gi.ai.trace")

traces_t = _MD.tables["ai_traces"]

# The vocabulary, in the order a request walks through it. Kept as a tuple so a
# typo in a span name fails a test rather than creating a category nobody
# queries — a trace nobody can filter for is a row, not a diagnostic.
SPANS = (
    "ai.request",
    "ai.guard.input",
    "ai.retrieve",
    "ai.cache",
    "ai.generate",
    "ai.guard.output",
)

ENABLED = os.environ.get("GI_AI_TRACE", "1").strip().lower() not in ("0", "false", "no")

# ⚠️ BOUNDED, AND SMALL ENOUGH TO NOTICE. A queue big enough to never fill is a
# queue that hides a stalled drain task until the process runs out of memory.
# 2,000 spans is a few hundred requests of backlog — far more than a healthy
# system ever holds, and little enough that a broken drain shows up as drops in
# `stats()` within minutes.
QUEUE_MAX = int(os.environ.get("GI_AI_TRACE_QUEUE", "2000"))

# Rows older than this are swept. A handful of rows per question on a system
# with a few hundred questions a day; 30 days is months of trend at negligible
# size, and the sweep is a DELETE by age rather than a partition because the
# table never gets large enough to need one.
RETENTION_DAYS = int(os.environ.get("GI_AI_TRACE_RETENTION_DAYS", "30"))

# The question is TRUNCATED, not hashed. A hash cannot be read back as an eval
# case, which is the entire point of retaining it (Q11); a cap stops a pasted
# document becoming a database row.
MAX_QUESTION_CHARS = 500
MAX_ATTRS_CHARS = 8000

_queue: Optional[asyncio.Queue] = None
_drain: Optional[asyncio.Task] = None
_dropped = 0
_written = 0


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def _queue_get() -> Optional[asyncio.Queue]:
    """The queue, created lazily on the running loop.

    Built on first use rather than at import: `asyncio.Queue()` binds to
    whichever loop is current, and this module is imported at process start —
    long before uvicorn's loop exists. A queue bound to the wrong loop is the
    kind of failure that only appears under load.
    """
    global _queue
    if _queue is None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return None
        _queue = asyncio.Queue(maxsize=QUEUE_MAX)
    return _queue


def emit(span: str, *, trace_id: str, lane: str = "", role: str = "",
         username: str = "", site_id: str = "", started_at=None,
         duration_ms: Optional[int] = None, ok: bool = True,
         outcome: str = "", attrs: Optional[dict] = None) -> None:
    """Record one span. Fire-and-forget; never raises, never blocks.

    Safe to call from anywhere, including outside a running loop (tests, CLI
    tools) — in that case it is a no-op rather than an error, because a helper
    script must not need an event loop to import a module that traces.
    """
    global _dropped
    if not ENABLED:
        return
    try:
        q = _queue_get()
        if q is None:
            return
        row = {
            "trace_id": trace_id,
            "span": span,
            "lane": lane or None,
            "role": role or None,
            "username": username or None,
            "Site_ID": site_id or None,
            "started_at": started_at or _now(),
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "ok": 1 if ok else 0,
            "outcome": (outcome or "")[:120] or None,
            "attrs_json": _attrs_json(attrs),
            "created_at": _now(),
        }
        try:
            q.put_nowait(row)
        except asyncio.QueueFull:
            # ⚠️ DROP, DO NOT WAIT. A missing diagnostic is a smaller loss than
            # a request that stalls behind its own instrumentation.
            _dropped += 1
    except Exception as e:                              # noqa: BLE001 — see header
        logger.debug("trace emit failed (ignored): %s", e)


def _attrs_json(attrs: Optional[dict]) -> Optional[str]:
    if not attrs:
        return None
    try:
        s = json.dumps(attrs, ensure_ascii=False, default=str)
    except Exception:                                   # noqa: BLE001
        return None
    return s[:MAX_ATTRS_CHARS]


def clip_question(q: str) -> str:
    return (q or "").strip()[:MAX_QUESTION_CHARS]


class Span:
    """Time one stage and emit it, whatever happens inside.

        with trace.Span("ai.retrieve", trace_id=tid, role=role) as sp:
            hits, tele = index.search_scored(...)
            sp.attrs(allowed_chapters=sorted(allowed), hits=tele)

    ⚠️ AN EXCEPTION IS RECORDED AND RE-RAISED, NOT SWALLOWED. The tracer must
    never change what a request does — including whether it fails. It marks the
    span not-ok, notes the exception TYPE (never its message, which can carry a
    fragment of whatever the request was carrying), and gets out of the way.
    """

    def __init__(self, span: str, *, trace_id: str, lane: str = "",
                 role: str = "", username: str = "", site_id: str = "") -> None:
        self.span = span
        self.trace_id = trace_id
        self.lane, self.role = lane, role
        self.username, self.site_id = username, site_id
        self._attrs: dict[str, Any] = {}
        self._outcome = "ok"
        self._t0 = 0.0
        self._started = None

    def attrs(self, **kw) -> "Span":
        self._attrs.update(kw)
        return self

    def outcome(self, value: str) -> "Span":
        self._outcome = value
        return self

    def __enter__(self) -> "Span":
        self._t0 = time.perf_counter()
        self._started = _now()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = int((time.perf_counter() - self._t0) * 1000)
        if exc_type is not None:
            self._outcome = "error"
            self._attrs["error_type"] = exc_type.__name__
        emit(self.span, trace_id=self.trace_id, lane=self.lane, role=self.role,
             username=self.username, site_id=self.site_id,
             started_at=self._started, duration_ms=ms,
             ok=exc_type is None, outcome=self._outcome, attrs=self._attrs)
        return False        # never suppress


# ── the drain ───────────────────────────────────────────────────────────────

async def drain_loop() -> None:
    """Write queued spans in batches, forever. One task per worker.

    Batched because a chat answer produces four to six spans within a second or
    two, and six INSERTs where one would do is six round trips the drain then
    spends not draining.
    """
    q = _queue_get()
    if q is None:
        return
    global _written
    while True:
        try:
            first = await q.get()
            batch = [first]
            # Take whatever else is already waiting, up to a sane cap.
            while len(batch) < 100:
                try:
                    batch.append(q.get_nowait())
                except asyncio.QueueEmpty:
                    break
            async with SessionLocal() as s:
                await s.execute(insert(traces_t), batch)
                await s.commit()
            _written += len(batch)
        except asyncio.CancelledError:
            raise
        except Exception as e:                          # noqa: BLE001 — see header
            logger.debug("trace drain failed (batch dropped): %s", e)
            await asyncio.sleep(1.0)


def start() -> None:
    """Start the drain task for this worker. Idempotent, never raises."""
    global _drain
    if not ENABLED or (_drain is not None and not _drain.done()):
        return
    try:
        _drain = asyncio.create_task(drain_loop())
    except Exception as e:                              # noqa: BLE001
        logger.debug("trace drain not started: %s", e)


async def stop() -> None:
    """Cancel the drain and flush what is already queued (test teardown)."""
    global _drain
    if _drain is not None:
        _drain.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _drain
        _drain = None


async def flush() -> int:
    """Write everything queued right now, synchronously. Returns the count.

    For tests and for shutdown. NOT for the request path — see the header.
    """
    q = _queue_get()
    if q is None:
        return 0
    batch = []
    while True:
        try:
            batch.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    if not batch:
        return 0
    try:
        async with SessionLocal() as s:
            await s.execute(insert(traces_t), batch)
            await s.commit()
        global _written
        _written += len(batch)
        return len(batch)
    except Exception as e:                              # noqa: BLE001
        logger.debug("trace flush failed: %s", e)
        return 0


async def sweep_retention() -> int:
    """Delete spans older than RETENTION_DAYS. Returns the row count.

    ⚠️ THE CALLER TAKES THE `daily_job_runs` CLAIM. Four workers each running
    this would each issue the same DELETE — harmless but wasteful, and exactly
    the shape of the bug that had three daily loops sending four copies of
    every message.
    """
    cutoff = _now() - _dt.timedelta(days=RETENTION_DAYS)
    try:
        async with SessionLocal() as s:
            res = await s.execute(
                delete(traces_t).where(traces_t.c["created_at"] < cutoff))
            await s.commit()
            return res.rowcount or 0
    except Exception as e:                              # noqa: BLE001
        logger.debug("trace retention sweep failed: %s", e)
        return 0


def stats() -> dict:
    """Queue health. `dropped` is the number that matters — see the header."""
    q = _queue
    return {"enabled": ENABLED,
            "queued": q.qsize() if q is not None else 0,
            "capacity": QUEUE_MAX,
            "written": _written,
            "dropped": _dropped,
            "draining": bool(_drain is not None and not _drain.done())}
