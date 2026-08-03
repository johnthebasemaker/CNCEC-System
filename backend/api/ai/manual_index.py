"""
backend/api/ai/manual_index.py — chapter parsing + lexical retrieval over
USER_MANUAL.md. Dependency-free (no vector store, no embeddings, no extra
package) and fast enough to run per question on the request thread.

Two problems this module exists to solve.

**1. The chapter splitter was eating chapters 1-4.**
The manual's Operations chapter contains shell blocks like

    ```bash
    # 1. Pull the new code
    git pull
    ```

and the old splitter matched `^# \\d+\\.` line-by-line with no idea that it
was inside a fence. Those shell comments parsed as chapters 1, 2, 3 and 4 and,
because the parse wrote into a dict keyed by number, the LAST match won — so
"Introduction & System Overview" was silently replaced by
`# 1. Stop the app first ...` and two lines of `launchctl`. Every role's
assistant context began with that. `iter_chapters()` tracks fences, so a `#`
inside a code block is body text, which is what it always was.

**2. The whole allowed manual was stuffed into every prompt.**
Admin got all ~180 KB of it on every question; every other role got the first
800 characters of each allowed chapter, which is the wrong 800 characters
whenever the answer lives further down. Retrieval fixes both at once: chunk the
manual at sub-heading level, score the chunks against the actual question, and
send only what matters.

**The security model is unchanged and is still enforced at the retrieval
layer.** `search()` takes the set of chapters the role may see and filters to
it BEFORE scoring, so a Store Keeper's context cannot physically contain an
Admin chunk — the model is never asked to keep a secret it was shown.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_CHAPTER_RE = re.compile(r"^# (\d+)\.\s+(.+?)\s*$")
_SUBHEAD_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_WORD_RE = re.compile(r"[a-z0-9_]+")

# Words that carry no retrieval signal in this corpus. Deliberately short —
# an over-eager stoplist hurts more than it helps on a 19-chapter manual.
_STOP = frozenset("""
a an the and or but if then than that this these those there here of to in on
at by for with from as is are was were be been being do does did doing have
has had i you he she it we they me my your our their its his her them us
what which who whom how when where why can could should would will shall may
might must not no nor so such only own same too very s t don now
""".split())


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage: a sub-section of a chapter, or a chapter's
    lead-in text before its first sub-heading."""
    chapter: int
    chapter_title: str
    heading: str
    text: str

    @property
    def label(self) -> str:
        head = f" › {self.heading}" if self.heading else ""
        return f"Section {self.chapter}: {self.chapter_title}{head}"


def iter_chapters(md: str) -> list[tuple[int, str, str]]:
    """[(number, title, body)] for every top-level `# N. Title` chapter.

    Fence-aware: a `#` line inside a ``` or ~~~ block is body text, never a
    heading. Duplicate numbers keep the FIRST occurrence — a real manual is
    numbered once, and preferring the first means a stray later match can no
    longer overwrite a genuine chapter.
    """
    out: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    fence: str | None = None
    num: int | None = None
    title = ""
    body: list[str] = []

    def flush():
        if num is not None and num not in seen:
            seen.add(num)
            out.append((num, title, "\n".join(body).strip()))

    for line in md.splitlines():
        f = _FENCE_RE.match(line)
        if f:
            mark = f.group(1)
            if fence is None:
                fence = mark
            elif fence == mark:
                fence = None
            if num is not None:
                body.append(line)
            continue
        if fence is None:
            m = _CHAPTER_RE.match(line)
            if m:
                flush()
                num, title, body = int(m.group(1)), m.group(2).strip(), []
                continue
        if num is not None:
            body.append(line)
    flush()
    return out


def chunk_chapter(num: int, title: str, body: str,
                  *, max_chars: int = 2200) -> list[Chunk]:
    """Split one chapter at `##`/`###` boundaries (fence-aware again), then
    hard-wrap any passage that is still enormous so a single sprawling
    sub-section cannot monopolise the context budget."""
    pieces: list[tuple[str, list[str]]] = [("", [])]
    fence: str | None = None
    for line in body.splitlines():
        f = _FENCE_RE.match(line)
        if f:
            mark = f.group(1)
            fence = mark if fence is None else (None if fence == mark else fence)
            pieces[-1][1].append(line)
            continue
        if fence is None:
            m = _SUBHEAD_RE.match(line)
            if m:
                pieces.append((m.group(2).strip(), []))
                continue
        pieces[-1][1].append(line)

    out: list[Chunk] = []
    for heading, lines in pieces:
        text = "\n".join(lines).strip()
        if not text:
            continue
        if len(text) <= max_chars:
            out.append(Chunk(num, title, heading, text))
            continue
        # Wrap on paragraph boundaries so a split never lands mid-sentence.
        buf: list[str] = []
        size = 0
        for para in text.split("\n\n"):
            if size + len(para) > max_chars and buf:
                out.append(Chunk(num, title, heading, "\n\n".join(buf)))
                buf, size = [], 0
            buf.append(para)
            size += len(para) + 2
        if buf:
            out.append(Chunk(num, title, heading, "\n\n".join(buf)))
    return out


def build_chunks(md: str) -> list[Chunk]:
    out: list[Chunk] = []
    for num, title, body in iter_chapters(md):
        out.extend(chunk_chapter(num, title, body))
    return out


def _tokens(s: str) -> list[str]:
    """Unigrams (stopwords removed) plus JOINED adjacent-word bigrams.

    The bigrams are formed BEFORE stopword removal, which is the whole point:
    users type "how do I log in", and dropping the stopword `in` leaves `log`,
    which does not match the chapter titled "Login, Sidebar & Common Elements".
    Joining the raw pair produces `login` and the match lands. The same trick
    covers "sign in"/"signin", "check out"/"checkout", "hand over"/"handover"
    and the other two-word forms this manual writes as one word.
    """
    raw = _WORD_RE.findall(s.lower())
    out = [w for w in raw if len(w) > 1 and w not in _STOP]
    for a, b in zip(raw, raw[1:]):
        joined = a + b
        if 5 <= len(joined) <= 20:
            out.append(joined)
    return out


class Index:
    """BM25 over the manual's chunks.

    BM25 rather than raw term frequency because the manual repeats its
    vocabulary heavily — "material", "stock" and "site" appear in nearly every
    chapter, so an un-weighted count retrieves the longest chapter every time
    regardless of the question. IDF suppresses exactly those terms, and the
    length normaliser stops a 2,000-character passage from outranking the
    150-character one that actually answers the question.
    """

    K1 = 1.4
    B = 0.72
    HEADING_BOOST = 2.4   # a hit in the sub-heading is worth several in prose

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.tf: list[dict[str, int]] = []
        self.head_tokens: list[set[str]] = []
        self.lens: list[int] = []
        df: dict[str, int] = {}
        for c in chunks:
            toks = _tokens(c.text)
            counts: dict[str, int] = {}
            for t in toks:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            self.lens.append(max(len(toks), 1))
            self.head_tokens.append(set(_tokens(f"{c.chapter_title} {c.heading}")))
            for t in counts:
                df[t] = df.get(t, 0) + 1
        n = max(len(chunks), 1)
        self.avg_len = sum(self.lens) / n
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, i: int, q_tokens: list[str]) -> float:
        tf, dl = self.tf[i], self.lens[i]
        head = self.head_tokens[i]
        total = 0.0
        for t in q_tokens:
            idf = self.idf.get(t)
            if idf is None:
                continue
            f = tf.get(t, 0)
            if f:
                total += idf * (f * (self.K1 + 1)) / (
                    f + self.K1 * (1 - self.B + self.B * dl / self.avg_len))
            if t in head:
                total += idf * self.HEADING_BOOST
        return total

    def search(self, query: str, *, allowed: set[int],
               k: int = 6, char_budget: int = 7000) -> list[Chunk]:
        """Top-scoring chunks the role may see, within a character budget.

        `allowed` is applied BEFORE scoring — that is the security boundary.
        A chunk from a chapter this role cannot see is never a candidate, so
        it cannot reach the prompt no matter what the question asks for.
        """
        q = _tokens(query)
        if not q:
            return []
        scored = [(self.score(i, q), i) for i in range(len(self.chunks))
                  if self.chunks[i].chapter in allowed]
        scored.sort(key=lambda x: (-x[0], x[1]))
        out: list[Chunk] = []
        used = 0
        for s, i in scored:
            if s <= 0 or len(out) >= k:
                break
            c = self.chunks[i]
            if used + len(c.text) > char_budget and out:
                continue
            out.append(c)
            used += len(c.text)
        # Present them in manual order: the model reads a coherent document
        # rather than a relevance-ranked jumble, and cross-references resolve.
        out.sort(key=lambda c: (c.chapter, self.chunks.index(c)))
        return out


def render_context(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"=== {c.label} ===\n{c.text}" for c in chunks)
