"""
backend/api/ai/manual_qa.py — role-gated Q&A over USER_MANUAL.md.

Faithful async port of legacy ai/manual_qa.py. The security model carries
over unchanged: the role filter happens at the RETRIEVAL layer, not the
prompt — a Store Keeper's context physically never contains the Admin
chapter, so the model cannot leak it. Updated for the v3.0 manual, which
grew two sections the legacy allowlist predates: §18 SME Estimator and
§19 Man-Hours (both hod/admin surfaces on the new stack).

Ollama calls go through the module object (`aic.stream`) so tests can
monkeypatch the client without a live server.

2026-08-04 — two fixes, both delegated to `manual_index`:
  * chapter parsing is now FENCE-AWARE. `# 1. Pull the new code` inside a
    ```bash block in the Operations chapter parsed as chapter 1 and, on a
    last-write-wins dict, replaced "Introduction & System Overview" with two
    lines of launchctl. Chapters 1-4 were wrong for EVERY role.
  * the prompt is now RETRIEVED, not stuffed. Admin used to receive the whole
    ~180 KB manual on every question, and every other role the first 800
    characters of each allowed chapter — the wrong 800 whenever the answer sat
    further down. The question now selects a handful of sub-sections.
The role filter still runs at the retrieval layer, BEFORE scoring, so the
security property is unchanged: a role's context cannot physically contain a
chapter it may not see.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

from . import client as aic
from . import manual_index as mx

# Which top-level USER_MANUAL.md sections each role may see. Lower roles
# cannot see higher roles' sections. §18 (SME) + §19 (Man-Hours) are
# hod/admin-locked features, mirroring the portal locks.
#
# ⚠️ A ROLE MISSING FROM THIS MAP IS NOT LOCKED OUT — IT IS GIVEN THE STORE
# KEEPER'S CHAPTERS. `allowed_sections` falls back to "store_keeper" so an
# unknown role can never be handed the whole manual, which is the right failure
# direction, but it is still the WRONG answer: the QSEP release added the `qc`
# role and forgot this map, so for a while a Quality inspector asking about
# inspections was answered out of the Store Keeper chapter and told anything
# else was "not in your section". Add the role here in the same commit that
# adds it to `auth.ROLE_META`.
_ROLE_ALLOWED: dict[str, set[int]] = {
    "store_keeper":   {1, 2, 3, 4, 10, 11, 12, 13, 21, 22},
    "supervisor":     {1, 2, 3, 4, 5, 11, 12, 13, 21, 22},
    "hod":            {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 18, 19, 21, 22},
    # Strict isolation: Logistics never sees Warehouse internals (and vice
    # versa); neither sees site-level chapters 4–6.
    "logistics":      {1, 2, 3, 9, 11, 12, 13, 14, 16, 21, 22},
    "warehouse_user": {1, 2, 3, 9, 11, 12, 13, 15, 16, 21, 22},
    # QC (2026-08-09) inspects and decides, and does nothing else. Chapter 22
    # is its own; 4 and 15 are there because an inspector has to understand
    # the issue and the goods-in they sit between, not to let them do either.
    "qc":             {1, 2, 3, 4, 11, 12, 15, 21, 22},
    # The view-only Auditor (2026-08-03) reads across every site but can
    # open only Dashboard / Stock / Records / Reports / Lining Coverage.
    # Its chapters mirror exactly that: orientation, reporting, the data
    # model and the glossary. No role operational how-tos it could not
    # perform anyway, and not the hosting chapter.
    "auditor":        {1, 2, 3, 8, 9, 10, 11, 12, 16, 20, 21, 22},
    "admin":          set(range(1, 23)),
}

_SECTION_TITLES = {
    1: "Introduction & System Overview",
    2: "Roles, Permissions & Page Access",
    3: "Login, Sidebar & Common Elements",
    4: "Store Keeper Manual",
    5: "Supervisor Manual",
    6: "HOD Manual",
    7: "Admin Manual",
    8: "Reports Module",
    9: "Automated Notifications (WhatsApp + Email)",
    10: "Data Model & Concept Reference",
    11: "Status Codes, Reason Codes & Glossary",
    12: "FAQ — Master Index",
    13: "2026-06 Feature Update",
    14: "Logistics Portal Manual",
    15: "Warehouse Portal Manual",
    16: "Cross-Role Procurement Walk-through",
    17: "Operations & Hosting",
    18: "Material Estimator (SME) Manual",
    19: "Man-Hours & Labor Tracking Manual",
    20: "Auditor (View-Only) Manual",
    21: "2026-08 Feature Update",
    22: "Quality, Safety, Employees & Procurement (QSEP)",
}


def _manual_path() -> Path:
    return Path(os.environ.get("GI_USER_MANUAL_PATH", "USER_MANUAL.md"))


@lru_cache(maxsize=1)
def _manual_text() -> str:
    path = _manual_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


@lru_cache(maxsize=1)
def _load_sections() -> dict[int, str]:
    """{section_number: full_section_text}, via the fence-aware parser.

    This used to split on a bare lookahead regex, which cannot tell a heading
    from a shell comment inside a fenced block — see manual_index.iter_chapters
    for the failure it caused.
    """
    md = _manual_text()
    if not md:
        return {}
    return {num: f"# {num}. {title}\n{body}".strip()
            for num, title, body in mx.iter_chapters(md)}


@lru_cache(maxsize=1)
def _index() -> mx.Index:
    """Built once per process — ~390 chunks over the live manual, a few ms."""
    return mx.Index(mx.build_chunks(_manual_text()))


_PER_SECTION_CHAR_CAP = 800


@lru_cache(maxsize=16)
def _context_for_role(role: str) -> str:
    """Concatenation of allowed sections, each labeled. Truncation policy
    (legacy Phase 7G): Admin gets FULL sections (deep answers live far past
    the head; keep_alive KV cache amortizes the longer prompt); every other
    role is head-truncated — site users ask short workflow questions."""
    allowed = _ROLE_ALLOWED.get(role, _ROLE_ALLOWED["store_keeper"])
    sections = _load_sections()
    if not sections:
        return ""
    is_admin = role == "admin"
    chunks = []
    for num in sorted(allowed):
        body = sections.get(num)
        if not body:
            continue
        if not is_admin and len(body) > _PER_SECTION_CHAR_CAP:
            body = body[:_PER_SECTION_CHAR_CAP] + \
                "\n[... truncated — ask for specifics if you need more ...]"
        chunks.append(f"=== Section {num}: {_SECTION_TITLES.get(num, '')} ===\n{body}")
    return "\n\n".join(chunks)


# Greeting fast-path — never call the LLM for trivial pleasantries (saves the
# full prompt-eval for every "hi" / "thanks").
_GREETING_TOKENS = {
    "hi", "hii", "hello", "hey", "heya", "yo", "hola",
    "thanks", "thank you", "ty", "thx",
    "ok", "okay", "cool", "great", "nice",
    "bye", "goodbye", "cya", "see you",
    "good morning", "good afternoon", "good evening", "morning", "evening",
}


def greeting_reply(question: str) -> str | None:
    q = re.sub(r"[!?.,…]+$", "", (question or "").strip().lower())
    if not q or len(q) > 24:
        return None
    if q in _GREETING_TOKENS:
        if q.startswith("thank") or q in {"ty", "thx"}:
            return "You're welcome — ask me anything from your section of the manual."
        if q in {"bye", "goodbye", "cya", "see you"}:
            return "Goodbye! I'll be here when you need me."
        if q in {"ok", "okay", "cool", "great", "nice"}:
            return "👍 Anything else from the manual you'd like me to look up?"
        return "Hi! I'm the Hub Assistant. Ask me anything about your role's section of the manual."
    return None


_ROLE_LABEL = {
    "store_keeper": "Store Keeper",
    "supervisor": "Supervisor",
    "hod": "Head of Department",
    "logistics": "Logistics Coordinator",
    "warehouse_user": "Warehouse Operator",
    "qc": "Quality Control Inspector",
    "admin": "Administrator",
    "auditor": "Auditor (view-only)",
}

# Role-aware refusal phrasing (never tell the Admin to "ask your Admin").
_ROLE_REFUSAL = {
    "store_keeper": "That's not in your section of the manual — please escalate to your HOD.",
    "supervisor": "That's not in your section of the manual — please escalate to your HOD.",
    "hod": "That's in the Admin chapter — please ask your Admin.",
    "logistics": "That's outside the Logistics Portal — please ask your Admin.",
    "warehouse_user": "That's outside the Warehouse Portal — please ask your Admin.",
    "qc": "That's outside the Quality section — please ask your HOD.",
    "admin": "I can't find that in the manual. Check the source markdown in USER_MANUAL.md.",
    "auditor": "That's outside the read-only Auditor view — please ask your Admin.",
}

_SYSTEM_PROMPT_TMPL = """\
You are the Hub Assistant, a documentation helper for the General \
Industries Hub warehouse system. You are talking to {username}, a {role_label}.

RULES:
- Answer ONLY using the manual sections provided below as CONTEXT.
- If the answer is not in the CONTEXT, reply with exactly this sentence \
and nothing else: "{refusal}"
- Be concise. 2-4 short sentences for most questions. Bullet lists are \
fine for steps.
- Refer to UI elements using exact names from the manual (e.g. "Entry \
Log → Consumption Log").
- Do NOT reveal information about roles other than the user's own. \
You can mention that "Admin can do X" only if §2 lists it as a permission, \
never with operational steps from a higher-role section. (Admins themselves \
have access to all sections — answer their questions fully.)
- Output plain text. No markdown headings. No code fences.

CONTEXT (manual sections {username} is allowed to see):
{context}
"""


def allowed_sections(role: str) -> set[int]:
    """The chapters this role may see. Unknown roles fall back to the LOWEST
    allowlist, never the highest — a typo in `users.role` must lose access,
    not gain it."""
    return _ROLE_ALLOWED.get(role, _ROLE_ALLOWED["store_keeper"])


def retrieve_context(role: str, question: str) -> str:
    """The passages worth showing for THIS question, from the chapters this
    role may see. Returns '' when nothing scores — the caller then falls back
    to the role's whole (truncated) context rather than answering blind."""
    if not question:
        return ""
    try:
        hits = _index().search(question, allowed=allowed_sections(role))
    except Exception:  # noqa: BLE001 — retrieval must never break the chat
        return ""
    return mx.render_context(hits)


def build_system_prompt(role: str, username: str = "",
                        question: str = "") -> str:
    """System prompt for one turn.

    With a question, CONTEXT is the retrieved passages — a few KB instead of
    the whole allowed manual, which is both much faster to evaluate and much
    more likely to contain the answer than a fixed head-truncation. Without
    one (or if nothing scores) it falls back to the role's full context, so
    the assistant degrades to its previous behaviour rather than to nothing.
    """
    context = retrieve_context(role, question) or _context_for_role(role)
    return _SYSTEM_PROMPT_TMPL.format(
        username=(username or "").strip() or "the user",
        role_label=_ROLE_LABEL.get(role, role.title() if role else "user"),
        refusal=_ROLE_REFUSAL.get(role, _ROLE_REFUSAL["store_keeper"]),
        context=context or "(manual not found on disk)",
    )


async def health() -> tuple[bool, str]:
    """(ok, msg) — server reachable, chat model pulled, manual on disk."""
    if not await aic.health():
        return False, (f"Local AI server unreachable at {aic.OLLAMA_HOST}. "
                       "Ask your admin to start the Ollama service.")
    installed = await aic.list_models()
    if installed and aic.MODEL_CHAT not in installed:
        return False, (f"Chat model {aic.MODEL_CHAT} is not pulled on the AI host "
                       f"(ollama pull {aic.MODEL_CHAT}).")
    if not _manual_path().exists():
        return False, f"USER_MANUAL.md not found at {_manual_path()}."
    return True, "ready"


async def answer_manual_question(question: str, role: str,
                                 username: str = "") -> AsyncIterator[str]:
    """Stream the answer token-by-token. On failure, yield a single friendly
    string rather than raising — the SSE endpoint never breaks mid-chat."""
    ok, msg = await health()
    if not ok:
        yield msg
        return
    question = (question or "").strip()
    if not question:
        yield "Type a question and I'll answer from your section of the manual."
        return
    canned = greeting_reply(question)
    if canned is not None:
        yield canned
        return
    system = build_system_prompt(role, username, question)
    prompt = f"User question: {question}\n\nAnswer:"
    try:
        async for chunk in aic.stream(aic.MODEL_CHAT, prompt, system=system,
                                      temperature=0.2, num_predict=512):
            yield chunk
    except RuntimeError as e:
        yield f"\n\n[Hub Assistant error: {e}]"
