# Deploying

Everything runs on free tiers: **Vercel** serves the Angular app, **Render**
runs the API with the worker inside it, **Neon** hosts Postgres, **Groq** runs
the model. PDF storage is optional.

```
Browser ──► Vercel   Angular app
   │
   └──────► Render   FastAPI + worker + Tectonic ──► Neon  Postgres
                                                └──► Groq  model
```

The frontend calls Render directly instead of proxying through Vercel, so the
live progress stream isn't buffered by a serverless hop. That costs one CORS
setting — step 5.

## 1. Database — Neon

1. At [neon.tech](https://neon.tech), click **New Project**.
2. For **region**, pick **AWS US West 2 (Oregon)**, then create.

   Your API runs in Oregon (`region: oregon` in `render.yaml`). Each page load
   makes several queries, so a database on another continent pays that trip
   every time. To use a different region, change `region:` in `render.yaml` and
   push it *before* step 3 — Render can't move a service after it's created.

   | `render.yaml` | Neon region |
   |---|---|
   | `oregon` | AWS US West 2 (Oregon) |
   | `ohio` | AWS US East 2 (Ohio) |
   | `virginia` | AWS US East 1 (N. Virginia) |
   | `frankfurt` | AWS Europe Central 1 (Frankfurt) |
   | `singapore` | AWS Asia Pacific 1 (Singapore) |

3. Click **Connect** and copy the whole connection string:

   ```
   postgresql://user:pass@ep-xxx.us-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```

   Copy it **including** `?sslmode=require&channel_binding=require`. The app
   rewrites those itself; deleting them is the usual reason a deployed database
   won't connect.

You don't create tables or run any SQL — migrations do that on first start.

## 2. Model key — Groq

Create one at [console.groq.com/keys](https://console.groq.com/keys).

For Anthropic instead, set `FORGE_LLM_PROVIDER=anthropic` and supply
`FORGE_ANTHROPIC_API_KEY` in the next step.

## 3. API — Render

1. **New → Blueprint**, connect the repo. Render reads `render.yaml`.
2. Fill in:

   | Variable | Value |
   |---|---|
   | `FORGE_DATABASE_URL` | the Neon string from step 1 |
   | `FORGE_GROQ_API_KEY` | the key from step 2 |
   | everything else | leave blank |

3. Apply. The first build takes ~6 minutes — it installs Tectonic, pulls the
   LaTeX packages into the image, and compiles the test resumes. A resume that
   won't compile fails the build here rather than on a user's first upload.
4. Open `https://<service>.onrender.com/api/health`. `compiler.available` and
   `llm_configured` must both be `true`. Copy the service URL.

## 4. Frontend — Vercel

1. At [vercel.com/new](https://vercel.com/new), import the repo.
2. Set **Root Directory** to `frontend`. Leave the build settings alone —
   `frontend/vercel.json` supplies them.
3. Add an environment variable, for Production and Preview:

   | Name | Value |
   |---|---|
   | `FORGE_API_URL` | your Render URL from step 3 |

4. Deploy, then copy the production URL.

A missing or non-`https` `FORGE_API_URL` fails the build deliberately — an app
that can't reach its API looks healthy and fails every sign-in.

## 5. Connect them

In Render → **Environment**:

```
FORGE_CORS_ORIGINS = https://your-app.vercel.app
```

Exact URL, `https://`, **no trailing slash**. Save and let it redeploy.

- Multiple origins: comma-separated.
- Preview deployments: set `FORGE_CORS_ORIGIN_REGEX`, e.g.
  `^https://your-project-[a-z0-9-]+\.vercel\.app$`.

## 6. Check it

Open the Vercel URL, create an account, upload
`backend/tests/fixtures/jakes_resume.tex`, and tailor it against a job posting.

The first request after 15 idle minutes waits up to a minute while Render wakes
up. The app starts that wake-up on page load, and the sign-in form says so if
it runs long.

## Optional: PDF download

Without storage, tailoring works and the `.tex` is offered; the PDF button
doesn't appear. To enable it with Cloudflare R2 (which wants a payment method
on file even for the free tier):

1. Create an R2 bucket named `resumeforge`.
2. Create an API token with **Object Read & Write** on it.
3. In Render, set `FORGE_S3_ENDPOINT_URL`
   (`https://<account-id>.r2.cloudflarestorage.com`), `FORGE_S3_ACCESS_KEY`,
   `FORGE_S3_SECRET_KEY`.

`/api/health` then reports `"storage_configured": true`. Any S3-compatible
service works — only the endpoint differs.

## Free tier limits

| Limit | Effect |
|---|---|
| Render sleeps after 15 idle minutes | First request waits ~1 minute |
| No background workers on free | Worker runs in the API process, one job at a time |
| Render's own free Postgres expires after 30 days | Why the database is Neon |
| Groq allows 8,000 tokens/minute | One generation can hit it; retried automatically, so runs get slower rather than failing |

## Shipping changes

Push, and both redeploy. Render runs `alembic upgrade head` before starting, so
schema changes ship with the code. Commits touching only `frontend/` skip the
API build.

After editing `backend/app/db/models.py`:

```bash
docker compose up -d postgres
cd backend
export FORGE_DATABASE_URL=postgresql://forge:forge@localhost:5432/resumeforge
alembic upgrade head
alembic revision --autogenerate -m "what changed"
```

Read the generated file, then run `pytest` — `test_migrations.py` fails if the
models and migrations disagree.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Vercel build: `FORGE_API_URL is not set` | Add the env var (step 4) |
| Sign-in fails, console shows a CORS error | `FORGE_CORS_ORIGINS` must match the origin exactly — `https://`, no trailing slash |
| Sign-in fails, `/api/health` also won't load | API is building or crashed — check Render logs |
| Render log: `FORGE_DATABASE_URL is not set` | Paste the Neon string |
| `/api/health` shows `llm_configured: false` | Missing key for the provider in `FORGE_LLM_PROVIDER` |
| Generation fails with `Groq API error 429` | Free-tier rate limit; wait a minute or switch provider |
