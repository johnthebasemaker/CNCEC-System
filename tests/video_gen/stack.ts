/**
 * Stack lifecycle for the recorder — a thin wrapper over the E2E suite's own
 * global setup/teardown, so a tutorial is recorded against the SAME isolated
 * stack the gate runs against (rule 15: `gihub_e2e_pw`, :8010 / :5183, never
 * the developer's :8000 / :5173 and never `gihub`).
 *
 * ⚠️ The wrapper exists for one reason: `GI_VIDEO_REUSE_STACK=1`. Recording a
 * batch of tutorials pays `cutover_migrate.py --wipe` once, not once per video
 * — it is ~30 s of the ~50 s a single short tutorial costs end to end. The
 * orchestrator raises the stack for the batch and sets this for each render.
 *
 * ⚠️ AND THE HAZARD THAT COMES WITH IT: this drops and rebuilds `gihub_e2e_pw`.
 * Do not record while `cd tests/e2e && npm test` is running — they own the same
 * database and the same two ports, and the loser fails in a way that looks like
 * a flaky spec.
 */
import type { FullConfig } from '@playwright/test'

/**
 * ⚠️ The E2E lifecycle modules are TypeScript ESM compiled through Playwright's
 * own CJS loader, and the shape that comes back from `await import()` depends
 * on which side of that interop you land on: sometimes `mod.default` is the
 * function, sometimes it is the module record and the function is one level
 * further down. Reaching straight for `mod.default` gave
 * "TypeError: mod.default is not a function" — which reads as a broken setup
 * file rather than an interop wrapper, so unwrap it explicitly.
 */
type Lifecycle = (c: FullConfig) => Promise<void> | void

function unwrap(mod: unknown, name: string): Lifecycle {
  let fn: unknown = mod
  for (let i = 0; i < 3 && fn && typeof fn !== 'function'; i++) {
    fn = (fn as { default?: unknown }).default
  }
  if (typeof fn !== 'function') throw new Error(`${name} has no callable default export`)
  return fn as Lifecycle
}

export async function up(config: FullConfig): Promise<void> {
  if (process.env.GI_VIDEO_REUSE_STACK === '1') {
    console.log('[video] GI_VIDEO_REUSE_STACK=1 — attaching to the running stack')
    return
  }
  await unwrap(await import('../e2e/global-setup'), 'global-setup')(config)
}

export async function down(config: FullConfig): Promise<void> {
  if (process.env.GI_VIDEO_REUSE_STACK === '1') {
    console.log('[video] GI_VIDEO_REUSE_STACK=1 — leaving the stack up')
    return
  }
  await unwrap(await import('../e2e/global-teardown'), 'global-teardown')(config)
}

export default up
