# Native Apps — build & distribution guide

GI Hub ships as **one web codebase** (`frontend/`) wrapped three ways:

| Target | Wrapper | Output | Status |
|---|---|---|---|
| Android | Capacitor | `.apk` / `.aab` | scaffolded — build locally (below) |
| iOS | Capacitor | `.ipa` (Xcode) | scaffolded — build locally (below) |
| Windows / macOS | Tauri v2 | `.exe` / `.dmg` | **scaffolded** — `src-tauri/` is committed (tauri init done 2026-07-25); CI builds it on tag pushes. A LOCAL build still needs the Rust toolchain, and no Tauri build has run on this Mac yet |
| Any browser / phone | PWA | installable, offline | LIVE today (USER_MANUAL §1.2) |

> **Automated releases:** pushing a version tag (`git tag v0.1.0 && git push origin v0.1.0`)
> builds ALL of the above on GitHub's runners and attaches the `.dmg`, `.exe`,
> `.msi` and `.apk` to that tag's GitHub Release — see §6.

Everything below runs from `frontend/` unless noted.

## 1. Android (.apk)

Prerequisites (one-time): [Android Studio](https://developer.android.com/studio)
with an SDK + JDK 21 (Studio bundles both — Capacitor 8 compiles against
Java 21; a JDK 17 build dies with "invalid source release: 21").

```bash
npm install                    # brings @capacitor/{core,cli,android,ios}
npx cap add android            # one-time: generates the android/ project (gitignored)
npm run cap:sync               # vite build + copy dist/ into the native shell
cd android && ./gradlew assembleDebug         # debug APK, installable immediately
# → android/app/build/outputs/apk/debug/app-debug.apk
```

Signed release for distribution: `./gradlew assembleRelease` after configuring
a keystore in `android/app/build.gradle` (Android Studio → Build → Generate
Signed App Bundle walks you through it).

## 2. iOS

Prerequisites: Xcode + an Apple Developer account (free account = 7-day
sideload; paid = TestFlight/App Store).

```bash
npx cap add ios                # one-time: generates the ios/ project (gitignored)
npm run cap:ios                # build + sync + open in Xcode → Product ▸ Archive
```

## 3. Windows (.exe) / macOS (.dmg) — Tauri v2 foundation

Tauri compiles a tiny Rust shell around the same `dist/` build — outputs are
~10 MB installers. `src-tauri/` is **already initialized and committed**
(`@tauri-apps/cli` is a devDependency; config: `src-tauri/tauri.conf.json`).
The normal path is the automated release pipeline (§6); to build locally:

```bash
# one-time: the Rust toolchain (NOT installed on the dev Mac yet)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# every build
npx tauri build
# → src-tauri/target/release/bundle/ (nsis .exe + .msi on Windows, dmg/app on macOS)
```

Windows installers must be built ON Windows, dmg ON macOS — Tauri does not
cross-compile. (`target/` is gitignored.)

## 4. OTA updates — how deployed changes reach users

- **PWA + browser users:** the service worker is built with
  `registerType: 'autoUpdate'` and `src/main.tsx` polls for a new deployment
  every 15 minutes **and** on every tab refocus — users get the new version
  automatically, no manual refresh required.
- **Capacitor apps (bundled mode, default):** the shell carries its own copy
  of `dist/` — rebuild + reinstall to update.
- **Capacitor apps (live-shell mode):** uncomment the `server.url` block in
  `frontend/capacitor.config.ts` to make the native app load the hosted
  portal (`https://gi.giinventory.com`) instead of the bundled copy — then
  the PWA auto-update path above applies to the native apps too, and no
  reinstall is ever needed. Recommended once the Hetzner deployment is live.

## 5. Hosting the installers

After the Hetzner deployment, drop the built artifacts in the repo-root
`downloads/` directory the API serves (or any static path behind the tunnel)
and link them from USER_MANUAL §1.2 — the manual already carries the
placeholder links (`/downloads/gi-hub.apk`, `/downloads/GI-Hub-Setup.exe`,
`/downloads/GI-Hub.dmg`).

## 6. Automated release pipeline (GitHub Actions)

Two workflows (adapted from the Bible-project reference pipeline) build and
publish the installers as **GitHub Release assets**:

| Workflow | Runners | Builds | Attaches |
|---|---|---|---|
| `.github/workflows/release-desktop.yml` | macos-14 + windows-latest | Tauri | `.dmg`, NSIS `.exe`, `.msi` |
| `.github/workflows/release-android.yml` | ubuntu-latest (JDK 21 — Capacitor 8 requires it; SDK preinstalled, `cap add android` regenerates the gitignored project) | Capacitor/Gradle | debug-signed `.apk` (sideload-ready) |

**To cut a release:**

```bash
git tag v0.1.0
git push origin v0.1.0
```

Both workflows fire, build in parallel (~10–20 min), and attach every
installer to the `v0.1.0` Release under Assets (auto-generated notes). A
`workflow_dispatch` run from the Actions tab builds WITHOUT publishing —
artifacts stay downloadable from the run page for 30 days (same shape as the
reference pipeline; we deliberately dropped its build-on-every-main-push
trigger — dual-CI guards main, and paid macOS/Windows minutes are saved for
tags). Neither workflow overlaps `Postgres dual-CI` (branch/PR-scoped paths)
or the manual Hetzner deploy workflows.

**Production API routing:** both workflows inject
`VITE_API_URL=https://gi.giinventory.com/api` into the web build, so the
standalone binaries call the hosted backend directly (Sync, AI, WhatsApp all
work). Web builds leave it unset and keep the relative `/api` prefix
(`frontend/src/api/client.ts` → `API_BASE`). The backend already allows the
native shell origins (`tauri://localhost`, `capacitor://localhost`, … — see
`CORS_ORIGINS` in `backend/api/config.py`) and, in production, issues the
refresh cookie with `SameSite=None; Secure` so silent session refresh works
cross-origin too.

**Cloudflare Access and the native apps (READ THIS if login says "Server
unreachable" in the app while the web portal works).** The domain sits
behind Cloudflare Access (Zero Trust). A browser passes Access once via its
SSO cookie — but the native shells make bare cross-origin XHR calls with
**no Access session**, so Access answers `/api/...` with a 302 to its login
page; the webview kills that cross-origin redirect and axios reports a
plain network error → the app shows "Server unreachable" even though the
server is fine. CSP/CORS are already configured (below); the remaining
gate is an **Access policy change in the Cloudflare Zero Trust dashboard**
(cannot be done from this repo):

1. Zero Trust → Access → Applications → add an application for
   `gi.giinventory.com/api/*` (more specific paths win over the site-wide app).
2. Give it a **Bypass** policy (Everyone). This is safe: every `/api` route
   enforces its own JWT auth, role gates and rate limits — Access was only
   ever a second wall for the *HTML portal*, which stays protected.
   (Alternative: a **Service Auth** policy + Access service token, but that
   would mean shipping the token inside the installers — don't.)
3. The in-app diagnostics confirm the state: the browser console of a
   blocked native app prints `Possible Cloudflare Access block detected…`
   (client.ts logs the exact status/headers of every network-level failure,
   403 and 5xx).

**Tauri CSP:** `src-tauri/tauri.conf.json` ships an explicit CSP whose
`connect-src` allows `https://gi.giinventory.com` plus the local dev
backends (`http://127.0.0.1:8000`, `http://localhost:8000`). If the API
domain ever changes, update BOTH the workflow `VITE_API_URL` and this
`connect-src` list, or the desktop app will block its own API calls.
`devCsp` stays wide-open for `tauri dev` (HMR websockets). Webview
`fetch`/XHR needs no Tauri capability entries — those only gate the
`@tauri-apps/api` plugins, which we don't use for networking.

Not yet wired (future): macOS notarization, a Play-Store release keystore
(`assembleRelease`), and auto-linking the newest release into USER_MANUAL
§1.2's download URLs.

## 7. First-launch warnings — unsigned builds

None of the CI artifacts are code-signed yet, so every OS shows a one-time
scare prompt. All three are expected; exact end-user wording lives in
USER_MANUAL §1.2.

| OS | What it says | Bypass |
|---|---|---|
| macOS | **"GI Hub is damaged and can't be opened"** — Gatekeeper quarantines any unsigned internet download; the dmg is fine | 3 steps (below) — quarantine strip alone is NOT enough on Apple Silicon |
| Windows | SmartScreen: "Windows protected your PC" | **More info → Run anyway** |
| Android | "Blocked by Play Protect" / unknown-source install | Allow from this source → **Install anyway** |

**macOS — the full 3-step fix (verified on Apple Silicon 2026-07-25).**
`xattr -cr` alone works on Intel Macs, but M-series hardware refuses to
execute completely unsigned code — the app dies on launch until it carries
at least an *ad-hoc* signature:

```bash
# 1. Drag "GI Hub.app" from the .dmg into /Applications, then:
sudo xattr -cr "/Applications/GI Hub.app"          # 2. strip quarantine
codesign --force --deep --sign - "/Applications/GI Hub.app"   # 3. ad-hoc sign (M-series: mandatory)
```

These go away permanently with signing: Apple Developer ID + notarization
(macOS), an Authenticode certificate (Windows), and a release keystore /
Play-Store listing (Android) — all on the future list above. The desktop
binaries being ~10 MB is Tauri working as designed (native OS webview, no
bundled Chromium); the app is complete and keeps the PWA offline queue.

## 8. Sanity gates

The wrappers never fork the web code: `npm run build && npx tsc --noEmit`
stays the frontend gate, and `tools/diagnose_sync.py` (docs/DEBUGGING.md)
verifies the deployed sync chain the apps rely on.
