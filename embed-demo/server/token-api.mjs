/**
 * Token backend: browser POSTs JSON here (no API key); we forward to Superset with X-API-Key.
 */
import cors from "cors";
import dotenv from "dotenv";
import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..");
dotenv.config({ path: path.join(root, ".env.local") });
dotenv.config({ path: path.join(root, ".env") });

const app = express();
const PORT = Number(process.env.TOKEN_API_PORT || 3001);

/** Comma-separated bases, tried in order (WSL + Docker: try host.docker.internal first). */
function supersetBases() {
  const raw =
    process.env.SUPERSET_URL ||
    process.env.VITE_SUPERSET_URL ||
    "http://127.0.0.1:9196";
  return raw
    .split(",")
    .map((s) => s.trim().replace(/\/$/, ""))
    .filter(Boolean);
}

const BASES = supersetBases();
const API_KEY = (process.env.SUPERSET_EMBED_API_KEY || "").trim();

function formatFetchError(err) {
  if (!(err instanceof Error)) return String(err);
  const parts = [err.message];
  if (err.cause !== undefined) {
    parts.push(String(err.cause));
  }
  return parts.join(" | ");
}

function isLikelyNetworkFailure(err) {
  const msg = formatFetchError(err);
  return /ECONNREFUSED|ENOTFOUND|ETIMEDOUT|EAI_AGAIN/i.test(msg);
}

app.use(cors({ origin: true }));
app.use(express.json({ limit: "1mb" }));

app.use((req, _res, next) => {
  console.log(`[token-api] ${req.method} ${req.url}`);
  next();
});

app.get("/health", (_req, res) => {
  res.json({
    ok: true,
    service: "embed-demo-token-api",
    supersetBases: BASES,
  });
});

app.get("/health/superset", async (_req, res) => {
  const errors = [];
  for (const base of BASES) {
    try {
      const r = await fetch(`${base}/login/`, {
        method: "GET",
        redirect: "manual",
        signal: AbortSignal.timeout(8000),
      });
      return res.json({
        ok: true,
        usedBase: base,
        supersetBases: BASES,
        probeStatus: r.status,
        hint: "Superset responded over TCP (port reachable from this Node process).",
      });
    } catch (e) {
      errors.push({ base, error: formatFetchError(e) });
    }
  }
  res.status(503).json({
    ok: false,
    supersetBases: BASES,
    tried: errors,
    hint:
      "None of SUPERSET_URL bases worked. Use comma-separated URLs, e.g. http://host.docker.internal:9196,http://127.0.0.1:9196 — or run the token API from Windows (not WSL) with SUPERSET_URL=http://127.0.0.1:9196 while Docker publishes 9196 on Windows.",
  });
});

async function forwardGuestToken(req, res) {
  if (!API_KEY) {
    res.status(500).json({
      error:
        "SUPERSET_EMBED_API_KEY missing in embed-demo/.env.local (same value as Superset).",
    });
    return;
  }

  const bodyJson = JSON.stringify(req.body ?? {});
  const attempts = [];

  for (let i = 0; i < BASES.length; i++) {
    const base = BASES[i];
    const targetUrl = `${base}/api/guest-token/generate`;
    try {
      const upstream = await fetch(targetUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": API_KEY,
        },
        body: bodyJson,
        signal: AbortSignal.timeout(60000),
      });
      const text = await upstream.text();
      const ct =
        upstream.headers.get("content-type") || "application/json; charset=utf-8";
      res.status(upstream.status).setHeader("Content-Type", ct).send(text);
      return;
    } catch (e) {
      attempts.push({ url: targetUrl, error: formatFetchError(e) });
      const canRetry =
        isLikelyNetworkFailure(e) && i < BASES.length - 1;
      if (canRetry) {
        console.warn(`[token-api] retry next base after`, targetUrl, e.message);
        continue;
      }
      console.error("[token-api] upstream error →", targetUrl, e);
      res.status(502).json({
        error: "Failed to reach Superset (network error before HTTP response)",
        detail: formatFetchError(e),
        triedUrl: targetUrl,
        attempts,
        supersetBases: BASES,
        hint:
          "Set SUPERSET_URL to comma-separated URLs, e.g. http://host.docker.internal:9196,http://127.0.0.1:9196. Or run the token API on Windows (not WSL) with SUPERSET_URL=http://127.0.0.1:9196.",
      });
      return;
    }
  }
}

app.post("/guest-token", forwardGuestToken);
app.post("/api/guest-token", forwardGuestToken);

app.listen(PORT, () => {
  console.log(
    `[token-api] http://127.0.0.1:${PORT} — POST /guest-token — GET /health — GET /health/superset`
  );
  console.log(`[token-api] Superset bases (in order): ${BASES.join(" | ")}`);
});
