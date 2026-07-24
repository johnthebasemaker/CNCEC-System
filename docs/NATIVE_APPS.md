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
with an SDK + JDK 17 (Studio bundles both).

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
| `.github/workflows/release-android.yml` | ubuntu-latest (JDK 17; SDK preinstalled, `cap add android` regenerates the gitignored project) | Capacitor/Gradle | debug-signed `.apk` (sideload-ready) |

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

Not yet wired (future): macOS notarization, a Play-Store release keystore
(`assembleRelease`), and auto-linking the newest release into USER_MANUAL
§1.2's download URLs.

## 7. Sanity gates

The wrappers never fork the web code: `npm run build && npx tsc --noEmit`
stays the frontend gate, and `tools/diagnose_sync.py` (docs/DEBUGGING.md)
verifies the deployed sync chain the apps rely on.
