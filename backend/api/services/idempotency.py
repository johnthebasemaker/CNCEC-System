"""
backend/api/services/idempotency.py — a retry is not a second order.

Four actions in the procurement chain are dangerous to repeat: creating a PR,
submitting it to Logistics, raising a PO and assigning one to a warehouse. A
double-clicked button, a retry after a flaky network, a stale tab someone comes
back to — all send the same request twice, and the second one is a second
purchase request, a second notification, or a second warehouse told to expect
the same goods.

THE PROTOCOL, and why it is claim-then-fill rather than check-then-write:

    1. CLAIM   INSERT (key, action, hash, result='') ON CONFLICT DO NOTHING
    2. work    the caller does the real thing
    3. FILL    UPDATE ... SET result = <the answer>

Checking first and writing after would leave the whole window between them open
to the very double-click this exists to stop. The claim is a row insert against
a primary key, so two concurrent requests with one key SERIALISE: the second
blocks on the uncommitted first, then finds the row already there.

Three outcomes for a repeat, and they are deliberately different:

  · same key, same body, work finished   → REPLAY the stored answer, no side
                                            effects, `"replayed": true`
  · same key, same body, still in flight → 409. Handing back an answer that
                                            does not exist yet would be a lie,
                                            and waiting would hold a connection
  · same key, DIFFERENT body             → 409. That is a client bug, not a
                                            retry, and replaying the first
                                            answer would hide it behind a
                                            success

⚠️ THE KEY IS SCOPED BY ACTION AND USER. Two different endpoints, or two
different people, cannot collide on a key one of them chose — the client
generates these, and a UUID from one browser must not be able to replay another
account's PO.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .ledger import _MD

idem_t = _MD.tables["procurement_idempotency"]

# Long enough to be a UUID, short enough that nobody can use this table as a
# blob store. Rejected rather than truncated: a silently shortened key could
# collide with another.
MAX_KEY_LEN = 200


def body_hash(body) -> str:
    """A stable fingerprint of the request.

    `sort_keys` so a client that reorders its JSON is still the same request,
    and `default=str` so a date or a Decimal hashes rather than raising — this
    runs before the work, and a hashing failure must not be how an order fails.
    """
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _scoped(key: str, action: str, username: str) -> str:
    return f"{username}::{action}::{key}"


async def claim(session: AsyncSession, *, key: str | None, action: str,
                body, username: str) -> dict | None:
    """Claim `key` for this action, or return the answer a previous call gave.

    Returns None when the caller should go ahead and do the work; a dict when
    it should return that dict instead. Raises 409 on a conflicting or in-flight
    repeat.

    A missing key means the caller did not ask for idempotency — the action
    proceeds unprotected, exactly as it did before this module existed. That is
    the right default for an API other systems already call.
    """
    if not key:
        return None
    key = str(key).strip()
    if not key:
        return None
    if len(key) > MAX_KEY_LEN:
        raise HTTPException(422, f"Idempotency-Key must be at most "
                                 f"{MAX_KEY_LEN} characters")
    scoped = _scoped(key, action, username)
    digest = body_hash(body)

    claimed = (await session.execute(
        pg_insert(idem_t)
        .values(idem_key=scoped, action=action, body_hash=digest,
                result_json="", created_by=username)
        .on_conflict_do_nothing(index_elements=["idem_key"])
        .returning(idem_t.c["idem_key"]))).scalar_one_or_none()
    if claimed is not None:
        return None                      # ours — do the work

    row = (await session.execute(select(idem_t).where(
        idem_t.c["idem_key"] == scoped))).mappings().first()
    if row is None:
        # The other holder rolled back between our insert and this read, so the
        # key is free again. Refuse rather than racing for it a second time:
        # the client's own retry is the safe way to resolve this, and a loop
        # here could spin against a caller that keeps failing.
        raise HTTPException(409, "that Idempotency-Key was in use and its "
                                 "request failed — retry with a new key")
    if row["body_hash"] != digest:
        raise HTTPException(409, (
            "that Idempotency-Key has already been used for a DIFFERENT "
            "request. A retry must send the same body; a new request needs a "
            "new key."))
    if not row["result_json"]:
        raise HTTPException(409, "a request with that Idempotency-Key is "
                                 "still being processed — wait for it rather "
                                 "than sending it again")
    stored = json.loads(row["result_json"])
    stored["replayed"] = True
    return stored


async def finish(session: AsyncSession, *, key: str | None, action: str,
                 username: str, result: dict) -> None:
    """Record what the claimed key answered, so a retry can replay it."""
    if not key:
        return
    key = str(key).strip()
    if not key:
        return
    await session.execute(
        update(idem_t)
        .where(idem_t.c["idem_key"] == _scoped(key, action, username))
        .values(result_json=json.dumps(result, default=str)))
