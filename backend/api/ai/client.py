"""
backend/api/ai/client.py — async Ollama client (port of legacy ai/client.py).

Talks to a local Ollama server over HTTP with httpx (already a backend dep).
Awaiting Ollama costs the event loop nothing — the heavy compute lives in the
Ollama process, not here. A module-level semaphore caps CONCURRENT generations
so simultaneous users queue politely instead of thrashing the model host
(CPX42: one warm 7-8B model at a time — user ruling 2026-07-06).

Test seam: routes and manual_qa call these functions through the module
object (`from . import client as aic; aic.stream(...)`) so service_tests can
monkeypatch `health` / `list_models` / `stream` without a live Ollama.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator, Optional

import httpx

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Model registry — same canonical ids as legacy ai/client.py, env-overridable.
MODEL_CHAT = os.environ.get("GI_AI_CHAT_MODEL", "llama3.1:8b")
MODEL_CODER = os.environ.get("GI_AI_CODER_MODEL", "qwen2.5-coder:7b")
MODEL_VISION = os.environ.get("GI_AI_VISION_MODEL", "qwen2.5vl:7b")

HEALTH_TIMEOUT_S = 2.0
GEN_TIMEOUT_S = float(os.environ.get("GI_AI_TIMEOUT_S", "240"))  # 7B cold start
KEEP_ALIVE = "30m"  # hold the KV cache warm between calls (legacy behavior)

# At most N generations in flight; the rest wait (the /ai endpoints emit a
# "queued" SSE event while waiting so the UI never looks frozen).
GEN_SEMAPHORE = asyncio.Semaphore(int(os.environ.get("GI_AI_CONCURRENCY", "2")))


# ── ⚠️ THE CLOUD SEAM (Phase 9d, ruling Q7) ─────────────────────────────────
# Local `qwen2.5vl:7b` reads the consumption form today. Its weakest task is a
# handwritten DIGIT — 4/9, 1/7, 3/8 and a lost decimal point are the failure
# modes — and slice 9c's form is built to make that as easy as possible
# (pre-printed names, a QR instead of header text, one boxed figure per row).
# If UAT still shows it is not good enough, the escalation is NOT a bigger local
# model: one warm 6 GB VLM is the standing on-box ruling, and a second would
# either cold-start on every switch or sit resident beside it.
#
# The escalation is this one function. Set GI_AI_VISION_PROVIDER=anthropic and
# GI_AI_VISION_API_KEY, and vision calls route to a cloud VLM instead. Nothing
# else in the pipeline changes: `ocr_form.py` calls `vision_json()` and neither
# knows nor cares which answered. Deliberately a runtime switch and not a code
# branch to be written later — a seam that has never been exercised is a plan,
# not a seam.
VISION_PROVIDER = os.environ.get("GI_AI_VISION_PROVIDER", "ollama").lower()
VISION_API_KEY = os.environ.get("GI_AI_VISION_API_KEY", "")
VISION_CLOUD_MODEL = os.environ.get("GI_AI_VISION_CLOUD_MODEL",
                                    "claude-sonnet-4-5")
VISION_CLOUD_URL = os.environ.get("GI_AI_VISION_CLOUD_URL",
                                  "https://api.anthropic.com/v1/messages")


def vision_provider() -> str:
    """Which engine vision calls will actually reach.

    Reports `ollama` when a cloud provider is named but unconfigured, rather
    than failing at the first upload: a missing key is an operator mistake that
    should surface in /ai/health, not in somebody's photograph.
    """
    if VISION_PROVIDER == "anthropic" and VISION_API_KEY:
        return "anthropic"
    return "ollama"


async def vision_json(prompt: str, *, system: str, image_b64: str,
                      num_predict: int = 1400,
                      timeout_s: float = GEN_TIMEOUT_S) -> tuple[str, str]:
    """One vision completion. Returns `(raw_text, model_id)`.

    The model id comes back with the text because it is stored on the entry:
    "which engine read this" is the first question asked when a quantity is
    disputed, and it is unanswerable later if nobody recorded it.
    """
    if vision_provider() == "anthropic":
        body = {
            "model": VISION_CLOUD_MODEL,
            "max_tokens": num_predict,
            "system": system,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/jpeg",
                                             "data": image_b64}},
                {"type": "text", "text": prompt}]}],
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as c:
                r = await c.post(VISION_CLOUD_URL, json=body, headers={
                    "x-api-key": VISION_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"})
                r.raise_for_status()
                parts = r.json().get("content", [])
                text = "".join(p.get("text", "") for p in parts
                               if p.get("type") == "text")
                return text, VISION_CLOUD_MODEL
        except Exception as e:
            raise RuntimeError(
                f"Cloud vision failed: {type(e).__name__}: {e}") from e

    async with GEN_SEMAPHORE:
        text = await generate(MODEL_VISION, prompt, system=system,
                              temperature=0.0, num_predict=num_predict,
                              images=[image_b64], timeout_s=timeout_s)
    return text, MODEL_VISION


async def health() -> bool:
    """True when the Ollama server answers /api/tags within 2s."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def list_models() -> list[str]:
    """Installed model names ([] when unreachable — callers treat as unknown)."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S) as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _payload(model: str, prompt: str, *, system: Optional[str], temperature: float,
             num_predict: int, images: Optional[list[str]] = None) -> dict:
    body: dict = {
        "model": model, "prompt": prompt, "keep_alive": KEEP_ALIVE,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system:
        body["system"] = system
    if images:
        body["images"] = images  # base64, no data: prefix (Ollama contract)
    return body


async def generate(model: str, prompt: str, *, system: Optional[str] = None,
                   temperature: float = 0.2, num_predict: int = 512,
                   images: Optional[list[str]] = None,
                   timeout_s: float = GEN_TIMEOUT_S) -> str:
    """One blocking completion. Raises RuntimeError on transport failure."""
    body = _payload(model, prompt, system=system, temperature=temperature,
                    num_predict=num_predict, images=images)
    body["stream"] = False
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(f"{OLLAMA_HOST}/api/generate", json=body)
            r.raise_for_status()
            return r.json().get("response", "")
    except Exception as e:  # normalized like legacy — caller shows a friendly msg
        raise RuntimeError(f"Ollama generate failed: {type(e).__name__}: {e}") from e


async def stream(model: str, prompt: str, *, system: Optional[str] = None,
                 temperature: float = 0.2, num_predict: int = 512,
                 timeout_s: float = GEN_TIMEOUT_S) -> AsyncIterator[str]:
    """Yield response chunks as they arrive. Raises RuntimeError before the
    first chunk on connection failure; mid-stream errors end the stream
    quietly (legacy contract — never break a half-rendered answer)."""
    body = _payload(model, prompt, system=system, temperature=temperature,
                    num_predict=num_predict)
    body["stream"] = True
    try:
        client = httpx.AsyncClient(timeout=timeout_s)
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Ollama unreachable: {e}") from e
    try:
        async with client.stream("POST", f"{OLLAMA_HOST}/api/generate",
                                 json=body) as r:
            if r.status_code != 200:
                raise RuntimeError(f"Ollama returned HTTP {r.status_code}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    piece = json.loads(line)
                except ValueError:
                    continue
                chunk = piece.get("response", "")
                if chunk:
                    yield chunk
                if piece.get("done"):
                    break
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise RuntimeError(f"Ollama unreachable at {OLLAMA_HOST}: {e}") from e
    finally:
        await client.aclose()
