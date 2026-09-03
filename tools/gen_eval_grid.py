#!/usr/bin/env python3
"""
tools/gen_eval_grid.py — rebuild the chapter x role coverage grid.

    python tools/gen_eval_grid.py            # rewrite tests/ai_eval/cases/grid.yaml
    python tools/gen_eval_grid.py --check    # fail if the file is stale

⚠️ EVERY EXPECTATION IS VERIFIED, NOT INVENTED, AND THE DISTINCTION COST A
DEBUGGING SESSION IN SLICE 11d.

`guard.neg.add_a_user` there asserted that §2 would answer "how do I add a
user", because "Roles, Permissions & Page Access" sounds like it would. It does
not: §2 is about which PAGES a role may open and contains none of "add a user",
"new user" or "user account". The retrieval was right and the expectation was a
guess — and a suite full of guessed expectations is one people argue with
instead of reading.

So each case here is GENERATED from a real sub-heading of a chapter the role may
read, and then RUN through `retrieve_context_scored()`. It is kept only if the
chapter it claims is actually retrieved AND ranks first. Anything that does not
hold is dropped rather than weakened.

⚠️ AND THAT IS WHY THIS IS A GENERATOR RATHER THAN A HAND-WRITTEN FILE. The
manual gains a chapter almost every phase and BM25's `idf` is corpus-wide, so
adding one perturbs the ranking for every role — measured at ~0.3 % in slice
11c, which was enough to move a chunk out of the top six. A grid maintained by
hand would drift into fiction one edit at a time; a regenerated one is either
current or loudly stale.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections import Counter

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("DATABASE_URL",
                      "postgresql+asyncpg://unused:unused@127.0.0.1:5433/unused")

OUT = _ROOT / "tests" / "ai_eval" / "cases" / "grid.yaml"

ROLES = ["store_keeper", "supervisor", "hod", "logistics", "warehouse_user",
         "qc", "qc_hod", "auditor", "admin"]

# Per role. Nine roles x eight is 72 grid cases, which with the fence probes,
# the jailbreak corpus and the near-miss pairs clears the 140-case target
# without padding it — a case that tests nothing is worse than a missing one.
PER_ROLE = 8

_LEADING_NUM = re.compile(r"^[\d.]+\s*")
_DECORATION = re.compile(r"[^\w\s/&()—\-,'’]+")
_SECTION_RX = re.compile(r"=== Section (\d+):")

HEADER = '''# Chapter x role coverage grid.
#
# ⚠️ GENERATED AND VERIFIED — DO NOT HAND-EDIT.
#     python tools/gen_eval_grid.py
#
# Each case was built from a real sub-heading of a chapter the role may read,
# then run through `retrieve_context_scored()`. It is kept only if the chapter
# it claims is actually retrieved AND ranks first. See the generator's docstring
# for why a guessed expectation is worse than no expectation.
#
# WHAT THIS GRID IS FOR: contextual precision and recall are DETERMINISTIC — no
# model, no temperature, a pure function of BM25 over a fixed corpus — so unlike
# the judged metrics they can gate a merge. A retrieval regression is the
# failure this system is actually prone to: the 800-character truncation that
# hid §2's access matrix from every non-admin role was exactly this, and it
# survived a whole phase. With this grid it would have failed the commit that
# caused it.
'''


def question_from(heading: str) -> str:
    t = _DECORATION.sub("", _LEADING_NUM.sub("", heading)).strip(" -—·")
    return re.sub(r"\s+", " ", t)


def build() -> list[dict]:
    from backend.api.ai import manual_qa as mq

    idx = mq._index()
    by_chapter: dict[int, list] = {}
    for c in idx.chunks:
        if c.heading:
            by_chapter.setdefault(c.chapter, []).append(c)

    out: list[dict] = []
    for role in ROLES:
        kept = 0
        for ch in sorted(mq.allowed_sections(role)):
            if kept >= PER_ROLE:
                break
            for chunk in by_chapter.get(ch, []):
                topic = question_from(chunk.heading)
                if not (12 <= len(topic) <= 70):
                    continue
                q = f"What does the manual say about {topic}?"
                ctx, tele = mq.retrieve_context_scored(role, q)
                shown = {int(m) for m in _SECTION_RX.findall(ctx)}
                hits = tele.get("hits") or []
                # ⚠️ BOTH CONDITIONS. "Retrieved somewhere in the top six" is a
                # weaker claim than the precision metric will make of it, and a
                # case whose expectation only just holds today is a flake
                # waiting for the next manual edit.
                if ch in shown and hits and hits[0]["chapter"] == ch:
                    out.append({"id": f"grid.{role}.ch{ch}", "role": role,
                                "prompt": q, "expect_chapters_any": [ch]})
                    kept += 1
                    break
    return out


def render(cases: list[dict]) -> str:
    lines = [HEADER]
    for c in cases:
        lines.append(f"- id: {c['id']}")
        lines.append(f"  role: {c['role']}")
        lines.append(f"  prompt: {json.dumps(c['prompt'])}")
        lines.append("  forbidden_substrings: []")
        lines.append("  must_refuse: false")
        lines.append(f"  expect_chapters_any: [{c['expect_chapters_any'][0]}]")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if grid.yaml is stale")
    args = ap.parse_args(argv)

    cases = build()
    text = render(cases)
    per_role = Counter(c["role"] for c in cases)
    chapters = sorted({c["expect_chapters_any"][0] for c in cases})

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.strip() != text.strip():
            print("❌ tests/ai_eval/cases/grid.yaml is STALE — the manual has "
                  "changed since it was generated. Re-run "
                  "`python tools/gen_eval_grid.py` and commit the result.")
            return 1
        print(f"✅ grid.yaml is current — {len(cases)} verified cases")
        return 0

    OUT.write_text(text, encoding="utf-8")
    print(f"▶ wrote {OUT.relative_to(_ROOT)} — {len(cases)} verified cases")
    print(f"  per role : {dict(per_role)}")
    print(f"  chapters : {chapters}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
