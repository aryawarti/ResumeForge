# ResumeForge

Tailors a LaTeX resume to a job description without breaking the formatting or
inventing experience you don't have.

**[Live demo](https://resume-forge-delta-taupe.vercel.app/)**

<!-- Add a screenshot of the diff view here once you have one:
![Diff view](docs/screenshot.png)
-->

## The problem

Sending the same resume to 40 jobs rarely works. ATS filters reject on keyword
mismatch, and humans skim the top third of the page. Tailoring each application
fixes both but takes half an hour by hand.

AI tools that do it have two failure modes. They work on plain text, so the
formatting is destroyed. And they hallucinate, quietly adding "Kubernetes"
because the posting asked for it.

## How it works

**Formatting.** The `.tex` file is parsed into a tree where every node keeps
byte offsets into the original source. Edits are recorded as
`(span, replacement)` pairs and applied in order, so anything untouched emits
its original bytes. Parsing and re-rendering a file with no edits returns it
byte for byte; there's a property test covering that, including a fixture
built to break the scanner.

**Hallucination.** The model never writes prose into the document. It returns
typed operations (reorder these bullets, rewrite this one), which code applies
and validates. There is no "insert bullet" operation, so a skill you don't
have cannot be added. Rewrites then pass three checks:

| Guard | Rule |
|---|---|
| Technology | No technology that wasn't in the original bullet |
| Numeric | Every figure must already exist in the original |
| Scope | No new "led", "owned", "architected", "team of N" |

The scope guard is the one most tools miss. "Built the payments service"
becoming "Led development of the payments service" adds no technology and no
number, and is still a lie you get caught telling.

Rejected operations are dropped and listed in the report rather than hidden.

## Pipeline

A LangGraph graph with two bounded cycles:

```
parse resume → read posting ⇄ gap fill     (when the posting is thin)
             → coverage → plan → apply
             → compile ⇄ fix               (capped at 3 attempts)
                       ⇄ trim              (if it gained a page)
             → deliver
```

Coverage sorts each requirement into covered and prominent, covered but buried,
or absent. Only the middle case is safe to automate. The rest becomes a gaps
report, which aggregates across applications: after fifteen of them, it can
tell you eleven wanted Kafka.

## Stack

Angular 21 (signals, zoneless) · FastAPI · LangGraph · Postgres · Tectonic ·
Groq or Anthropic, switchable with `FORGE_LLM_PROVIDER`

## Running locally

Needs Python 3.12+, Node 20.19+, and a Groq or Anthropic key.

```bash
cp .env.example backend/.env      # set the provider and its key

cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python scripts/install_tectonic.py
uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm start
```

Defaults to SQLite with object storage off, so nothing else has to be running.

The pipeline also runs without the web app:

```bash
forge inspect resume.tex          # show the parsed structure
forge tailor resume.tex job.txt   # full run, writes .tex, .pdf and a report
```

## Tests

```bash
cd backend && pytest
```

`test_roundtrip.py` covers the byte-identity property, `test_guards.py` every
way a hallucination could get through, and `test_migrations.py` that the models
and the migrations describe the same schema.

## Deploying

Free tier throughout: Vercel, Render, Neon, Groq. See [DEPLOY.md](DEPLOY.md).

## License

MIT
