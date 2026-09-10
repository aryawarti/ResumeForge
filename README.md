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

---

## Running it

**Prerequisites:** Python 3.12+, Node 20+, Docker (for local Postgres and
MinIO), and an Anthropic API key.

```bash
git clone <this repo> && cd ResumeForge

cp .env.example backend/.env      # then fill in FORGE_ANTHROPIC_API_KEY
docker compose up -d              # Postgres + MinIO

cd backend
python -m venv .venv && ./.venv/Scripts/activate   # Unix: source .venv/bin/activate
pip install -e ".[dev]"
python scripts/install_tectonic.py                 # ~35 MB, no TeX install needed
uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm start            # http://localhost:4200
```

### Without the web app

The pipeline runs standalone, which is the fastest way to iterate on it:

```bash
forge inspect resume.tex                  # show the parsed structure
forge compile resume.tex                  # compile to PDF
forge tailor resume.tex --job job.txt     # full pipeline, writes .tex + .pdf + report
```

### Tests

```bash
cd backend && pytest          # 81 tests
```

The ones that matter most are `test_roundtrip.py` (the byte-identity property)
and `test_guards.py` (every way a hallucination could get through).

---

## Deployment

Free tiers throughout, which shaped the architecture:

| Piece | Host | Why |
|---|---|---|
| Angular SPA | Vercel | Static files; `vercel.json` handles SPA routing. |
| API + worker | Render | Docker, one web service. |
| Postgres | **Neon** | Render's own free Postgres expires 30 days after creation. |
| PDFs | Cloudflare R2 | S3-compatible and no egress fees, so the code is unchanged from MinIO. |

Two consequences worth knowing before you deploy:

**There is no separate worker.** Render's free plan has no background worker
service (Starter, $7/mo, is the floor), so the job queue is a Postgres table
drained by an in-process `asyncio` worker using `SELECT … FOR UPDATE SKIP
LOCKED`. On a 512 MB instance that is the right shape anyway. Jobs carry a
heartbeat, and stale ones are requeued at startup — a free instance can be
restarted mid-generation.

**Free instances sleep.** After 15 minutes idle the next request waits about a
minute for cold start. And at 0.1 shared CPU a generation runs meaningfully
slower than the sub-minute target — with three compile retries it can exceed
that. The Starter plan is what makes the timing claim hold; everything else
works unchanged on free.

The Tectonic package cache is warmed during the Docker build. Free instances
have no persistent disk, so without that step the first request after every
deploy would re-download the LaTeX support files.

---

## Layout

```
backend/app/
  latex/      scanner, parser, tree, editbuffer, compiler   ← the round-trip guarantee
  agent/      ops, guards, lexicon, graph, prompts, report  ← the typed edits and their validation
  api/        routes and schemas
  db/         models with soft deletes throughout
frontend/src/app/
  core/       auth, refresh interceptor, SSE progress
  features/   resumes, tailor, generations, diff view, gaps
```

Base resumes and generated versions are independent: deleting one never affects
the other, and every generation stores its own `.tex` snapshot. You will get a
callback three weeks later and need to know exactly which version they are
holding.
