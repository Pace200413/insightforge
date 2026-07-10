# InsightForge

**An AI-powered autonomous data analyst.** You ask a business question like *"Why did revenue drop last month?"* and InsightForge plans a multi-step investigation, discovers the relevant database schema, writes and safety-checks its own SQL, repairs its queries when they fail, validates the results, and explains what it found — clearly separating proven facts from unproven hypotheses, with the evidence attached.

> It's not a text-to-SQL chatbot. It's a junior BI analyst that shows its work.

![InsightForge investigating a business question](docs/demo.png)

---

## Why I built this

I wanted a portfolio project that went beyond prompt engineering. A tool that turns one question into one SQL query is already everywhere and doesn't prove much. So I built the harder thing: a system that behaves like an analyst investigating an unfamiliar company database — forming a plan, running multiple queries, catching its own mistakes, checking its own conclusions, and being honest about what the data can and can't tell you.

The most interesting engineering wasn't the AI itself. It was everything around it: the security layer that stops it from running anything dangerous, the validation that re-checks its numbers, and the evaluation framework that scores its answers against known ground truth. Those unglamorous parts are what make an AI system trustworthy.

---

## How it works

When you ask *"Why did revenue drop last month?"*, InsightForge runs this pipeline:

```
Question ──► Interpreter ──► Investigation Planner ──► Schema Discovery
                                                            │
        Insight Generator ◄── Result Validator ◄── Executor ◄── SQL Firewall ◄── SQL Generator
              │                                       ▲                              │
              ▼                                       └────── Query Repair ◄── (on error, ≤3 tries)
        Evidence-backed answer
```

1. **Interpret** — an LLM parses the question into a structured spec: the metric (resolved against approved business definitions), the time period, and whether it needs a root-cause investigation.
2. **Plan** — a rule-based planner breaks the question into concrete analytical steps (compare by segment, by region, check refunds, check order volume).
3. **Discover schema** — instead of dumping the whole database schema at the AI, it *searches* for the tables relevant to each step, the way a real analyst explores an unfamiliar database.
4. **Generate SQL** — the LLM writes a query for each step.
5. **Firewall** — every query is parsed with SQLGlot and only allowed to run if it's a read-only `SELECT` against approved tables. Dangerous operations (`DROP`, `DELETE`, `pg_sleep`, PII columns) are blocked before they reach the database.
6. **Execute & repair** — queries run against PostgreSQL inside a read-only transaction. When one fails, a repair agent reads the database error and fixes the query, up to 3 attempts.
7. **Validate** — results are checked for the kinds of mistakes that produce confident-but-wrong answers: empty results, join fan-out, all-null columns.
8. **Explain** — a final agent turns the validated results into a plain-English answer, separating observed facts from hypotheses, and stating what the database can't prove.

Every number in the final answer comes from a real query against real data. Nothing is invented.

Each finding expands to reveal the exact SQL, the rows it returned, how many repair attempts it took, and how long it ran — so you can trace any claim back to its evidence:

![The evidence panel — every query the analyst ran](docs/evidence.png)

---

## What makes it different

- **Safety by construction** — the AI can only emit `SELECT`s that survive parse-tree policy checks. No regex filters (which are trivially bypassed); the firewall reasons over the actual SQL syntax tree.
- **It fixes its own mistakes** — when the LLM writes broken SQL, the repair agent reads the Postgres error and corrects it. I've watched it recover from real failures live.
- **Measured, not demoed** — I seeded the database with six known business anomalies (an enterprise sales collapse, a regional slowdown, a refund spike, a failed campaign, and two data-quality traps), then built an evaluation framework that scores the AI's conclusions against that ground truth. No cherry-picked demos.
- **Production thinking** — full observability: every query is logged to an audit trail, and token usage, latency, and estimated cost are tracked per investigation.

---

## Tech stack

- **Backend:** FastAPI, async SQLAlchemy 2, PostgreSQL (via Docker)
- **AI:** LLM tool-calling (works with Groq's free tier or Anthropic), abstracted behind a provider factory
- **SQL safety:** SQLGlot parse-tree analysis
- **Testing:** pytest — **103 passing tests**, all runnable without a database or API key
- **Frontend:** self-contained HTML — an investigation timeline and an evidence panel where every finding expands to show the SQL and rows behind it

---

## Quickstart

Requirements: Python 3.11+, Docker.

```bash
cp .env.example .env          # add your GROQ_API_KEY (free) or ANTHROPIC_API_KEY
make install                  # create venv, install deps
make db-up                    # start Postgres in Docker
make seed                     # generate the synthetic database (~650k rows)
make run                      # start the API on :8000
```

Open the frontend:

```bash
cd frontend && python3 -m http.server 3000
# then open http://localhost:3000
```

Run the test suite:

```bash
make test    # 103 tests
```

Score the AI against the ground-truth benchmark:

```bash
make eval
```

---

## What's inside

```
backend/app/
  core/            config, database engine, structured logging
  api/routes/      HTTP endpoints (health, investigate, observability)
  agents/          the AI components — interpreter, planner, SQL generator,
                   query repair, insight generator
  security/        SQL firewall + audit log
  semantic_layer/  approved metric definitions (metrics.yaml)
  db/              schema discovery, read-only executor, data generator
  evaluation/      benchmark questions with ground-truth answers + scoring
  observability/   token, latency & cost tracking
  services/        investigation orchestrator + result validator
  schemas/         shared Pydantic models
scripts/           anomaly manifest + database seeding
docs/              architecture notes and an honest failure analysis
```

---

## Design principles

1. **Safety by construction** — the AI can only run queries that pass parse-based policy checks, and only read-only ones.
2. **No invented metrics** — business terms like "net revenue" resolve to approved definitions, not the model's guess.
3. **Claims require evidence** — every finding carries the query, row counts, time period, and validation status behind it.
4. **Measured, not demoed** — a benchmark with known answers scores accuracy, groundedness, safety, and repair success.

---

## Honest limitations

This runs on a free LLM tier, which shapes what it can do today:

- **Rate limits.** The free Groq tier caps daily tokens, so a full multi-question evaluation run can exhaust the budget. A paid key removes this.
- **SQL quality.** The model occasionally writes queries with mistakes (referencing aliases before they exist, nested aggregates). The repair agent recovers most of these, and the firewall blocks the rest — but it means not every investigation completes cleanly on the free tier.
- **Not deployed.** This runs locally. Deploying it would need a hosted Postgres, secrets management, and a paid LLM key — out of scope for a portfolio project, but the architecture supports it.

I chose to document these honestly rather than hide them behind a hand-picked demo. The `docs/failure_analysis.md` file goes into detail on the SQL-generation failures and how the repair layer handles them — because how a system fails is as important as how it succeeds.

---

Built by **Islam Mamedov**
[GitHub](https://github.com/islam-mamedov) · [Hugging Face](https://huggingface.co/islam-mamedov)