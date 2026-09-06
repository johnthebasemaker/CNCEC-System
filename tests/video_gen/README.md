# `tests/video_gen/` — the tutorial recorder (Phase 12 prototype)

Records a role tutorial as a screencast plus a `beats.json` timeline.
`scripts/generate_tutorial.py` is the only thing that should invoke it.

```bash
.venv/bin/python scripts/generate_tutorial.py
```

## What this is not

**It is not a test and it must never become a gate.** It lives under its own
Playwright config precisely so `cd tests/e2e && npm test` cannot pick it up: a
tutorial that renders badly is a tutorial to re-render, not a red build. It
asserts only enough to fail loudly on a recording that would be *silently*
wrong — a panel that never opened, an answer that never rendered.

## What it reuses, and why

| Reused from `tests/e2e/` | Why not a copy |
|---|---|
| `global-setup` / `global-teardown` | A recording must run against the throwaway `gihub_e2e_pw` on :8010/:5183, never a live database (**rule 15**). A second stack builder is a second place for that rule to be got wrong. |
| `setup/auth.setup.ts` | One login fixture. A recorder with its own would drift from the suite's. |
| `harness/env.ts` | Ports, users, storage-state paths — one source. |
| `node_modules` | Symlinked by the orchestrator. Same Playwright build as the gate, so a recording cannot be made in a different browser than the one the suite tests with. No second 300 MB install. |

## ⚠️ Do not run this and the E2E suite at once

They own the same database and the same two ports. The loser fails looking like
a flaky spec. `--reuse-stack` (`GI_VIDEO_REUSE_STACK=1`) exists for batches:
raise the stack once, record many, tear down once.

## Files

| File | Job |
|---|---|
| `playwright.config.ts` | One worker, serial, long timeouts, no retries. |
| `stack.ts` / `stack-teardown.ts` | Wrap the E2E lifecycle; honour `GI_VIDEO_REUSE_STACK`. |
| `harness/record.ts` | Beats, the synthetic cursor, the redaction hook, the scripted AI lane, the recording context. |
| `sample_tutorial.spec.ts` | The one tutorial the prototype ships: Store Keeper → Hub Assistant. |

## Three things in `harness/record.ts` that are load-bearing

1. **The context is created by hand**, not by `test.use({ video })`. Playwright
   starts recording when the *context* is created; through the fixture that
   happens before the test body and the spec never learns when. Beat alignment
   needs `t0` to be a number we hold.

2. **`recordVideo.size` does not scale the page.** Measured: a 1536×864
   viewport in a 1920×1080 canvas is drawn 1:1 at the top-left and leaves black
   bands over exactly 20% of each axis. `deviceScaleFactor` does not change it.
   The two are pinned equal here; the upscale to the delivery canvas happens
   once, in ffmpeg.

3. **The holds come from the audio, not from the spec.** A Playwright-driven UI
   is far faster than a person explaining it. Recorded with guessed pauses this
   tutorial overran all six beats — 40.2 s of speech over 19.6 s of video. The
   orchestrator renders the narration first, measures it, and passes per-beat
   hold times in the shot list.
