# InsightForge

**An AI-powered autonomous data analyst.** Ask a business question like *"Why did revenue drop last month?"* and InsightForge plans a multi-step investigation, discovers the relevant schema, generates and safety-checks SQL, repairs its own failed queries, validates its results with independent calculations, and produces an evidence-backed explanation — clearly separating proven facts from unproven hypotheses.

> Not a text-to-SQL chatbot. A junior BI analyst that shows its work.

## Architecture (planned)

```
Question ──► Interpreter ──► Investigation Planner ──► Schema Discovery
                                                            │
        Insight Generator ◄── Result Validator ◄── Executor ◄── SQL Firewall ◄── SQL Generator
              │                                       ▲                              │
              ▼                                       └────── Query Repair ◄── (on error, ≤3 tries)
        Evidence-backed report + charts
```

Every AI-generated query passes through a **SQLGlot-based firewall** (read-only, allowlisted tables, row limits, timeouts, audit log) and runs against a **read-only Postgres role**. Every important claim is re-verified with an independent query before it reaches the final answer.

## Roadmap

| Stage | Deliverable | Status |
|-------|-------------|--------|
| 1 | Foundation: FastAPI, Postgres via Docker, config, health checks, module layout | ✅ this repo |
| 2 | E-commerce schema + synthetic data generator with injected ground-truth anomalies | ⬜ |
| 3 | Schema discovery + question interpreter | ⬜ |
| 4 | Semantic layer enforcement + investigation planner | ⬜ |
| 5 | SQL generation, firewall, read-only executor, query repair | ⬜ |
| 6 | Result validation + approved analytics functions | ⬜ |
| 7 | Insight generation with evidence panel | ⬜ |
| 8 | Observability: traces, token/cost accounting | ⬜ |
| 9 | Evaluation framework: benchmark + scoring dashboard | ⬜ |
| 10 | Next.js frontend + deployment | ⬜ |

## Quickstart

Requirements: Python 3.11+, Docker.

```bash
cp .env.example .env          # add your ANTHROPIC_API_KEY
make install                  # create venv, install deps
make db-up                    # start Postgres in Docker
make run                      # start the API on :8000
```

Verify:

```bash
curl http://localhost:8000/health      # liveness
curl http://localhost:8000/health/db   # database readiness
```

Run tests: `make test` · Lint: `make lint`

## Repository layout

```
backend/app/
  core/            config, database engine, structured logging
  api/routes/      HTTP endpoints
  agents/          AI components (interpreter, planner, generator, repair, validator, insights)
  security/        SQL firewall + audit log
  semantic_layer/  approved metric definitions (metrics.yaml)
  analytics/       approved statistical functions the AI may call
  db/              schema introspection, read-only executor, seeding
  evaluation/      benchmark questions with ground-truth answers + scoring
  observability/   tracing, latency, token & cost metrics
  services/        investigation orchestration
  schemas/         shared Pydantic models
backend/tests/
scripts/           data generation & maintenance scripts
docs/              architecture notes, failure analyses
```

## Design principles

1. **Safety by construction** — the AI can only emit `SELECT`s that survive parsing-based policy checks, and only against a read-only DB role.
2. **No invented metrics** — business terms resolve to approved definitions in `metrics.yaml`.
3. **Claims require evidence** — every finding carries its query, row counts, period, metric definition, and validation status.
4. **Measured, not demoed** — a benchmark with known answers scores accuracy, groundedness, safety, repair success, latency, and cost.
