# CLAUDE.md — Intelligence Engine

## What This Is

A pre-engagement intelligence tool for management consultants. Enter a real company name, the engine researches it via Claude on Bedrock, generates organizational/workforce analysis, identifies engagement opportunities, and produces a professional intelligence briefing — with 5 human checkpoints throughout.

## Quick Start

```bash
pip install -e ".[dev]"
pytest                           # 17 tests, no AWS needed
python webapp/app.py             # Consultant UI at localhost:5000
                                 # Engineer dashboard at localhost:5000/engineer
```

## AWS Setup

Profile: `intelligence-dev` (SSO, us-east-1)
Verify: `aws sts get-caller-identity --profile intelligence-dev`

Three CloudFormation stacks deployed:
- `intelligence-engine-dev-storage` — S3 bucket for run artifacts
- `intelligence-engine-dev-state` — DynamoDB table for run state
- `intelligence-engine-dev-observability` — CloudWatch usage dashboard

Deploy all:
```bash
aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
  --template-file infrastructure/cloudformation/storage.yaml \
  --stack-name intelligence-engine-dev-storage --parameter-overrides Environment=dev

aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
  --template-file infrastructure/cloudformation/state.yaml \
  --stack-name intelligence-engine-dev-state --parameter-overrides Environment=dev

aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
  --template-file infrastructure/cloudformation/observability.yaml \
  --stack-name intelligence-engine-dev-observability --parameter-overrides Environment=dev
```

## Architecture

```
Consultant (webapp/) → Bedrock Claude Sonnet 4.6 → Research + Analysis → HTML Briefing
                              ↕
                    S3 (artifacts) + DynamoDB (state)
```

Two personas:
- **Consultant** (`localhost:5000`): enters company, approves 5 checkpoints, gets intelligence briefing
- **Engineer** (`localhost:5000/engineer`): system dashboard, run inspection, infrastructure status

## Key Files

| Path | Purpose |
|------|---------|
| `webapp/app.py` | Flask app — all routes, LLM calls, checkpoint logic |
| `webapp/templates/` | Jinja2 HTML templates (index, run, engineer, engineer_run) |
| `agent/engine.py` | Methodology-driven agent with Bedrock tool-use |
| `agent/model.py` | Bedrock model abstraction (configurable) |
| `agent/context.py` | RunContext — per-run state and artifact access |
| `storage/` | StorageBackend interface (LocalStorage, S3Storage) |
| `state/run_state.py` | DynamoDB run lifecycle manager |
| `tools/basic_analysis.py` | Deterministic pandas workforce analysis |
| `tools/chart.py` | matplotlib chart generation |
| `tools/report.py` | Jinja2 HTML report renderer |
| `methodology/` | Markdown playbooks the agent follows |
| `prompts/` | System prompt + narrative template |
| `infrastructure/cloudformation/` | All AWS infrastructure as CloudFormation |
| `scripts/bedrock_usage.py` | CLI for Bedrock token usage and Cost Explorer |
| `orchestrator.py` | CLI orchestrator (S3 + DynamoDB + Bedrock + checkpoints) |
| `docs/` | Architecture, decisions, build status, corporate deployment |

## Runtime Model

Default: `us.anthropic.claude-sonnet-4-6` (inference profile)
The active Claude Code session uses: `us.anthropic.claude-opus-4-6-v1[1m]`

These are different — do not conflate them. The webapp uses Sonnet for cost efficiency.
The model is configurable in `agent/model.py` via `ModelConfig`.

## Known Constraints

- **Claude Fable 5**: inference profile ACTIVE but invocation returns "unavailable" — external AWS limitation
- **provider_data_share**: enabled on this account for Fable experiment — NOT acceptable for production with real client data
- **Cost Explorer**: data lags 24-48h behind CloudWatch metrics
- **Webapp state**: in-memory (restarting loses run history) — production uses DynamoDB
- **No CI/CD**: deploys are manual via CLI

## Principles

1. Deterministic tools vs. LLM reasoning — never mix them
2. Run isolation by construction (client_id/run_id prefix in all storage paths)
3. Prompts are NOT a security boundary
4. Methodology guides but doesn't cage the agent
5. Infrastructure as code (CloudFormation)
6. No real client data, no credentials in repo
7. Feedback from consultant checkpoints feeds into subsequent LLM calls

## Tests

```bash
pytest -v                          # All unit/integration (no AWS)
python run_local.py --use-bedrock  # Local with Bedrock narrative
python orchestrator.py run --auto-approve  # Full AWS (S3+DDB+Bedrock)
python scripts/bedrock_usage.py --hours 24 # Token usage report
```

## Corporate Deployment

See `docs/corporate-deployment-architecture.md` for the target model:
- Engineer uses Claude Code on EC2 via SSM (no console access)
- DevOps owns infra (VPC, IAM roles, CI/CD pipeline)
- Consultants access via App Runner behind corporate SSO
- Production agent runs on AgentCore Runtime
- All changes flow through Git → PR → CI/CD → deploy
