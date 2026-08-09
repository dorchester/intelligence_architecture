# Intelligence Engine

A pre-engagement intelligence tool for management consultants specializing in
organization, workforce, and change. Enter a real company name — the engine
researches it via Claude on Amazon Bedrock, analyzes organizational dynamics,
identifies engagement opportunities, and produces a professional intelligence
briefing with 5 human-in-the-loop checkpoints.

## What This Does

1. **Research** a real company (headcount, revenue, segments, recent developments)
2. **Analyze** organizational structure, workforce risks, and culture signals
3. **Identify** 4-6 specific consulting engagement opportunities
4. **Draft** an 800-1200 word intelligence briefing for senior consultants
5. **Render** a professional HTML report ready for internal circulation

At each phase, the consultant can validate findings, correct errors, redirect
focus, and shape the final output through feedback that feeds into subsequent
LLM calls.

## Quick Start

```bash
pip install -e ".[dev]"
python webapp/app.py
```

Open http://localhost:5000 — enter any company name (e.g. "FedEx", "Eli Lilly",
"General Motors") and walk through the 5-checkpoint workflow.

## Architecture

```
Consultant (localhost:5000)
    |
    |-- Phase 1: Company Research ------ Claude Sonnet 4.6 via Bedrock
    |-- Phase 2: Org Analysis ---------- Claude Sonnet 4.6 via Bedrock
    |-- Phase 3: Opportunities --------- Claude Sonnet 4.6 via Bedrock
    |-- Phase 4: Briefing Draft -------- Claude Sonnet 4.6 via Bedrock
    '-- Phase 5: HTML Report ----------- Jinja2 template
         |
         '-- All artifacts stored in runs/{company}/{run_id}/
```

**AWS Services Used:**
- Amazon Bedrock (Claude Sonnet 4.6 inference)
- S3 (run artifact storage, CloudFormation-managed)
- DynamoDB (run state and checkpoints, CloudFormation-managed)
- CloudWatch (Bedrock usage monitoring dashboard)

## Two Interfaces

| URL | Who | Purpose |
|-----|-----|---------|
| `localhost:5000` | Consultant | Start briefings, approve checkpoints, view reports |
| `localhost:5000/engineer` | Engineer | System dashboard, all runs, infrastructure status, run inspection |

## Project Structure

```
intelligence_architecture/
├── webapp/             — Flask web UI (primary entry point)
│   ├── app.py          — routes, LLM orchestration, checkpoint logic
│   └── templates/      — Jinja2 HTML (index, run, engineer, engineer_run)
├── agent/              — agent engine, model abstraction, context
├── tools/              — deterministic analytical tools (pandas, matplotlib)
├── storage/            — storage abstraction (local filesystem + S3)
├── state/              — DynamoDB run state manager
├── methodology/        — Markdown playbooks (version-controlled)
├── prompts/            — system prompt + narrative templates
├── infrastructure/     — CloudFormation templates
├── scripts/            — Bedrock usage reporting, data generation
├── docs/               — architecture, decisions, corporate deployment
├── tests/              — pytest suite (17 tests)
├── orchestrator.py     — CLI path (S3 + DynamoDB + agent engine)
└── CLAUDE.md           — context file for Claude Code sessions
```

## Key Design Decisions

1. **LLM does real research** — not fake data generation. Uses Claude's knowledge
   to produce accurate company intelligence.
2. **5 human checkpoints** — consultant validates, corrects, and directs at each phase.
3. **Feedback compounds** — operator input at checkpoint N feeds into phases N+1, N+2, etc.
4. **Deterministic tools separated from reasoning** — analytical code is tested and versioned.
5. **Run isolation** — client/run prefix structure enforced by storage interface.
6. **Infrastructure as code** — all AWS resources in CloudFormation.

## AWS Setup

```bash
# Verify credentials
aws sts get-caller-identity --profile intelligence-dev

# Deploy infrastructure (already done in dev)
aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
  --template-file infrastructure/cloudformation/storage.yaml \
  --stack-name intelligence-engine-dev-storage --parameter-overrides Environment=dev
```

See `docs/build-status.md` for full resource inventory.

## Tests

```bash
pytest -v                    # 17 tests (no AWS needed)
python scripts/bedrock_usage.py --profile intelligence-dev --hours 24  # Usage report
```

## Documentation

- [CLAUDE.md](CLAUDE.md) — session continuity for Claude Code
- [docs/build-status.md](docs/build-status.md) — milestones, resources, next steps
- [docs/corporate-deployment-architecture.md](docs/corporate-deployment-architecture.md) — target corporate model
- [docs/bedrock-usage-monitoring.md](docs/bedrock-usage-monitoring.md) — monitoring setup
- [docs/decisions.md](docs/decisions.md) — architectural decision log
- [docs/v0-architecture-report.html](docs/v0-architecture-report.html) — visual architecture report

## License

MIT — see [LICENSE](LICENSE).
