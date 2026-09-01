"""
backend/api/services/bloom.py — Bloom filters for the uniqueness hot paths.

WHAT A BLOOM FILTER ACTUALLY PROMISES, because the whole design below is built
on the asymmetry and it is easy to get backwards:

    "definitely NOT in the set"   — exact, never wrong
    "MAYBE in the set"            — probabilistic, ~1% wrong at our sizing

There is no third answer. So a Bloom filter can retire a database round trip for
a name that is FREE, and can never retire one for a name that is TAKEN. That is
the right way round for every use here: somebody typing candidate usernames into
a registration form is asking about free names, and gets an instant answer;
somebody who has hit a collision gets one confirming query.

────────────────────────────────────────────────────────────────────────────
⚠️ THE FILTER IS AN ACCELERATOR. THE DATABASE IS THE AUTHORITY.

Nothing here decides anything. Every "maybe" falls through to the same query
that ran before, and every write path keeps the same `UNIQUE` index and the same
`IntegrityError` handler it had — `users.username`, `pending_users.username`,
`inventory.SAP_Code` (primary key) and the global `(SAP_Code, serial_no)` on
`asset_units` are all still the only things that decide.

That discipline is not defensive style, it is required for correctness, and the
reason is deployment shape rather than the data structure. This process holds
its own copy of the bits. When the API runs on more than one uvicorn worker, a
username registered by worker B is absent from worker A's filter until the next
refresh — worker A would then answer "definitely not present" about a name that
IS present, which is a FALSE NEGATIVE, the one answer a Bloom filter is supposed
to be incapable of. It is incapable of it about its OWN contents; a stale copy
of the set is a different problem, and no amount of hashing fixes it.

So the rule is absolute: a `probably_present() == False` may skip a read, and
may never authorise a write. Suite CP asserts exactly that.

Two things keep the staleness small anyway — `add()` on every local write, and
`refresh_all()` on a timer — but they are optimisations of a window, not a
closure of it.

────────────────────────────────────────────────────────────────────────────
WHY NO DEPENDENCY

`bitarray` and `pybloom-live` both exist. This is ~90 lines of bytearray and one
`hashlib.blake2b`, it needs no wheel on the deployment box, and it follows the
same call this project already made for `ai/manual_index.py` (BM25 by hand
rather than a vector store). The hashing is Kirsch-Mitzenmacher double hashing:
one digest, split into two 64-bit halves, `g_i(x) = h1 + i*h2` — k independent
positions for the price of one hash, which is the standard construction and not
a shortcut.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from typing import Awaitable, Callable, Iterable, Optional

logger = logging.getLogger("gi.bloom")


class BloomFilter:
    """A fixed-capacity Bloom filter over a `bytearray`.

    Sized from the expected element count and the false-positive rate you are
    willing to pay: `m = -n·ln(p) / (ln 2)²` bits and `k = (m/n)·ln 2` hashes,
    which are the standard optima. Both are computed once at construction — a
    Bloom filter cannot be resized, and adding well past `capacity` silently
    raises the false-positive rate rather than failing, which is why
    `saturation()` exists and why `refresh_all` rebuilds at the real row count.
    """

    __slots__ = ("capacity", "error_rate", "m", "k", "bits", "n",
                 "built_at", "name")

    def __init__(self, name: str, capacity: int = 10_000,
                 error_rate: float = 0.01) -> None:
        if capacity < 1:
            capacity = 1
        if not (0.0 < error_rate < 1.0):
            raise ValueError("error_rate must be strictly between 0 and 1")
        self.name = name
        self.capacity = int(capacity)
        self.error_rate = float(error_rate)
        m = math.ceil(-self.capacity * math.log(self.error_rate)
                      / (math.log(2) ** 2))
        # A floor keeps the arithmetic sane for tiny sets: a 9-row users table
        # would otherwise ask for 11 bytes, where one extra row moves the
        # false-positive rate more than the sizing formula assumes.
        self.m = max(int(m), 1024)
        self.k = max(int(round((self.m / self.capacity) * math.log(2))), 1)
        self.bits = bytearray((self.m + 7) // 8)
        self.n = 0
        self.built_at = time.time()

    # ── hashing ──────────────────────────────────────────────────────────────
    def _positions(self, key: str) -> Iterable[int]:
        """The k bit positions for `key`, by Kirsch-Mitzenmacher double hashing.

        ⚠️ THE KEY IS CASEFOLDED AND STRIPPED HERE, once, so every caller cannot
        forget to. Usernames and SAP codes are compared case-insensitively by
        the queries these filters front (`func.trim`, `lower`), and a filter
        that treated "Sunil" and "sunil" as different keys would answer
        "definitely free" about a name that is taken — a false negative
        manufactured by the wrapper rather than by the mathematics.
        """
        norm = str(key or "").strip().casefold().encode("utf-8")
        digest = hashlib.blake2b(norm, digest_size=16).digest()
        h1 = int.from_bytes(digest[:8], "big")
        h2 = int.from_bytes(digest[8:], "big") | 1   # odd, so it generates
        for i in range(self.k):
            yield (h1 + i * h2) % self.m

    # ── the two operations ───────────────────────────────────────────────────
    def add(self, key: str) -> None:
        for pos in self._positions(key):
            self.bits[pos >> 3] |= 1 << (pos & 7)
        self.n += 1

    def probably_present(self, key: str) -> bool:
        """False means DEFINITELY absent from this filter's snapshot.

        True means "maybe" and obliges the caller to confirm against the
        database. See the module docstring for why False obliges the caller to
        confirm too, on any path that writes.
        """
        return all(self.bits[pos >> 3] & (1 << (pos & 7))
                   for pos in self._positions(key))

    __contains__ = probably_present

    # ── introspection (health endpoint + tests) ──────────────────────────────
    def saturation(self) -> float:
        """Elements added / capacity. Past 1.0 the error rate is above spec."""
        return self.n / self.capacity if self.capacity else 0.0

    def stats(self) -> dict:
        return {"name": self.name, "bits": self.m, "hashes": self.k,
                "bytes": len(self.bits), "elements": self.n,
                "capacity": self.capacity, "error_rate": self.error_rate,
                "saturation": round(self.saturation(), 3),
                "age_s": round(time.time() - self.built_at, 1)}


# ── the registry ────────────────────────────────────────────────────────────
# Each entry names a loader that returns every key currently in the set. The
# loader runs at startup and on the refresh timer; `add()` covers the window
# in between for writes this process made.
Loader = Callable[[], Awaitable[list[str]]]

_FILTERS: dict[str, BloomFilter] = {}
_LOADERS: dict[str, Loader] = {}
# Headroom over the live row count. A filter rebuilt at exactly today's size is
# already over its designed error rate by tomorrow morning.
_HEADROOM = 4
_MIN_CAPACITY = 2_048

REFRESH_SECONDS = 300.0


def register(name: str, loader: Loader) -> None:
    _LOADERS[name] = loader


def get(name: str) -> Optional[BloomFilter]:
    """The filter, or None when it has never been built.

    ⚠️ RETURNS None RATHER THAN AN EMPTY FILTER, and callers must treat None as
    "no information". An empty Bloom filter answers "definitely not present" to
    every question, so handing one back before it is populated would turn the
    startup window into a period where every username looks free — the exact
    false negative the whole module is arranged to avoid.
    """
    return _FILTERS.get(name)


async def rebuild(name: str) -> Optional[BloomFilter]:
    """Rebuild one filter from its loader. Never raises — a filter that cannot
    be built is left absent, and every caller then falls back to the database,
    which is what they do on a "maybe" anyway."""
    loader = _LOADERS.get(name)
    if loader is None:
        return None
    try:
        keys = await loader()
    except Exception as e:                                  # noqa: BLE001
        logger.warning("bloom: could not build %r: %s", name, e)
        return None
    capacity = max(len(keys) * _HEADROOM, _MIN_CAPACITY)
    bf = BloomFilter(name, capacity=capacity)
    for key in keys:
        if key:
            bf.add(key)
    _FILTERS[name] = bf
    return bf


async def refresh_all() -> dict:
    """Rebuild every registered filter. Called at startup and on the timer."""
    out = {}
    for name in list(_LOADERS):
        bf = await rebuild(name)
        out[name] = bf.stats() if bf else None
    return out


def add(name: str, key: str) -> None:
    """Record a key this process just wrote. Silent no-op when the filter is
    absent — the next rebuild will pick the row up from the database."""
    bf = _FILTERS.get(name)
    if bf is not None and key:
        bf.add(key)


def stats() -> dict:
    return {name: bf.stats() for name, bf in _FILTERS.items()}


_TASK: Optional[asyncio.Task] = None


async def _refresh_loop() -> None:
    while True:
        try:
            await asyncio.sleep(REFRESH_SECONDS)
            await refresh_all()
        except asyncio.CancelledError:
            raise
        except Exception as e:                              # noqa: BLE001
            logger.warning("bloom refresh loop: %s", e)


def start_refresh_loop() -> None:
    """Idempotent — main.py's startup hook may run more than once in tests."""
    global _TASK
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_refresh_loop())


async def stop_refresh_loop() -> None:
    global _TASK
    if _TASK is not None and not _TASK.done():
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):         # noqa: BLE001
            pass
    _TASK = None


# ── the three sets ──────────────────────────────────────────────────────────
# Chosen for the same reason: read constantly, written rarely, and asked about
# in a loop somewhere.

USERNAMES = "usernames"
SAP_CODES = "sap_codes"
ASSET_SERIALS = "asset_serials"


def asset_key(sap_code: str, serial_no: str) -> str:
    """Identity of a serialised unit is the PAIR, globally (alembic
    a3c17e9b25d4). Joining with a character that cannot appear in either half
    keeps ('AB','C') and ('A','BC') distinct."""
    return f"{str(sap_code or '').strip()}\x1f{str(serial_no or '').strip()}"


async def _load_usernames() -> list[str]:
    """⚠️ BOTH REGISTRIES, because a name is only free if it is free in both.

    `auth.register` refuses a username that exists in `users`, and separately
    refuses one already awaiting approval in `pending_users`. A filter loaded
    from `users` alone would answer "definitely free" for a name somebody
    requested an hour ago — correct about the table it was built from, and
    wrong about the question being asked.
    """
    from sqlalchemy import select

    from ..db import SessionLocal
    from .ledger import _MD
    users_t = _MD.tables["users"]
    pending_t = _MD.tables["pending_users"]
    async with SessionLocal() as s:
        rows = (await s.execute(select(users_t.c["username"]))).scalars().all()
        pend = (await s.execute(
            select(pending_t.c["username"]))).scalars().all()
    return [str(u) for u in list(rows) + list(pend) if u]


async def _load_sap_codes() -> list[str]:
    from sqlalchemy import select

    from ..db import SessionLocal
    from .ledger import _MD
    inv_t = _MD.tables["inventory"]
    async with SessionLocal() as s:
        rows = (await s.execute(select(inv_t.c["SAP_Code"]))).scalars().all()
    return [str(c).strip() for c in rows if c]


async def _load_asset_serials() -> list[str]:
    from sqlalchemy import select

    from ..db import SessionLocal
    from .ledger import _MD
    unit_t = _MD.tables["asset_units"]
    async with SessionLocal() as s:
        rows = (await s.execute(select(unit_t.c["SAP_Code"],
                                       unit_t.c["serial_no"]))).all()
    return [asset_key(r[0], r[1]) for r in rows if r[0] and r[1]]


register(USERNAMES, _load_usernames)
register(SAP_CODES, _load_sap_codes)
register(ASSET_SERIALS, _load_asset_serials)
