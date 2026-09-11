# ResumeForge

Upload your LaTeX resume once. Paste any job description. Get back a tailored,
compiled resume — with every change explained and nothing invented.

---

## Why this is built the way it is

Two decisions define the system, and most of the code follows from them.

**LaTeX is the source format.** A `.tex` file has structure a program can
target. The resume is parsed into a typed tree, specific nodes are edited, and
the file is re-rendered. The whole file is never handed to a language model.
Formatting preservation is therefore structural, not hopeful.

**The model emits operations, not prose.** It returns a list of typed edits —
reorder these bullets, rewrite this one, promote this section — each with a
stated reason. Operations are applied by code and validated by code. The model
influences content; it never touches layout.

The second decision is also the anti-hallucination mechanism. There is no
"insert bullet" operation in the schema, so the model *cannot* add a skill you
don't have — not "is checked for and rejected", but has no way to express it.

---

## The round-trip guarantee

The parser never re-serialises an AST. Every node holds byte offsets into the
immutable source, and editing records `(span, replacement)` pairs that are
applied in offset order. A node nobody edited emits its original bytes.

So `render(parse(src))` with zero edits returns a **byte-identical** file, and
that is a property test rather than an aspiration — including against
`tests/fixtures/adversarial.tex`, which exists to break the scanner with
escaped braces, comments containing `}`, and nested environments.

Anything the parser does not recognise is preserved verbatim.

---

## The validators

Rewrites pass three independent guards before they are applied. A failure
rejects that one operation; the run continues, and the rejection is shown in
the report rather than hidden.

| Guard | Rule |
|---|---|
| Technology | A rewritten bullet may not gain any technology absent from the original bullet. |
| Numeric | Every figure in a rewrite must exist in the original. |
| Scope | No new `led` / `owned` / `architected` / `team of N`. |

The scope guard is the one most tools lack. "Built the payments service" →
"Led development of the payments service" adds no technology and no number,
and would sail past a keyword check — but it is a lie you get caught telling in
a screening call.

Two further checks run on the document as a whole: it must compile, and it must
not have gained a page.

LaTeX escaping is applied **in code** when an edit lands, never requested from
the model, which makes a whole class of compile failure impossible.

---

## The pipeline

A LangGraph graph with two genuine cycles:

```
parse_resume → parse_job ⇄ gap_fill        (cycle 1, when the posting is thin)
             → coverage → plan → apply
             → compile ⇄ compile_fix       (cycle 2, capped at 3 attempts)
                       ⇄ trim              (when the page count grew)
             → deliver
```

Both cycles are bounded. `coverage` classifies each requirement as covered and
prominent, covered but buried or differently phrased, or genuinely absent —
three situations needing three responses, and only the middle one can be
automated safely. The rest becomes the gaps report.

The model behind it is either Anthropic or Groq, selected by
`FORGE_LLM_PROVIDER`. Both constrain the edit vocabulary during generation, and
the guards above are code, so the guarantee is the same on either.

---

## Running it

**Prerequisites:** Python 3.12+, Node 20.19+, and a Groq or Anthropic API key.
Docker is optional.

```bash
cp .env.example backend/.env       # then set FORGE_LLM_PROVIDER and its key

cd backend
python -m venv .venv && ./.venv/Scripts/activate    # Unix: source .venv/bin/activate
pip install -e ".[dev]"
python scripts/install_tectonic.py                  # no TeX installation needed
uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm start             # http://localhost:4200
```

The default database is a SQLite file created on first start, and object
storage is off, so nothing else needs to be running. To develop against
Postgres instead:

```bash
docker compose up -d postgres
cd backend
export FORGE_DATABASE_URL=postgresql://forge:forge@localhost:5432/resumeforge
alembic upgrade head
```

Postgres schemas — local or hosted — are only ever changed by migrations. SQLite
tables are created straight from the models, and `tests/test_migrations.py`
keeps the two identical.

### Without the web app

The pipeline runs standalone, which is the fastest way to iterate on it:

```bash
forge inspect resume.tex                  # show the parsed structure
forge compile resume.tex                  # compile to PDF
forge tailor resume.tex job.txt           # full pipeline, writes .tex + .pdf + report
```

### Tests

```bash
cd backend && pytest
```

The ones that matter most are `test_roundtrip.py` (the byte-identity property),
`test_guards.py` (every way a hallucination could get through), and
`test_migrations.py` (models and migrations describe the same schema). The
migration tests run against SQLite by default; point
`FORGE_TEST_POSTGRES_URL` at a disposable Postgres database to run them there
too.

---

## Deployment

Free tiers throughout. The step-by-step guide is **[DEPLOY.md](DEPLOY.md)**.

| Piece | Host | Why |
|---|---|---|
| Angular SPA | Vercel | Static files. The API URL is written into `config.js` at build time from `FORGE_API_URL`. |
| API + worker | Render | One Docker web service; migrations run before it starts. |
| Postgres | Neon | Render's own free Postgres is deleted 30 days after creation. |
| Model | Groq or Anthropic | Groq has a free tier; Anthropic gives better rewrites. |
| PDFs (optional) | Any S3-compatible store | Without one, the tailored `.tex` is offered instead. |

Two consequences worth knowing before you deploy:

**There is no separate worker.** Render's free plan has no background worker
service, so the job queue is a Postgres table drained by an in-process
`asyncio` worker using `SELECT … FOR UPDATE SKIP LOCKED`. On a 512 MB instance
that is the right shape anyway. Jobs carry a heartbeat, and stale ones are
requeued at startup — a free instance can be restarted mid-generation.

**Free instances sleep.** After 15 minutes idle the next request waits about a
minute for cold start; the app pings the API as the page loads so the wake-up
starts early. And at 0.1 shared CPU a generation runs meaningfully slower than
the sub-minute target — with compile retries it can exceed that.

The Tectonic package cache is warmed during the Docker build. Free instances
have no persistent disk, so without that step the first compile after every
deploy would re-download the LaTeX support files.

---

## Layout

```
backend/app/
  latex/      scanner, parser, tree, editbuffer, compiler   ← the round-trip guarantee
  agent/      ops, guards, lexicon, graph, prompts, report  ← the typed edits and their validation
  api/        routes and schemas
  db/         models with soft deletes throughout
backend/migrations/                                          ← the only thing that changes a Postgres schema
frontend/src/app/
  core/       auth, refresh interceptor, SSE progress
  features/   resumes, tailor, generations, diff view, gaps
infra/        the API's Docker image
```

Base resumes and generated versions are independent: deleting one never affects
the other, and every generation stores its own `.tex` snapshot. You will get a
callback three weeks later and need to know exactly which version they are
holding.
