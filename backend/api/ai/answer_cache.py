"""
backend/api/ai/answer_cache.py — the same question, asked by twelve HODs.

⚠️ READ THE KEY BEFORE ANYTHING ELSE. A CACHE KEYED ON THE QUESTION ALONE IS A
FENCE BYPASS.

Rule 9's guarantee is that a role's CONTEXT differs: a Store Keeper's prompt
physically cannot contain the Admin chapter, so two people typing a
byte-identical question are entitled to DIFFERENT answers. Serve one of them the
other's cached reply and the boundary the retrieval fence enforces from the
front has been walked around from the side — invisibly, because the answer will
be fluent, plausible, and cite chapters that reader has never been shown.

So the key is, and must remain:

    sha256( normalised question · role · manual content hash · prompt hash )

⚠️ THE MANUAL HASH IS NOT OPTIONAL. This manual gains a chapter almost every
phase (§24 in 10b, §25 in 11c). An answer cached against the previous edition
describes a screen that has changed, confidently and with no sign of being
stale. Hashing the corpus retires every entry on any manual edit, which is the
only invalidation rule nobody has to remember to apply. The PROMPT hash does the
same job for `_SYSTEM_PROMPT_TMPL`: change the instructions and the old answers
stop matching rather than lingering under new rules.

⚠️ ONLY THE MANUAL ASSISTANT IS CACHED. `/ai/query`, `/ai/nl-search`,
`/ai/insights` and `/ai/eod-summary` answer from LIVE STOCK — a cached "you have
40 drums" is a wrong number wearing a timestamp, and it is worse than a slow
right one. `route.LanePolicy.cacheable` says which lanes may use this, and only
`assistant` has it.

────────────────────────────────────────────────────────────────────────────
EXACT MATCH, NOT SEMANTIC — AND WHY THAT IS THE ORDER

Rule 11 (benchmark before you add) applies to caches as much as to indexes.
`nomic-embed-text` is already pulled, so a semantic cache needs no new service;
what is missing is evidence it would earn its correctness risk. This costs ~60
lines and makes the HIT RATE measurable, which is the number that decides
whether stage 2 is worth building at all.

And the risk it would take on is specific: "what can a supervisor approve?" and
"what can a supervisor NOT approve?" sit about 0.95 apart in embedding space and
have opposite answers. A similarity threshold is a correctness knob dressed as a
performance knob. It should not be turned until a curated set of near-miss pairs
has proved where it can safely sit — which is what `tests/ai_eval`'s near-miss
cases (slice 11f) exist to establish.

⚠️ AND NOTHING HERE RAISES. A cache that can fail a request has made the system
less reliable in exchange for making it faster, which is the wrong trade in both
directions. Every function swallows its own errors and behaves as a miss.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import re
from typing import Optional

from sqlalchemy import insert, select, update

from ..db import SessionLocal
from ..services.ledger import _MD

logger = logging.getLogger("gi.ai.cache")

cache_t = _MD.tables["ai_answer_cache"]

# Answers older than this are swept even if the manual has not changed. The
# hashes already retire stale content; this is only about a table that would
# otherwise grow forever on a system where somebody asks something once.
TTL_DAYS = 30

_WS = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s?!.]+$")


def normalise(question: str) -> str:
    """Lowercase, collapse whitespace, drop terminal punctuation.

    Deliberately conservative. Stemming or stop-word removal here would make
    "can a supervisor approve this" and "can a supervisor not approve this"
    collide, which is the exact failure a semantic cache has to be careful
    about and which an exact-match cache has no business inheriting.
    """
    return _TRAILING.sub("", _WS.sub(" ", (question or "").strip().lower()))


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(b"\x00")
        h.update((p or "").encode("utf-8", "replace"))
    return h.hexdigest()


def manual_hash() -> str:
    """A hash of the corpus the answers were produced from."""
    try:
        from . import manual_qa as mq
        return hashlib.sha256(mq._manual_text().encode("utf-8")).hexdigest()[:32]
    except Exception:                                   # noqa: BLE001
        return "unknown"


def prompt_hash() -> str:
    """A hash of the system-prompt TEMPLATE (not of one filled prompt).

    The filled prompt contains the retrieved context, which varies per question
    and is already covered by the question and role. What matters here is the
    INSTRUCTIONS: change them and every cached answer was produced under rules
    that no longer apply.
    """
    try:
        from . import manual_qa as mq
        return hashlib.sha256(
            mq._SYSTEM_PROMPT_TMPL.encode("utf-8")).hexdigest()[:32]
    except Exception:                                   # noqa: BLE001
        return "unknown"


def key_for(question: str, role: str) -> str:
    """⚠️ THE ONLY PLACE THE KEY IS CONSTRUCTED. Both the lookup and the store
    call this, so a lookup cannot accidentally omit a factor the store
    included — which would produce a permanent miss, or worse, a hit on a key
    that means something different."""
    return _sha(normalise(question), role or "", manual_hash(), prompt_hash())


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


async def lookup(question: str, role: str) -> Optional[dict]:
    """A previously-given answer for THIS role, or None. Never raises."""
    try:
        k = key_for(question, role)
        async with SessionLocal() as s:
            row = (await s.execute(select(cache_t).where(
                cache_t.c["key_hash"] == k))).mappings().first()
            if row is None:
                return None
            if row["created_at"] and (
                    _now() - row["created_at"]).days > TTL_DAYS:
                return None
            # ⚠️ BELT AND BRACES ON THE ROLE. The key already covers it; this
            # compares the stored value too. A hash collision is not the worry
            # — a future edit to `key_for` that drops a factor is, and this
            # turns that class of mistake into a miss instead of a leak.
            if row["role"] != role:
                logger.warning("cache key/role mismatch — treating as a miss")
                return None
            await s.execute(update(cache_t).where(cache_t.c["key_hash"] == k)
                            .values(hit_count=cache_t.c["hit_count"] + 1,
                                    last_hit_at=_now()))
            await s.commit()
            return {"answer": row["answer"], "model": row["model"],
                    "age_s": int((_now() - row["created_at"]).total_seconds())
                    if row["created_at"] else 0,
                    "hit_count": (row["hit_count"] or 0) + 1}
    except Exception as e:                              # noqa: BLE001 — see header
        logger.debug("cache lookup failed (treated as a miss): %s", e)
        return None


async def store(question: str, role: str, answer: str, *,
                model: str = "", lane: str = "assistant") -> bool:
    """Remember an answer. Never raises; returns whether it was written.

    ⚠️ REFUSALS AND ERRORS ARE NOT CACHED, and the caller enforces that by only
    calling this on a complete successful generation. Caching a refusal would
    make a transient guard decision permanent for that role, and caching an
    error sentence would serve it to everybody who asks again.
    """
    text = (answer or "").strip()
    if not text or not (question or "").strip():
        return False
    try:
        k = key_for(question, role)
        async with SessionLocal() as s:
            exists = (await s.execute(select(cache_t.c["key_hash"]).where(
                cache_t.c["key_hash"] == k))).first()
            if exists:
                return False
            await s.execute(insert(cache_t).values(
                key_hash=k, lane=lane, role=role,
                question_norm=normalise(question), answer=text,
                model=model or None, manual_hash=manual_hash(),
                prompt_hash=prompt_hash(), created_at=_now(), hit_count=0))
            await s.commit()
            return True
    except Exception as e:                              # noqa: BLE001
        logger.debug("cache store failed (ignored): %s", e)
        return False


async def stats() -> dict:
    """Entries, hits and the top repeated questions.

    ⚠️ THE HIT RATE IS THE POINT OF SHIPPING STAGE 1 AT ALL. It is the evidence
    that decides whether a semantic cache is worth its correctness risk, and
    without it that decision would be a preference.
    """
    try:
        from sqlalchemy import func as _f
        async with SessionLocal() as s:
            row = (await s.execute(select(
                _f.count().label("entries"),
                _f.coalesce(_f.sum(cache_t.c["hit_count"]), 0).label("hits"),
            ).select_from(cache_t))).mappings().first()
            top = [dict(m) for m in (await s.execute(
                select(cache_t.c["role"], cache_t.c["question_norm"],
                       cache_t.c["hit_count"])
                .order_by(cache_t.c["hit_count"].desc()).limit(10)
            )).mappings().all()]
        entries = int(row["entries"] or 0)
        hits = int(row["hits"] or 0)
        return {"entries": entries, "hits": hits,
                # Served / asked. An entry is written on a miss, so
                # (hits) / (hits + entries) is the share of questions answered
                # without a generation.
                "hit_rate": round(hits / (hits + entries), 3) if entries else 0.0,
                "top": top}
    except Exception as e:                              # noqa: BLE001
        logger.debug("cache stats failed: %s", e)
        return {"entries": 0, "hits": 0, "hit_rate": 0.0, "top": []}


async def sweep() -> int:
    """Drop entries the hashes no longer match, and anything past the TTL.

    The hashes already make a stale entry unreachable; this reclaims the space.
    Carried by the orphan-sweep loop under the `daily_job_runs` claim, like the
    trace retention — four workers each running it would each issue the same
    DELETE.
    """
    try:
        from sqlalchemy import delete, or_
        cutoff = _now() - _dt.timedelta(days=TTL_DAYS)
        async with SessionLocal() as s:
            res = await s.execute(delete(cache_t).where(or_(
                cache_t.c["created_at"] < cutoff,
                cache_t.c["manual_hash"] != manual_hash(),
                cache_t.c["prompt_hash"] != prompt_hash())))
            await s.commit()
            return res.rowcount or 0
    except Exception as e:                              # noqa: BLE001
        logger.debug("cache sweep failed: %s", e)
        return 0
