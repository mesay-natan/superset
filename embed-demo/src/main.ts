import { embedDashboard } from "@superset-ui/embedded-sdk";
import "./style.css";

const mount = document.querySelector<HTMLElement>("#embed-root");
const statusEl = document.querySelector<HTMLElement>("#status");

function setStatus(text: string) {
  if (statusEl) statusEl.textContent = text;
}

const supersetDomain = (
  import.meta.env.VITE_SUPERSET_URL || "http://localhost:9196"
).replace(/\/$/, "");
const embeddedId = (import.meta.env.VITE_EMBEDDED_DASHBOARD_ID || "").trim();

/** Your backend base URL (token API). Must not expose SUPERSET_EMBED_API_KEY to the browser. */
const tokenApiBase = (
  import.meta.env.VITE_TOKEN_API_URL || "http://localhost:3001"
).replace(/\/$/, "");

async function requestGuestTokenFromBackend(): Promise<string> {
  const res = await fetch(`${tokenApiBase}/guest-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      resources: [{ type: "dashboard", id: embeddedId }],
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Guest token failed (${res.status}): ${text}`);
  }
  const data = (await res.json()) as { guest_token?: string };
  const token = data.guest_token;
  if (!token) {
    throw new Error("Response missing guest_token");
  }
  return token;
}

async function main() {
  if (!mount) {
    setStatus("Missing #embed-root container.");
    return;
  }
  if (!embeddedId) {
    setStatus(
      "Set VITE_EMBEDDED_DASHBOARD_ID in .env.local (embedded dashboard UUID from Superset → Embed dashboard)."
    );
    return;
  }

  let guestToken: string;
  try {
    setStatus("Requesting guest token from backend…");
    guestToken = await requestGuestTokenFromBackend();
  } catch (e) {
    setStatus(e instanceof Error ? e.message : String(e));
    return;
  }

  setStatus("Loading embedded dashboard…");
  try {
    await embedDashboard({
      id: embeddedId,
      supersetDomain,
      mountPoint: mount,
      fetchGuestToken: () => Promise.resolve(guestToken),
      dashboardUiConfig: { hideTitle: false },
    });
    setStatus("");
  } catch (e) {
    setStatus(e instanceof Error ? e.message : String(e));
  }
}

void main();
