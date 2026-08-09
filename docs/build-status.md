# Build Status

## Completed Milestones

1. **Local thin slice** — deterministic tools, chart, report template, 17 tests passing
2. **Bedrock model abstraction** — configurable via inference profiles, Sonnet 4.6 confirmed
3. **Storage abstraction** — LocalStorage + S3Storage with structural client/run isolation
4. **S3 infrastructure** — CloudFormation-deployed encrypted private bucket
5. **DynamoDB run state** — lifecycle tracking, checkpoint mechanism, client GSI
6. **Agent engine** — methodology-driven reasoning via Bedrock tool-use API
7. **Structured logging** — JSON logs with run_id, tool calls, timing
8. **Full CLI orchestrator** — S3 + DynamoDB + Bedrock + checkpoints (end-to-end verified)
9. **CloudWatch monitoring** — dashboard tracking all Claude models (Opus, Sonnet, Haiku)
10. **Consultant web UI** — pre-engagement intelligence tool with 5 human checkpoints
11. **Real company research** — LLM generates accurate org/workforce intelligence (not fake data)
12. **Feedback loop** — consultant feedback persisted and fed into subsequent LLM calls
13. **Engineer dashboard** — system-wide view of all runs, infrastructure, and config
14. **Corporate deployment design** — target architecture for consulting firm with DevOps/security constraints
15. **Architecture visualization** — interactive HTML report with SVG diagrams

## Current State

**Consultant experience** (`python webapp/app.py` → localhost:5000):
- Enter any real company name + optional engagement context
- Engine researches via Bedrock (real facts: employee count, revenue, segments)
- 5 checkpoints: validate research → review org analysis → prioritize opportunities → review briefing → approve delivery
- Produces professional HTML intelligence briefing
- Feedback at each checkpoint shapes subsequent analysis

**Engineer experience** (localhost:5000/engineer):
- System dashboard: all runs, model status, infrastructure cards
- Per-run inspection: raw research JSON, checkpoint history, artifacts, execution log
- Links to CloudWatch dashboard

## Deployed AWS Resources

| Resource | Name | Stack | Cost Model |
|----------|------|-------|------------|
| S3 Bucket | `intelligence-engine-dev-runs-*` | intelligence-engine-dev-storage | Pay-per-GB |
| DynamoDB Table | `intelligence-engine-dev-runs` | intelligence-engine-dev-state | Pay-per-request |
| CloudWatch Dashboard | `intelligence-engine-bedrock-usage` | intelligence-engine-dev-observability | Free |
| Bedrock (runtime) | `us.anthropic.claude-sonnet-4-6` | — | Pay-per-token |

All resources tagged: `Application=intelligence-engine`, `Environment=dev`, `ManagedBy=cloudformation`

## Reproducible Commands

```bash
# Tests (no AWS)
pytest -v

# Consultant web UI
python webapp/app.py

# CLI orchestrator (full AWS path)
python orchestrator.py run --auto-approve

# Bedrock usage last 24h
python scripts/bedrock_usage.py --profile intelligence-dev --hours 24

# Bedrock cost (24-48h delay)
python scripts/bedrock_usage.py --profile intelligence-dev --cost --month-to-date

# Deploy all infrastructure
aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
  --template-file infrastructure/cloudformation/storage.yaml \
  --stack-name intelligence-engine-dev-storage --parameter-overrides Environment=dev
# (repeat for state.yaml, observability.yaml)
```

## Known Issues

1. **Fable 5 unavailable** — AWS account limitation, using Sonnet 4.6 instead
2. **provider_data_share enabled** — sandbox only, must disable for production
3. **Webapp state is in-memory** — restarting loses run history (production uses DynamoDB)
4. **No CI/CD pipeline** — deploys are manual
5. **No containerization** — webapp not yet Dockerized for App Runner
6. **Cost Explorer data** — may not be populated yet (new account, 24-48h lag)
7. **No corporate auth** — webapp is unauthenticated (add SSO for corporate)

## Next Steps

1. Containerize webapp (Dockerfile → ECR → App Runner)
2. CI/CD pipeline (GitHub Actions → staging → prod)
3. AgentCore Runtime deployment (production agent execution)
4. Corporate IAM roles (least-privilege per persona)
5. VPC with Bedrock endpoint (corporate network controls)
6. Bedrock Guardrails (content filtering for production)
7. Additional analytical methodologies beyond workforce basics

## Session Context

- AWS profile: `intelligence-dev` (SSO, AdministratorAccess for sandbox bootstrap)
- Region: `us-east-1`
- Active Claude Code model: `us.anthropic.claude-opus-4-6-v1[1m]`
- Intelligence Engine runtime model: `us.anthropic.claude-sonnet-4-6`
- Git remote: `https://github.com/dorchester/intelligence_architecture.git`
- 15 commits on main, all pushed
