# Deploying ResumeForge

Free tiers end to end: **Vercel** serves the Angular app, **Render** runs the API
with the tailoring worker inside it, **Neon** hosts Postgres, and **Groq** runs
the model. PDF download is optional and needs S3-compatible storage.

Budget about half an hour, most of it waiting for Render's first build.

```
Browser ──► Vercel   static Angular app
   │
   └──► Render      FastAPI + in-process worker + Tectonic ──► Neon  (Postgres)
                                                          └──► Groq  (model)
```

The frontend talks to Render directly rather than through Vercel, so the live
progress stream is never buffered by a proxy. The cost is one CORS setting,
which is step 6.

---

## Before you start

- **Rotate your Groq key** if it has ever been pasted anywhere outside
  `backend/.env` — a chat, an issue, a screenshot. Create a new one at
  [console.groq.com/keys](https://console.groq.com/keys) and delete the old one.
- `backend/.env` is gitignored and never leaves your machine. Production reads
  its settings from the Render dashboard instead.

## 1. Put the code on GitHub

Render and Vercel both deploy from a repository. If this one has no remote yet,
create an empty repository on GitHub, then:

```bash
git remote add origin https://github.com/<you>/resumeforge.git
git push -u origin master
```

## 2. Create the database — Neon

1. Sign up at [neon.tech](https://neon.tech) and create a project.
2. **Choose the region to match Render's.** Render's region is set in
   `render.yaml` and defaults to `oregon`, which pairs with Neon's
   *AWS US West 2 (Oregon)*. To run somewhere else, change `region:` in
   `render.yaml` and commit it *before* step 4 — Render cannot move a service
   after it is created.

   | `render.yaml` | Neon region |
   |---|---|
   | `oregon` | AWS US West 2 (Oregon) |
   | `ohio` | AWS US East 2 (Ohio) |
   | `virginia` | AWS US East 1 (N. Virginia) |
   | `frankfurt` | AWS Europe Central 1 (Frankfurt) |
   | `singapore` | AWS Asia Pacific 1 (Singapore) |

3. Open **Connect** and copy the connection string. Pooled or direct both work.

Paste it **exactly as Neon shows it**, `?sslmode=require&channel_binding=require`
and all. asyncpg rejects those two parameters, so the app rewrites them itself;
editing the string by hand is how it usually breaks.

You don't create any tables. Migrations create them when the API starts.

## 3. Get a model key — Groq

Create one at [console.groq.com/keys](https://console.groq.com/keys).

To use Anthropic instead, set `FORGE_LLM_PROVIDER` to `anthropic` in step 4 and
provide `FORGE_ANTHROPIC_API_KEY` in place of the Groq key.

## 4. Deploy the API — Render

1. At [dashboard.render.com](https://dashboard.render.com), choose
   **New → Blueprint** and connect your repository. Render reads `render.yaml`.
2. Fill in the values it asks for:

   | Variable | Value |
   |---|---|
   | `FORGE_DATABASE_URL` | The Neon connection string from step 2 |
   | `FORGE_GROQ_API_KEY` | The key from step 3 |
   | `FORGE_CORS_ORIGINS` | Leave blank for now — you get this URL in step 5 |
   | everything else | Leave blank |

   `FORGE_SECRET_KEY` is generated for you.

3. Apply. **The first build takes several minutes** — about six on a laptop,
   most of it downloading LaTeX packages into the image. It also installs
   Tectonic and compiles the test resumes.
   If a resume cannot be compiled, the build fails there on purpose, rather
   than on someone's first upload.
4. When the service is live, open `https://<your-service>.onrender.com/api/health`:

   ```json
   {
     "status": "ok",
     "environment": "production",
     "compiler": { "available": true, "backend": "tectonic" },
     "llm_provider": "groq",
     "llm_configured": true,
     "storage_configured": false
   }
   ```

   `compiler.available` and `llm_configured` must both be `true`. Copy the
   service URL for the next step.

## 5. Deploy the frontend — Vercel

1. At [vercel.com/new](https://vercel.com/new), import the same repository.
2. Set **Root Directory** to `frontend`. The framework, build command and output
   directory all come from `frontend/vercel.json`; leave them as they are.
3. Under **Environment Variables**, add:

   | Name | Value | Environments |
   |---|---|---|
   | `FORGE_API_URL` | Your Render URL, e.g. `https://resumeforge-api.onrender.com` | Production and Preview |

4. Deploy, then copy the production URL, e.g. `https://resumeforge.vercel.app`.

If `FORGE_API_URL` is missing or isn't `https://`, the build stops with a
message saying so. That is deliberate: a deployed app that can't find its API
looks healthy and fails every sign-in.

## 6. Connect the two — CORS

In Render, open the service → **Environment**, and set:

```
FORGE_CORS_ORIGINS = https://resumeforge.vercel.app
```

Use your exact Vercel URL: `https://`, **no trailing slash**. Save, and let
Render redeploy.

- Several origins: separate them with commas.
- To admit Vercel preview deployments as well, also set
  `FORGE_CORS_ORIGIN_REGEX` to `^https://resumeforge-[a-z0-9-]+\.vercel\.app$`,
  replacing `resumeforge` with your Vercel project name.

## 7. Check it end to end

Open your Vercel URL, create an account, upload a resume (the repository has one
at `backend/tests/fixtures/jakes_resume.tex`), open it to confirm the parsed
structure, then tailor it against a job description.

The first request after 15 idle minutes waits up to a minute while Render wakes
the API. The app starts that wake-up as soon as the page loads, and the sign-in
form explains the wait if it runs past five seconds.

---

## Optional: PDF download

Without storage, everything works and the tailored `.tex` is offered for
download; the PDF button just doesn't appear. To add it with Cloudflare R2 —
which asks for a payment method on file even for its free tier:

1. In Cloudflare, create an R2 bucket named `resumeforge`.
2. Create an R2 API token with **Object Read & Write** access to that bucket,
   and note its Access Key ID and Secret Access Key.
3. In Render, set:

   | Variable | Value |
   |---|---|
   | `FORGE_S3_ENDPOINT_URL` | `https://<account-id>.r2.cloudflarestorage.com` |
   | `FORGE_S3_ACCESS_KEY` | Access Key ID |
   | `FORGE_S3_SECRET_KEY` | Secret Access Key |

4. After the redeploy, `/api/health` reports `"storage_configured": true`.

Any S3-compatible service works the same way; only the endpoint differs.

---

## What the free tiers mean in practice

| Limit | What you'll notice |
|---|---|
| Render sleeps after 15 idle minutes; waking takes about a minute | A slow first request. Not an error. |
| Render's free plan has no background workers | The worker runs inside the API, one generation at a time. |
| Render's own free Postgres is deleted 30 days after creation | Why the database is Neon. |
| Groq's free tier allows 8,000 tokens per minute | One generation can reach it. Rate limits are retried using Groq's own wait time, so a run gets slower instead of failing. |
| R2 needs a payment method on file | Why PDF storage is optional. |

## Shipping changes

Push to the branch Render and Vercel are watching and both redeploy. Render runs
`alembic upgrade head` before starting the server, so schema changes ship with
the code that needs them. Commits that only touch `frontend/` don't rebuild the
API image.

After changing `backend/app/db/models.py`, create a migration against a local
Postgres:

```bash
docker compose up -d postgres
cd backend
export FORGE_DATABASE_URL=postgresql://forge:forge@localhost:5432/resumeforge
alembic upgrade head
alembic revision --autogenerate -m "describe the change"
```

In PowerShell, set the variable with
`$env:FORGE_DATABASE_URL = "postgresql://forge:forge@localhost:5432/resumeforge"`.

Read the generated file, then run `pytest`: `tests/test_migrations.py` fails if
the models and the migrations describe different schemas.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Vercel build fails: `FORGE_API_URL is not set` | Missing environment variable | Add it (step 5), redeploy |
| Vercel build rejects the Node.js version | Project pinned to an old Node | Project → Settings → General → Node.js Version → 22.x or later |
| Sign-in says it can't reach the server, and the browser console shows a CORS error | `FORGE_CORS_ORIGINS` doesn't exactly match the page's origin | Exact URL, `https://`, no trailing slash (step 6) |
| Same message, no CORS error, `/api/health` won't load either | The API is still building, deploying, or crashed | Check the service's logs in Render |
| Render log: `FORGE_DATABASE_URL is not set` | Missing environment variable | Set it to the Neon string |
| Render log: `FORGE_SECRET_KEY …` | Service created by hand rather than from the Blueprint | Set a random value of 32+ characters |
| `/api/health` shows `"llm_configured": false` | No key for the provider in `FORGE_LLM_PROVIDER` | Set the matching key |
| A generation fails with `Groq API error 429` | Sustained rate limit on the free tier | Wait a minute and regenerate, or switch provider |
| No PDF button after configuring storage | Wrong endpoint or credentials | Check the Render logs for storage errors |
