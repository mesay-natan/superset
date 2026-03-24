# Superset embed demo

Minimal app using [`@superset-ui/embedded-sdk`](https://github.com/apache/superset/tree/master/superset-embedded-sdk) with the flow:

1. **Browser → token backend** — `POST /guest-token` on the token API (e.g. `http://localhost:3001/guest-token`) with `resources` in the JSON body. No `X-API-Key` in the browser.
2. **Token backend → Superset** — `POST /api/guest-token/generate` with `X-API-Key` (from `embed-demo/.env.local` only on the server).
3. **Browser → Embedded SDK** — only after a `guest_token` is returned, `embedDashboard` runs with `fetchGuestToken: () => Promise.resolve(guestToken)`.

## One-time steps in Superset (UI)

1. Start Superset with this repo’s config (e.g. Docker) so embedding and guest tokens are enabled.
2. Open a dashboard → **⋯** → **Embed dashboard**, enable embedding, copy the **embedded UUID**.
3. Allow your app origin (e.g. `http://localhost:5173`) in **Allowed domains** / parent [`.env`](../.env) `CORS_ORIGINS`.

## Run (Vite + token API)

```bash
cd embed-demo
cp .env.example .env.local
```

Edit `.env.local`:

- `VITE_EMBEDDED_DASHBOARD_ID` — embedded UUID.
- `SUPERSET_EMBED_API_KEY` — same as on the Superset server (used **only** by `server/token-api.mjs`, not exposed to the client bundle).
- `VITE_TOKEN_API_URL` — if the token API is not `http://localhost:3001`.

```bash
npm install
npm run dev
```

This starts:

- **Vite** (usually `http://localhost:5173`) — static UI.
- **Token API** (`http://localhost:3001`) — forwards guest-token requests to Superset.

Open the Vite URL. You should see “Requesting guest token from backend…” then the embedded dashboard.

## Production

Replace `server/token-api.mjs` with your real backend: same contract (`POST` with `resources` body, forward to Superset with `X-API-Key`). Never expose `SUPERSET_EMBED_API_KEY` in the browser.

## Troubleshooting

- **`502` / `ECONNREFUSED` on `POST /guest-token`**: Node cannot connect to any Superset base. In `.env.local`, set **`SUPERSET_URL`** to a **comma-separated list** (tried in order), e.g. `http://host.docker.internal:9196,http://127.0.0.1:9196`. **`host.docker.internal`** is provided by Docker Desktop and often works from WSL. If all fail, run **`npm run dev` from Windows** (not WSL) with **`SUPERSET_URL=http://127.0.0.1:9196`**. Open **`http://localhost:3001/health/superset`** to probe each base. Keep **`VITE_SUPERSET_URL`** as `http://localhost:9196` for the browser/iframe when you use a Windows browser.
- **`404` on the token URL**: Wrong process on that port. Open `http://localhost:3001/health` — expect `embed-demo-token-api`. Run `npm run dev` from `embed-demo`.
- **`Guest token failed` (HTTP errors from Superset)**: Superset is reachable but rejected the request — check `SUPERSET_EMBED_API_KEY` and embedded dashboard id.
