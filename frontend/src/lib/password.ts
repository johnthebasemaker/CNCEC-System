/**
 * frontend/src/lib/password.ts — the password policy, mirrored for the UI.
 *
 * ⚠️ THIS IS A COURTESY, NOT THE BOUNDARY. `admin.assert_password_ok` is what
 * actually enforces the policy, on every credential-setting path, server-side.
 * This exists so somebody typing a password is told the requirement while
 * they type it instead of after a round trip — and it must be kept in step
 * with `admin.MIN_PW` / `admin.password_problems`. If the two ever disagree,
 * the server wins and the user sees its 422; the failure mode is a confusing
 * message, never a weak password.
 *
 * Operator ruling 2026-08-11: 8 characters with complexity, down from 12
 * plain. Both halves matter — the length came DOWN, so the complexity is
 * what keeps the policy meaningful.
 */

/** Minimum length. Mirrors backend `admin.MIN_PW`. */
export const MIN_PW = 8

// Mirrors backend `admin._PW_SPECIALS`.
const SPECIALS = "!@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~"

/**
 * Everything wrong with this password, as clause fragments. Empty = fine.
 *
 * Returns ALL failures rather than the first, so a person is told the whole
 * requirement once instead of discovering it one rejection at a time. The
 * caller joins them: "Password must " + problems.join('; ') + "."
 */
export function passwordProblems(pw: string): string[] {
  const problems: string[] = []
  const v = pw ?? ''
  if (v.length < MIN_PW) problems.push(`be at least ${MIN_PW} characters (yours is ${v.length})`)
  if (!/[A-Z]/.test(v)) problems.push('contain an uppercase letter')
  if (!/[0-9]/.test(v)) problems.push('contain a number')
  if (![...v].some((c) => SPECIALS.includes(c))) {
    problems.push('contain a special character, e.g. ! @ # $ % &')
  }
  return problems
}

/** True when the password satisfies the policy. */
export const passwordOk = (pw: string): boolean => passwordProblems(pw).length === 0
