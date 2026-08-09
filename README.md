# Intelligence Engine

A pre-engagement intelligence tool for management consultants working in
organization, workforce, and change.

Enter a company. The engine verifies it's real, loads a workforce dataset if one
exists, researches the organization through Claude on Amazon Bedrock, identifies
engagement opportunities, and produces a briefing — pausing at five checkpoints
where the consultant validates, corrects, and redirects the work.

**No AWS access? Start here:**
[`docs/architecture-report.html`](docs/architecture-report.html) is a complete
visual walkthrough, and [`docs/samples/`](docs/samples/) contains real output
from the running system so you can see exactly what it produces.

---

## What it does

| Phase | What happens | Checkpoint |
|---|---|---|
| **0a** | Verify the company is real (cheap Haiku call, before spending Sonnet tokens) | — |
| **0b** | Load workforce dataset from S3 and compute ~30 signals | — |
| **1** | Research: headcount, revenue, segments, recent developments | Validate research |
| **2** | Organizational analysis: structure, workforce risks, culture | Review analysis |
| **3** | Identify 4–6 specific engagement opportunities | Prioritize |
| **4** | Draft an 800–1200 word intelligence briefing | Review draft |
| **5** | Render formatted HTML report | Approve for delivery |

At every checkpoint the consultant can **approve** (with optional direction that
carries forward) or **request a revision**, which sends their feedback back to
the model and regenerates that phase. Revision is a loop, not a dead end.

---

## Two consoles

| URL | Who | What they see |
|---|---|---|
| `localhost:5000` | **Consultant** | Client list, progress, checkpoints, the briefing. No infrastructure. |
| `localhost:5000/engineer` | **Engineer** | System state, run traces, guardrail editor, dataset browser, the exact LLM context. |

They are separate Flask blueprints in separate modules. Consultant templates
contain zero links to the engineer console, and it can be switched off entirely
with `ENGINEER_CONSOLE=0`.

---

## Quick start

```bash
pip install -e ".[dev]"
pytest -q                    # 46 tests, no AWS needed
python webapp/app.py         # → http://localhost:5000
```

Without AWS credentials the app still runs — you can research any company by
name. The 20 preset clients and their workforce datasets require S3.

### With AWS

```bash
aws sso login --profile intelligence-dev
./infrastructure/deploy.sh                                  # all three stacks
python scripts/data_generation/generate_all.py --profile intelligence-dev
python webapp/app.py
```

---

## Workforce datasets

Twenty anonymized client archetypes across insurance, automotive, pharma,
logistics, hospitality, healthcare, energy, telecom, banking, aerospace, CPG,
manufacturing, tech services, retail, utilities, professional services, airlines,
media, and commercial real estate.

Each has **500 employee profiles** and **500 job postings** spanning 24 months —
10,000 records of each in total.

Generation is a hybrid: Bedrock produces each company's organizational template
(departments, title ladders, domain skills, hiring patterns), then Python
generates volume from it deterministically. That keeps token cost bounded while
producing industry-accurate data — a pharma client yields *Clinical Development
& Medical Affairs* and *GCP compliance*, not generic placeholders.

The query layer computes ~30 signals from these records and renders them into a
compact block injected into every LLM prompt. See
[`docs/samples/sample_agent_context.txt`](docs/samples/sample_agent_context.txt)
for exactly what the model receives.

---

## Guardrails

Fourteen rules in [`guardrails/config.yaml`](guardrails/config.yaml), each with
`enabled`, a `severity` of block/warn/log, and its own parameters. The engineer
console renders every rule, shows a violation feed, and provides a YAML editor
that validates before saving and hot-reloads.

- **Input** — gibberish detection (no-vowel tokens, keyboard runs, repeated
  characters), placeholder blocklist, prompt-injection patterns
- **Entity** — confirm the company is real before spending tokens; block
  fictional companies; surface low-confidence names for confirmation
- **Output** — PII patterns (blocking), hallucination markers, minimum length,
  research completeness with retry
- **Cost** — per-run token ceiling, revision limit per checkpoint, concurrency cap

---

## Architecture

```
Consultant ──► Guardrails ──► Orchestrator ──► Bedrock (Claude)
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
              Dataset Query   S3 artifacts   DynamoDB state
              (S3, scoped)    (versioned)    (run lifecycle)
```

Client and run isolation is **structural**. Every storage call takes `client_id`
and `run_id` as required arguments and the S3 key is built from them, so a query
scoped to one client cannot return another's records. Tests deliberately attempt
cross-client reads and assert they fail.

---

## Repository layout

```
webapp/
  app.py            consultant console + run orchestration
  engineer.py       engineer console (blueprint)
  runtime.py        shared state, avoids circular import
guardrails/
  engine.py         rule enforcement
  config.yaml       the tuning surface
datasets/
  query.py          S3 access + signal computation
agent/              Bedrock wrapper, run context
storage/            local + S3 backends, one interface
state/              DynamoDB run lifecycle
infrastructure/
  deploy.sh         deploy every stack
  cloudformation/   storage, state, observability, optional logging
scripts/
  data_generation/  company archetypes + generator
  bedrock_usage.py  CloudWatch tokens + Cost Explorer
  export_samples.py refresh docs/samples from S3
docs/
  architecture-report.html   full visual walkthrough
  samples/                   real output, no AWS needed
tests/                       46 tests
```

---

## AWS resources

All CloudFormation-managed, tagged `Application=intelligence-engine`.

| Stack | Resource | Cost |
|---|---|---|
| `…-dev-storage` | S3 bucket — versioned, AES-256, TLS-only, private | per GB |
| `…-dev-state` | DynamoDB — on-demand, PITR, client_id GSI | per request |
| `…-dev-observability` | CloudWatch dashboard — tokens, latency, cache, errors | free |

Runtime uses Bedrock on-demand: Claude Sonnet 4.6 for the engine, Haiku 4.5 for
entity verification.

No account IDs appear anywhere in this repository.

---

## Documentation

- [`docs/architecture-report.html`](docs/architecture-report.html) — full visual walkthrough
- [`docs/samples/`](docs/samples/) — real system output, readable without AWS
- [`docs/corporate-deployment-architecture.md`](docs/corporate-deployment-architecture.md) — target model for a firm where DevOps owns infrastructure
- [`docs/bedrock-usage-monitoring.md`](docs/bedrock-usage-monitoring.md) — token and cost monitoring
- [`docs/decisions.md`](docs/decisions.md) — architectural decision log
- [`docs/build-status.md`](docs/build-status.md) — milestones and known limits
- [`CLAUDE.md`](CLAUDE.md) — context for Claude Code sessions

---

## Known limits

Stated plainly:

- **Run state is in-memory.** The DynamoDB table exists and the CLI orchestrator
  uses it, but the web app keeps runs in a process dictionary. Restarting loses
  history.
- **Research is model knowledge, not live retrieval.** Facts come from training
  data and are bounded by the cutoff. Checkpoint 1 exists precisely so a
  consultant validates them.
- **Datasets are synthetic.** They demonstrate the pattern of grounding analysis
  in measured signals; they are not evidence about any real company.
- **No authentication.** Intended for local use. Corporate deployment needs SSO.
- **No CI/CD.** Tests run locally; deployment is manual.

---

## License

MIT — see [LICENSE](LICENSE).
