/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPERSET_URL: string;
  readonly VITE_EMBEDDED_DASHBOARD_ID: string;
  /** Base URL of the token API (server/token-api.mjs), default http://localhost:3001 */
  readonly VITE_TOKEN_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
