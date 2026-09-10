/** Base URL for the API.
 *
 * Vercel serves the built SPA as static files and the API lives on Render, so
 * in production these are two different origins. That is deliberate rather
 * than accidental: proxying through Vercel would put a serverless hop in front
 * of the SSE progress stream, and buffering there is exactly what you do not
 * want on a sixty-second job. Talking to Render directly keeps the stream
 * unbuffered; the cost is CORS, which the API allows via FORGE_CORS_ORIGINS.
 *
 * EventSource cannot send an Authorization header, which is why the progress
 * stream authenticates with a short-lived ticket in the query string instead.
 *
 * Set PRODUCTION_API to your Render URL once deployed. `__FORGE_API__` on
 * window overrides everything, which is useful for pointing a preview build at
 * a branch deploy without rebuilding.
 */

const PRODUCTION_API = 'https://resumeforge-api.onrender.com/api';

const isLocal =
  location.hostname === 'localhost' || location.hostname === '127.0.0.1';

export const API =
  (globalThis as { __FORGE_API__?: string }).__FORGE_API__ ??
  (isLocal ? 'http://localhost:8000/api' : PRODUCTION_API);
