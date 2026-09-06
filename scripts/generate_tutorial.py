#!/usr/bin/env python3
"""
scripts/generate_tutorial.py — PHASE 12 PROTOTYPE ORCHESTRATOR.

Turns one tracked YAML script into one finished tutorial MP4:

    YAML ─▶ [egress guard] ─▶ HeyGen payload ─▶ avatar audio ─┐   ← pass A
                                        (measured durations)   │
                                                               ▼
              shot list + per-beat HOLDS ─▶ Playwright ─▶ screencast.webm  ← pass B
                                              + beats.json    │
                                                              ▼
                                                    ffmpeg composite ─▶ .mp4

⚠️ PASS A COMES FIRST, AND THAT IS THE DESIGN, NOT THE ORDER IT WAS WRITTEN IN.
A Playwright-driven UI is far faster than a person describing it: the first run
of this pipeline recorded first and narrated second, and overran ALL SIX beats
— 40.2 s of speech over a 19.6 s recording, the worst by 5.15 s. So the audio
is rendered and MEASURED first, and each UI step is then held for the length of
its own line. With a real key that ordering is unchanged: HeyGen is called
BEFORE the browser opens, because the avatar's timing is what the screencast
has to be cut to.

⚠️ THE ONE RULE THIS FILE ENFORCES (P12-1). The HeyGen request is built from
the YAML's `narration:` block and nothing else, and `assert_text_only()`
refuses to send any string that is not a `say:` line in that file. No frame, no
screenshot, no database row, no API response. The screencast — which is the
half that CAN carry real stock and real employee names — never leaves the
machine: the avatar comes back as a clip and ffmpeg composites LOCALLY.

⚠️ NO HEYGEN KEY EXISTS YET, so the default run MOCKS the call: it prints the
exact JSON that would be posted and synthesises a stand-in avatar locally
(`say` for the voice, Pillow for the card). Everything downstream of the call
— alpha compositing, audio placement, beat-aligned captions, the encode — is
the real pipeline, unchanged, so the mock proves the part that would otherwise
be assumed.

⚠️ AND ONE MEASURED FACT ABOUT THIS MACHINE: Homebrew's ffmpeg 8.1.2 is built
WITHOUT libfreetype, so `drawtext` does not exist here ("Unknown filter
'drawtext'"). Every caption is therefore rendered to an RGBA PNG with Pillow
and composited with `overlay`. That is not a workaround — it is better: real
fonts, brand colours, and no filtergraph escaping for text a human wrote.

Usage
-----
    .venv/bin/python scripts/generate_tutorial.py
    .venv/bin/python scripts/generate_tutorial.py --script scripts/tutorials/x.yaml
    .venv/bin/python scripts/generate_tutorial.py --skip-record   # re-composite
    .venv/bin/python scripts/generate_tutorial.py --reuse-stack   # batch mode

⚠️ Do not run this while `cd tests/e2e && npm test` is running. Both own
`gihub_e2e_pw`, :8010 and :5183, and the loser fails looking like a flaky spec.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
E2E = ROOT / "tests" / "e2e"
RECORDER = ROOT / "tests" / "video_gen"
DEFAULT_SCRIPT = ROOT / "scripts" / "tutorials" / "store_keeper_hub_assistant.yaml"
DEFAULT_OUT = ROOT / "docs" / "tutorials" / "out"

FPS = 30
CANVAS = (1920, 1080)
AVATAR_PX = 360
GI_NAVY = (0, 31, 64, 255)
GI_GOLD = (201, 162, 39, 255)
INK = (233, 238, 247, 255)
DIM = (132, 146, 168, 255)

FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# ⚠️ WHERE THE AVATAR SITS IS A PER-TUTORIAL CHOICE, NOT A HOUSE STYLE, and
# this is the defect the first composite had: the stand-in was pinned bottom
# right, which is exactly where the Hub Assistant panel opens — so the tutorial
# about the assistant spent thirty seconds with the assistant behind a talking
# head. The rule is simply that the avatar never covers the control being
# demonstrated, and only the script's author knows which control that is.
CORNERS = {
    "bottom-left": ("56", "H-h-56"),
    "bottom-right": ("W-w-56", "H-h-56"),
    "top-left": ("56", "56"),
    "top-right": ("W-w-56", "56"),
}

# HeyGen's v2 generate endpoint. Never called without --live AND a key.
HEYGEN_URL = "https://api.heygen.com/v2/video/generate"


# ══════════════════════════════════════════════════════════════════════════
# 0. small shell helpers
# ══════════════════════════════════════════════════════════════════════════
def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable[:200]}{'…' if len(printable) > 200 else ''}")
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg(args: list[str]) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def probe_seconds(path: pathlib.Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def hms(seconds: float) -> str:
    return f"{int(seconds // 60):d}:{seconds % 60:05.2f}"


# ══════════════════════════════════════════════════════════════════════════
# 1. the script
# ══════════════════════════════════════════════════════════════════════════
REQUIRED = ("tutorial_id", "title", "role", "hub_role", "language",
            "assistant_question", "assistant_answer", "narration")


def load_script(path: pathlib.Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not doc.get(k)]
    if missing:
        sys.exit(f"FATAL: {path.name} is missing: {', '.join(missing)}")
    for i, line in enumerate(doc["narration"]):
        if not line.get("beat") or not line.get("say"):
            sys.exit(f"FATAL: narration[{i}] needs both `beat:` and `say:`")
    return doc


# A beat's narration is followed by this much silence before the UI moves on.
# Not zero: a step that cuts on the last syllable reads as clipped.
BREATH_S = 0.35


def shot_list(doc: dict, holds: dict[str, int], think_ms: int) -> dict:
    """The JSON the spec reads. Python owns the YAML; TypeScript never parses it."""
    red = doc.get("redaction") or {}
    return {
        "tutorial_id": doc["tutorial_id"],
        "role": doc["role"],
        "language": doc["language"],
        "assistant_question": doc["assistant_question"],
        "assistant_answer": doc["assistant_answer"].strip(),
        "assistant_think_ms": think_ms,
        # ⚠️ THE WHOLE POINT OF PASS A. Each beat is held for as long as its
        # narration actually takes, measured from the rendered audio. See the
        # module docstring: the first run of this pipeline overran every one of
        # six beats by 2.3-5.2 s because the holds were guesses.
        "holds": holds,
        "mask": list(red.get("mask") or []),
        "replace": dict(red.get("replace") or {}),
    }


# ══════════════════════════════════════════════════════════════════════════
# 2. record — Playwright against the isolated E2E stack (rule 15)
# ══════════════════════════════════════════════════════════════════════════
def ensure_node_modules() -> None:
    """
    Point the recorder at the E2E suite's `node_modules` instead of installing
    a second copy. Same Playwright build as the gate, no network, ~300 MB saved
    — and it cannot drift into recording against a different browser than the
    one the suite tests with.
    """
    link = RECORDER / "node_modules"
    target = E2E / "node_modules"
    if not target.exists():
        sys.exit(f"FATAL: {target} is missing — run `npm ci` in tests/e2e first")
    if link.is_symlink() or link.exists():
        return
    link.symlink_to(os.path.relpath(target, RECORDER))
    print(f"[record] linked {link.relative_to(ROOT)} → {target.relative_to(ROOT)}")


def record(shot: dict, work: pathlib.Path, reuse_stack: bool) -> tuple[pathlib.Path, dict]:
    ensure_node_modules()
    work.mkdir(parents=True, exist_ok=True)
    shot_path = work / "shotlist.json"
    shot_path.write_text(json.dumps(shot, indent=2), encoding="utf-8")

    env = {**os.environ,
           "GI_TUTORIAL_SHOTLIST": str(shot_path),
           "GI_TUTORIAL_OUT": str(work)}
    if reuse_stack:
        env["GI_VIDEO_REUSE_STACK"] = "1"

    t0 = time.time()
    subprocess.run(
        ["npx", "playwright", "test", "-c", "../video_gen/playwright.config.ts"],
        cwd=E2E, env=env, check=True)
    print(f"      playwright finished in {time.time() - t0:.1f}s")

    beats = json.loads((work / "beats.json").read_text(encoding="utf-8"))
    return pathlib.Path(beats["video"]), beats


# ══════════════════════════════════════════════════════════════════════════
# 3. the HeyGen boundary
# ══════════════════════════════════════════════════════════════════════════
class EgressRefused(RuntimeError):
    pass


# Shapes that mean "this came out of the database, not out of the script".
_DATA_SHAPES = (
    (re.compile(r"\bdata:[a-z]+/"), "a data: URI (an embedded asset)"),
    (re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), "a base64 blob"),
    (re.compile(r"(?:^|\s)/(?:Users|var|tmp|home)/\S+"), "an absolute filesystem path"),
    (re.compile(r"\.(?:png|jpe?g|webm|mp4|mov|db|xlsx|csv|pdf)\b", re.I), "a file reference"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (re.compile(r"\b\d{6,}\b"), "a long digit run (an ID or SAP code)"),
)


def assert_text_only(payload: dict, allowed: set[str]) -> None:
    """
    The egress guard. Two independent checks, because either alone fails open:

      1. STRUCTURAL — every leaf is a scalar. A payload cannot carry bytes, a
         file handle or a nested asset descriptor, because there is nowhere in
         the tree for one to sit.
      2. PROVENANCE — every free-text leaf longer than a token is EXACTLY a
         `say:` line from the tracked YAML. Not "looks safe", not "passed a
         scrubber": character-for-character a string a person reviewed in a
         diff. A scrubber is a filter that gets better over time; this is a
         whitelist that is complete on day one.

    The `_DATA_SHAPES` sweep runs anyway, over the whitelisted strings. It
    exists to catch a script whose AUTHOR pasted a real name or a SAP code into
    the narration — provenance says the string was reviewed, not that the
    review was any good.
    """
    def walk(node, path="payload"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, (str, int, float, bool)) or node is None:
            if isinstance(node, str) and " " in node.strip():
                if node not in allowed:
                    raise EgressRefused(
                        f"{path} carries free text that is not a reviewed "
                        f"narration line:\n    {node[:120]!r}")
        else:
            raise EgressRefused(f"{path} is a {type(node).__name__}, not a scalar")

    walk(payload)

    for text in allowed:
        for rx, what in _DATA_SHAPES:
            if rx.search(text):
                raise EgressRefused(
                    f"narration line looks like {what} — refusing to send:\n"
                    f"    {text[:120]!r}")


def heygen_payload(doc: dict) -> tuple[dict, set[str]]:
    """
    Build the request AND the whitelist of strings it is allowed to contain, in
    the same function.

    ⚠️ These two were separate on the first run and the guard immediately
    refused its own payload — `payload.title` is composed from four tracked
    YAML scalars and the whitelist only knew about narration lines. That is the
    guard working, but a whitelist assembled somewhere else is a whitelist that
    drifts: the next field added here would have been "fixed" by widening the
    other function, and the check would have quietly become decorative.
    Returning both from one place makes every emitted string name its source.
    """
    av = doc.get("avatar") or {}
    title = f"GI Hub · {doc['title']} · {doc['hub_role']} · {doc['language']}"
    allowed = {title}
    for line in doc["narration"]:
        allowed.add(line["say"].strip())
        allowed.add(" ".join(line["say"].split()))
    payload = {
        "caption": False,
        "dimension": {"width": AVATAR_PX * 3, "height": AVATAR_PX * 3},
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": av.get("avatar_id", "PLACEHOLDER_AVATAR_ID"),
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "voice_id": av.get("voice_id", "PLACEHOLDER_VOICE_ID"),
                    "input_text": line["say"].strip(),
                },
                "background": {"type": "color", "value": "#00000000"},
            }
            for line in doc["narration"]
        ],
    }
    payload["title"] = title
    return payload, allowed


def heygen_live(payload: dict, key: str) -> dict:
    """
    ⚠️ UNVERIFIED. No HeyGen key has ever been used against this repository, so
    this function has never executed. It is written from the published v2
    contract and must be treated as a first draft until one real 200 is seen.
    Deliberately NOT reachable without both --live and HEYGEN_API_KEY.
    """
    import requests
    r = requests.post(HEYGEN_URL, json=payload, timeout=60,
                      headers={"X-Api-Key": key, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════════════════
# 4. the mock avatar — local voice + local card
# ══════════════════════════════════════════════════════════════════════════
def say_to_wav(text: str, dest: pathlib.Path, voice: str | None) -> pathlib.Path | None:
    """macOS `say` stands in for HeyGen's TTS. Returns None when unavailable."""
    if not shutil.which("say"):
        return None
    aiff = dest.with_suffix(".aiff")
    cmd = ["say", "-r", "168", "-o", str(aiff)]
    if voice:
        cmd += ["-v", voice]
    try:
        run(cmd + [text])
    except subprocess.CalledProcessError:
        return None
    ffmpeg(["-i", str(aiff), "-ar", "48000", "-ac", "1", str(dest)])
    aiff.unlink(missing_ok=True)
    return dest


def synthesize(doc: dict, work: pathlib.Path, voice: str | None) -> list[dict]:
    """
    PASS A — render every narration line and MEASURE it, before a browser is
    opened.

    ⚠️ THIS ORDERING IS THE MOST IMPORTANT THING IN THE FILE, and it was not
    obvious until the pipeline was run the other way round. The first version
    recorded first and narrated second, and the timing report said this:

        beat            starts     window     narration   verdict
        title             0.86s     3.15s      8.30s   OVERRUNS by  5.15s
        open_assistant    4.02s     4.17s      6.46s   OVERRUNS by  2.29s
        question          8.18s     1.23s      5.41s   OVERRUNS by  4.17s
        thinking          9.41s     1.83s      5.98s   OVERRUNS by  4.15s
        answer           11.24s     5.74s      8.71s   OVERRUNS by  2.97s
        close            16.98s     2.66s      5.34s   OVERRUNS by  2.68s

    Six beats, six overruns, 40.2 s of speech over a 19.6 s recording. A UI
    driven by Playwright is *fast* — far faster than a person explaining it —
    so a narration written for humans will always outrun it. The fix is not a
    longer guessed pause: it is to hold each step for the MEASURED length of
    its own line, which means the audio has to exist first.

    Same discipline as `docs/exec_video/project_v3/build.py`, which cuts every
    scene to its measured WAV rather than to a guess — arrived at here
    independently, by getting it wrong.
    """
    clips: list[dict] = []
    vo = work / "vo"
    vo.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(doc["narration"]):
        text = " ".join(line["say"].split())
        wav = say_to_wav(text, vo / f"{i:02d}_{line['beat']}.wav", voice)
        # No `say` on this host: estimate at ~2.6 words/s so the holds are
        # still roughly right and the pipeline still produces a silent video.
        dur = probe_seconds(wav) if wav else len(text.split()) / 2.6
        clips.append({"beat": line["beat"], "dur": dur, "wav": wav, "text": text})
    return clips


def holds_from(clips: list[dict]) -> dict[str, int]:
    return {c["beat"]: int((c["dur"] + BREATH_S) * 1000) for c in clips}


def place_narration(clips: list[dict], beats: list[dict], total_s: float,
                    work: pathlib.Path) -> tuple[pathlib.Path, list[dict]]:
    """
    PASS B's other half — lay the rendered lines onto the MEASURED beat times.

    The report is printed either way. A green one is not decoration: it is the
    evidence that the holds computed in pass A survived contact with a real
    browser, and a UI change that makes a step slower shows up here as a gap
    rather than as narration talking over the next screen.
    """
    at = {b["id"]: b["t_ms"] / 1000.0 for b in beats}
    placed = []
    for c in clips:
        if c["beat"] not in at:
            print(f"  ⚠️  narration beat {c['beat']!r} was never stamped by the "
                  f"recording — skipped")
            continue
        placed.append({**c, "start": at[c["beat"]]})

    print("\n  beat            starts     window     narration   verdict")
    print("  " + "─" * 62)
    worst = 0.0
    for i, c in enumerate(placed):
        nxt = placed[i + 1]["start"] if i + 1 < len(placed) else total_s
        window = nxt - c["start"]
        over = c["dur"] - window
        worst = max(worst, over)
        verdict = "ok" if over <= 0.05 else f"OVERRUNS by {over:5.2f}s"
        print(f"  {c['beat']:<14} {c['start']:7.2f}s {window:8.2f}s "
              f"{c['dur']:9.2f}s   {verdict}")
    print(f"  {'':<14} {'':>7}  {'':>8}  worst overrun: {worst:+.2f}s\n")

    out = work / "narration.wav"
    have = [c for c in placed if c["wav"]]
    if not have:
        ffmpeg(["-f", "lavfi", "-t", f"{total_s:.3f}",
                "-i", "anullsrc=r=48000:cl=mono", str(out)])
        print("  ⚠️  no `say` on this host — the narration track is silence")
        return out, placed

    args: list[str] = ["-f", "lavfi", "-t", f"{total_s:.3f}",
                       "-i", "anullsrc=r=48000:cl=mono"]
    for c in have:
        args += ["-i", str(c["wav"])]
    parts = [f"[{n}:a]adelay={int(c['start'] * 1000)}:all=1[a{n}]"
             for n, c in enumerate(have, start=1)]
    mix = "".join(f"[a{n}]" for n in range(1, len(have) + 1))
    graph = ";".join(parts) + f";[0:a]{mix}amix=inputs={len(have) + 1}:normalize=0[out]"
    ffmpeg(args + ["-filter_complex", graph, "-map", "[out]",
                   "-ar", "48000", "-ac", "1", str(out)])
    return out, placed


def avatar_card(doc: dict, dest: pathlib.Path) -> pathlib.Path:
    """A round, transparent-background stand-in for HeyGen's talking head."""
    from PIL import Image, ImageDraw, ImageFont
    size = AVATAR_PX * 3
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=GI_NAVY, outline=GI_GOLD, width=14)
    f_big = _font(FONT_BOLD, 96)
    f_sm = _font(FONT_REG, 46)
    _centre(d, size // 2, size // 2 - 110, "MOCK", f_big, GI_GOLD)
    _centre(d, size // 2, size // 2 - 10, "AVATAR", f_big, GI_GOLD)
    av = (doc.get("avatar") or {}).get("avatar_id", "—")
    _centre(d, size // 2, size // 2 + 96, "HeyGen would render", f_sm, DIM)
    _centre(d, size // 2, size // 2 + 152, av, f_sm, INK)
    img.save(dest)
    return dest


def avatar_mask(dest: pathlib.Path) -> pathlib.Path:
    """A greyscale circle: white inside, black outside. `alphamerge` fodder."""
    from PIL import Image, ImageDraw
    size = AVATAR_PX * 3
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    m.save(dest)
    return dest


def probe_pix_fmt(path: pathlib.Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()


def build_avatar_clip(doc: dict, narration: pathlib.Path, total_s: float,
                      work: pathlib.Path) -> tuple[pathlib.Path, bool]:
    """
    Encode the stand-in the way a real transparent-background HeyGen clip would
    arrive, so the composite exercises the ACTUAL alpha path.

    ⚠️ AND THEN PROVE IT, BECAUSE THE ENCODE LIES. Homebrew's ffmpeg 8.1.2
    accepts `-pix_fmt yuva420p` with `libvpx-vp9`, exits 0, prints no warning
    — and writes a `yuv420p` stream. The alpha is silently discarded, and the
    only symptom is a black square around the avatar in the finished MP4. Same
    shape as rule 16: an exit code is not a result, so the pix_fmt is READ BACK
    and a mask path is used when the claim does not hold. VP8 drops it too;
    both were checked.
    """
    card = avatar_card(doc, work / "avatar_card.png")
    alpha = work / "avatar.webm"
    try:
        ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(card),
                "-i", str(narration), "-t", f"{total_s:.3f}",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "1M",
                "-auto-alt-ref", "0", "-deadline", "realtime", "-cpu-used", "5",
                "-c:a", "libopus", "-b:a", "96k", str(alpha)])
        got = probe_pix_fmt(alpha)
        if got.startswith("yuva"):
            return alpha, True
        print(f"  ⚠️  VP9 encode reported success but wrote {got}, not yuva420p — "
              f"this build has no alpha. Using the mask path instead.")
    except subprocess.CalledProcessError:
        print("  ⚠️  VP9 alpha encode failed — using the mask path instead")
    alpha.unlink(missing_ok=True)

    opaque = work / "avatar.mp4"
    ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(card),
            "-i", str(narration), "-t", f"{total_s:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k", str(opaque)])
    return opaque, False


# ══════════════════════════════════════════════════════════════════════════
# 5. overlays — Pillow, because this ffmpeg has no drawtext
# ══════════════════════════════════════════════════════════════════════════
def _font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _centre(draw, cx: int, y: int, text: str, font, fill) -> None:
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _panel(draw, box, radius=18, fill=(0, 31, 64, 214), outline=GI_GOLD, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def watermark_png(doc: dict, live: bool, dest: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    title, sub = doc["title"], doc.get("subtitle", doc["hub_role"])
    f_t, f_s, f_b = _font(FONT_BOLD, 40), _font(FONT_REG, 25), _font(FONT_BOLD, 22)
    w = int(max(d.textlength(title, font=f_t), d.textlength(sub, font=f_s))) + 120
    _panel(d, (48, 44, 48 + w, 156))
    d.rectangle((48, 44, 56, 156), fill=GI_GOLD)
    d.text((84, 62), title, font=f_t, fill=INK)
    d.text((86, 112), sub, font=f_s, fill=DIM)
    if not live:
        # Top right, deliberately: the bottom of the frame belongs to the
        # captions and the avatar, and a warning that overlaps either is a
        # warning people learn to read past.
        band = "PROTOTYPE · MOCK AVATAR · NO HEYGEN CALL WAS MADE"
        bw = int(d.textlength(band, font=f_b)) + 44
        _panel(d, (CANVAS[0] - 48 - bw, 44, CANVAS[0] - 48, 96),
               radius=10, fill=(90, 20, 20, 214), outline=(255, 92, 92, 255))
        d.text((CANVAS[0] - 26 - bw, 56), band, font=f_b, fill=(255, 214, 214, 255))
    img.save(dest)
    return dest


def caption_png(text: str, x0: int, wrap: int, dest: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(FONT_REG, 34)
    # ⚠️ NOT `[:3]`. Truncating here silently dropped the last four words of a
    # narration line the avatar still said out loud — a caption that disagrees
    # with the voice is worse than no caption. The panel grows instead, and a
    # line that needs more than four is a line to shorten in the YAML.
    lines = textwrap.wrap(text, width=wrap)
    if len(lines) > 4:
        print(f"  ⚠️  caption wraps to {len(lines)} lines — shorten it: {text[:60]}…")
    lh, pad = 46, 26
    w = int(max(d.textlength(ln, font=f) for ln in lines)) + pad * 2
    h = lh * len(lines) + pad * 2 - 8
    y0 = CANVAS[1] - 72 - h
    _panel(d, (x0, y0, x0 + w, y0 + h))
    for i, ln in enumerate(lines):
        d.text((x0 + pad, y0 + pad - 6 + i * lh), ln, font=f, fill=INK)
    img.save(dest)
    return dest


# ══════════════════════════════════════════════════════════════════════════
# 6. composite
# ══════════════════════════════════════════════════════════════════════════
def composite(screencast: pathlib.Path, avatar: pathlib.Path, alpha: bool,
              mask: pathlib.Path, watermark: pathlib.Path,
              captions: list[tuple[pathlib.Path, float, float]],
              corner: str, total_s: float, dest: pathlib.Path) -> None:
    """
    One graph, two entry points for the avatar:

      · `alpha=True`  — the clip carries its own alpha (a real HeyGen
        transparent render, or a build of ffmpeg that can encode yuva420p).
      · `alpha=False` — the clip is opaque and a greyscale mask supplies the
        alpha through `alphamerge`. This is ALSO the path a green-screen
        delivery takes (`colorkey` → `alphamerge`), so it is not a downgrade —
        it is the branch most real footage will use.
    """
    args = ["-i", str(screencast), "-i", str(avatar), "-i", str(watermark),
            "-i", str(mask)]
    for png, _, _ in captions:
        args += ["-i", str(png)]

    chain = [
        f"[0:v]scale={CANVAS[0]}:{CANVAS[1]}:flags=lanczos,fps={FPS},format=rgba[bg]",
    ]
    if alpha:
        chain.append(f"[1:v]scale={AVATAR_PX}:{AVATAR_PX},format=rgba[av]")
    else:
        chain += [
            f"[1:v]scale={AVATAR_PX}:{AVATAR_PX},format=rgba[avc]",
            f"[3:v]scale={AVATAR_PX}:{AVATAR_PX},format=gray[avm]",
            "[avc][avm]alphamerge[av]",
        ]
    chain += [
        f"[bg][av]overlay={CORNERS[corner][0]}:{CORNERS[corner][1]}:format=auto[v0]",
        "[v0][2:v]overlay=0:0[v1]",
    ]
    prev = "v1"
    for n, (_, t0, t1) in enumerate(captions):
        nxt = f"c{n}"
        # ⚠️ commas inside a filter option must be escaped — the filtergraph
        # parser reads a bare comma as "next filter in the chain".
        chain.append(f"[{prev}][{n + 4}:v]overlay=0:0:"
                     f"enable=between(t\\,{t0:.3f}\\,{t1:.3f})[{nxt}]")
        prev = nxt
    chain.append(f"[{prev}]format=yuv420p[v]")

    ffmpeg(args + [
        "-filter_complex", ";".join(chain),
        "-map", "[v]", "-map", "1:a",
        "-t", f"{total_s:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(dest),
    ])


# ══════════════════════════════════════════════════════════════════════════
# 7. main
# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", type=pathlib.Path, default=DEFAULT_SCRIPT)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--skip-record", action="store_true",
                    help="re-composite from the last screencast (no browser)")
    ap.add_argument("--reuse-stack", action="store_true",
                    help="attach to an already-running gihub_e2e_pw stack")
    ap.add_argument("--voice", default=None, help="macOS `say` voice for the mock VO")
    ap.add_argument("--live", action="store_true",
                    help="actually call HeyGen (needs HEYGEN_API_KEY; UNVERIFIED)")
    a = ap.parse_args()

    doc = load_script(a.script)
    work = a.out / doc["tutorial_id"]
    work.mkdir(parents=True, exist_ok=True)
    print(f"\n═══ {doc['title']} · {doc['hub_role']} · {doc['language']} ═══")
    print(f"    script {a.script.relative_to(ROOT)}")
    print(f"    work   {work.relative_to(ROOT)}")

    # ── 1. the boundary, FIRST ───────────────────────────────────────────
    # Before a browser opens, before a frame exists. Nothing downstream can
    # widen what crosses, because nothing downstream has been built yet.
    print("\n[1/5] HeyGen payload — the only thing that would leave this machine")
    payload, allowed = heygen_payload(doc)
    try:
        assert_text_only(payload, allowed)
    except EgressRefused as e:
        sys.exit(f"\n🛑 EGRESS REFUSED (P12-1)\n   {e}")
    print(f"      ✅ egress guard: every free-text field traces to a reviewed "
          f"line in {a.script.name} ({len(allowed)} whitelisted strings)")
    print(f"      ── the exact JSON that would be POSTed to {HEYGEN_URL} ──")
    for ln in json.dumps(payload, indent=2, ensure_ascii=False).splitlines():
        print("      " + ln)

    key = os.environ.get("HEYGEN_API_KEY", "").strip()
    if a.live and not key:
        sys.exit("FATAL: --live needs HEYGEN_API_KEY in the environment")
    live = bool(a.live and key)
    if live:
        print("\n      ⚠️  --live: calling HeyGen for real (UNVERIFIED PATH)")
        print(json.dumps(heygen_live(payload, key), indent=2))
        sys.exit("STOP: the poll-and-download half is not implemented — see "
                 "PROPOSED_PHASE12_PLAN.md §4.4")
    print("\n      ⛔ NOT SENT — no HEYGEN_API_KEY and no --live. "
          "Synthesising the avatar locally instead.")

    # ── 2. PASS A: narration, measured ───────────────────────────────────
    print("\n[2/5] pass A — rendering the narration so the UI can be cut to it")
    clips = synthesize(doc, work, a.voice)
    holds = holds_from(clips)
    spoken = sum(c["dur"] for c in clips)
    print(f"      {len(clips)} lines, {spoken:.1f}s of speech "
          f"({'measured' if clips[0]['wav'] else 'ESTIMATED — no `say` here'})")
    for c in clips:
        print(f"        {c['beat']:<16} {c['dur']:5.2f}s → hold "
              f"{holds[c['beat']] / 1000:5.2f}s")

    # The assistant's "Thinking…" pause is not a cosmetic delay — it is the
    # window its own narration line has to play in, so it is DERIVED from the
    # audio rather than guessed in the YAML.
    think_ms = max(int(doc.get("assistant_think_ms", 1400)),
                   holds.get("thinking", 0))
    shot = shot_list(doc, holds, think_ms)

    # ── 3. PASS B: the screencast ────────────────────────────────────────
    if a.skip_record:
        beats = json.loads((work / "beats.json").read_text(encoding="utf-8"))
        screencast = pathlib.Path(beats["video"])
        print(f"\n[3/5] --skip-record: reusing {screencast.name}")
    else:
        print("\n[3/5] pass B — recording against the isolated stack "
              "(gihub_e2e_pw, :8010/:5183)")
        screencast, beats = record(shot, work, a.reuse_stack)

    total_s = probe_seconds(screencast)
    last_beat = max(b["t_ms"] for b in beats["beats"]) / 1000.0
    print(f"      {screencast.name}: {hms(total_s)}, "
          f"{len(beats['beats'])} beats, last at {last_beat:.2f}s")
    if last_beat > total_s + 0.5:
        # The alignment assumption, asserted rather than trusted. A beat past
        # the end of the video means t0 and the recording start disagree, and
        # every narration line is then misplaced by the same amount.
        sys.exit(f"FATAL: last beat {last_beat:.2f}s is past the video's "
                 f"{total_s:.2f}s — beat t0 and the recording start disagree")

    # ── 4. avatar + overlays ─────────────────────────────────────────────
    print("\n[4/5] placing the narration and building the stand-in avatar")
    narration, placed = place_narration(clips, beats["beats"], total_s, work)
    avatar, alpha = build_avatar_clip(doc, narration, total_s, work)
    print(f"      avatar: {avatar.name} "
          f"({'VP9 with alpha' if alpha else 'opaque H.264 + alphamerge mask'})")

    ov = work / "overlays"
    ov.mkdir(exist_ok=True)
    mask = avatar_mask(ov / "avatar_mask.png")
    wm = watermark_png(doc, live, ov / "watermark.png")
    corner = (doc.get("avatar") or {}).get("corner", "bottom-left")
    if corner not in CORNERS:
        sys.exit(f"FATAL: avatar.corner must be one of {', '.join(CORNERS)}")
    # Captions start clear of the avatar when it shares the bottom edge.
    cap_x = 56 + AVATAR_PX + 40 if corner == "bottom-left" else 64
    cap_wrap = 52 if corner == "bottom-left" else 64
    captions: list[tuple[pathlib.Path, float, float]] = []
    for i, c in enumerate(placed):
        end_t = placed[i + 1]["start"] if i + 1 < len(placed) else total_s
        captions.append((caption_png(c["text"], cap_x, cap_wrap, ov / f"cap{i:02d}.png"),
                         c["start"], max(c["start"] + 0.6, end_t - 0.15)))
    print(f"      {len(captions)} beat-aligned captions + 1 watermark, avatar "
          f"{corner} (Pillow — this ffmpeg has no drawtext)")

    # ── 5. composite ─────────────────────────────────────────────────────
    print("\n[5/5] compositing")
    final = a.out / f"{doc['tutorial_id']}.mp4"
    t0 = time.time()
    composite(screencast, avatar, alpha, mask, wm, captions, corner,
              total_s, final)
    print(f"\n✅ {final.relative_to(ROOT)}")
    print(f"   {hms(probe_seconds(final))} · {final.stat().st_size / 1e6:.1f} MB · "
          f"{CANVAS[0]}x{CANVAS[1]} · encoded in {time.time() - t0:.1f}s")

    print("\n   To publish it into the training hub Phase 10 already built:")
    print(f"     POST /training/assets  {{\"module_key\": "
          f"\"{doc.get('training_module_key', '?')}\", \"language\": "
          f"\"{doc['language']}\", \"storage_uri\": \"<object-store URL>\", "
          f"\"duration_s\": {int(total_s)}}}")
    print("   (admin-only; the player says \"not published yet\" until a row exists)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
