# Build Status

## Current state

A working pre-engagement intelligence tool with real Bedrock inference, S3-backed
workforce datasets, enforced guardrails, and two separate consoles.

**46 tests passing.** All infrastructure CloudFormation-managed.

## Completed

1. Local thin slice — deterministic tools, chart, report template
2. Bedrock model abstraction via cross-region inference profiles
3. Storage abstraction — LocalStorage + S3Storage, one interface
4. S3 infrastructure — encrypted, versioned, TLS-only, private
5. DynamoDB run state with client_id GSI
6. Methodology-driven agent engine using the tool-use API
7. Structured JSON logging with run_id correlation
8. CLI orchestrator — full S3 + DynamoDB + Bedrock path
9. CloudWatch dashboard across Opus, Sonnet, and Haiku
10. Consultant console — 5 checkpoints, revision loops, feedback that compounds
11. Real company research (accurate headcount, revenue, segments)
12. **20 workforce datasets** — 10,000 profiles, 10,000 postings in S3
13. **Dataset query layer** — ~30 signals rendered into LLM context
14. **Guardrail engine** — 14 YAML-configured rules, hot-reloadable
15. **Engineer console** — separate blueprint, guardrail editor, dataset browser
16. `infrastructure/deploy.sh` — one command deploys every stack
17. `docs/samples/` — real output committed so the repo reads without AWS
18. `docs/architecture-report.html` — full visual walkthrough

## Deployed AWS resources

| Stack | Resource | Cost model |
|---|---|---|
| `intelligence-engine-dev-storage` | S3 bucket (artifacts + datasets) | per GB |
| `intelligence-engine-dev-state` | DynamoDB table | per request |
| `intelligence-engine-dev-observability` | CloudWatch dashboard | free |

Runtime: Bedrock on-demand — Sonnet 4.6 (engine), Haiku 4.5 (verification).

Tagged `Application=intelligence-engine`, `Environment=dev`,
`ManagedBy=cloudformation`.

## Verified end-to-end

- Guardrails block gibberish (`asdfjkl` → keyboard pattern) and prompt injection
- Preset run loads 500 profiles + 500 postings and grounds research in them
- Research returns dataset-consistent headcount
- All engineer console pages return 200
- Cross-client dataset reads fail by construction

## Commands

```bash
pytest -q
./infrastructure/deploy.sh
python scripts/data_generation/generate_all.py --profile intelligence-dev
python scripts/export_samples.py --profile intelligence-dev
python webapp/app.py
python scripts/bedrock_usage.py --profile intelligence-dev --hours 24
```

## Known limits

1. Web app run state is in-memory — DynamoDB wiring exists but only the CLI
   orchestrator uses it. Restarting loses run history.
2. Research is model knowledge, not live retrieval. Checkpoint 1 exists so a
   consultant validates it.
3. Datasets are synthetic — they demonstrate the grounding pattern, not facts
   about real companies.
4. No authentication. Local use only until SSO is added.
5. No CI/CD pipeline.
6. `provider_data_share` enabled — sandbox only.
7. Claude Fable 5 unavailable for this account (external AWS limitation).

## Next

1. Wire the web app to DynamoDB so runs survive restart
2. Containerize and deploy to App Runner behind SSO
3. CI/CD with secret scanning and staged promotion
4. AgentCore Runtime for session suspend/resume at checkpoints
5. Least-privilege IAM roles per persona
6. VPC endpoints for Bedrock, S3, DynamoDB
