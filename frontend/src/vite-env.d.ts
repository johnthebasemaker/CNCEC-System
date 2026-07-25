/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  /** Absolute API base for NATIVE builds (Tauri/Capacitor); unset on web,
   *  where the relative '/api' prefix is used. Set by release-*.yml. */
  readonly VITE_API_URL?: string
}
