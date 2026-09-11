/** Base URL for the API, resolved when the app loads.
 *
 * In order:
 *
 * 1. `window.__FORGE_API__`, set by /config.js. That file is generated at build
 *    time from the FORGE_API_URL environment variable (scripts/write-config.mjs),
 *    so a Vercel deployment is pointed at its API through a Vercel setting and a
 *    redeploy, never a source edit.
 * 2. The local uvicorn, when the page itself is served from localhost.
 * 3. Same-origin /api, for a host that serves the SPA and the API together.
 *
 * In production the API is on a different origin (Render) from the SPA
 * (Vercel). That is deliberate: proxying through Vercel would put a serverless
 * hop in front of the SSE progress stream, and buffering there is exactly what
 * a sixty-second job cannot afford. The cost is CORS, which the API allows via
 * FORGE_CORS_ORIGINS. EventSource cannot send an Authorization header, which is
 * why the progress stream authenticates with a short-lived query-string ticket.
 */

const configured = (globalThis as { __FORGE_API__?: string }).__FORGE_API__;

const isLocal =
  location.hostname === 'localhost' || location.hostname === '127.0.0.1';

export const API =
  configured ?? (isLocal ? 'http://localhost:8000/api' : '/api');
