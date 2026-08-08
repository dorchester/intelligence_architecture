# Build Status

## Completed Milestones

1. **Local thin slice** — deterministic tools, chart, report template, tests (6 passing)
2. **Bedrock model abstraction** — configurable model via inference profiles, Claude Sonnet 4.6 working
3. **Storage abstraction** — LocalStorage + S3Storage with structural client/run isolation
4. **S3 infrastructure** — CloudFormation-deployed encrypted private bucket
5. **DynamoDB run state** — lifecycle tracking, checkpoint mechanism, client GSI
6. **Agent engine** — methodology-driven reasoning via tool-use API, reads playbook, invokes tools
7. **Structured logging** — JSON logs with run_id, tool calls, timing; operator console view
8. **Full orchestrator** — integrates all components with checkpoint approval gate
9. **End-to-end verification** — synthetic run produces valid HTML report in S3

## Current Deployed Architecture

- **S3 Bucket**: `intelligence-engine-dev-runs-{account_id}` (us-east-1)
- **DynamoDB Table**: `intelligence-engine-dev-runs` (us-east-1, PAY_PER_REQUEST)
- **Bedrock Model**: `us.anthropic.claude-sonnet-4-6` (inference profile)
- **CloudFormation Stacks**:
  - `intelligence-engine-dev-storage`
  - `intelligence-engine-dev-state`

## Reproducible Test Commands

```bash
# Unit/integration tests (no AWS required)
pytest -v

# Local thin slice (no AWS)
python run_local.py

# Local with Bedrock narrative (requires AWS credentials)
python run_local.py --use-bedrock

# Full AWS end-to-end (S3 + DynamoDB + Bedrock + checkpoint)
python orchestrator.py run --auto-approve

# Check run status
python orchestrator.py status <run_id>

# Download report
python orchestrator.py download <run_id>

# With structured logging
python orchestrator.py --log-file run.log run --auto-approve
```

## Next Milestone

- AgentCore Runtime deployment (when pursuing V1)
- Sandboxed dynamic code execution via Code Interpreter
- Additional methodology playbooks
- Automated evaluation dimensions
- CI/CD pipeline

## Known Issues

1. **Claude Fable 5 unavailable** — inference profile is ACTIVE but invocation returns
   "unavailable for this account". External AWS limitation; using Sonnet 4.6 instead.
2. **provider_data_share enabled** — acceptable for sandbox with synthetic data only.
   Must be reviewed/disabled before production deployment with real client data.
3. **Checkpoint blocks process** — V0 uses `input()` for approval. Production would
   use async callback or AgentCore session suspend.
4. **No CI/CD** — tests run locally only. GitHub Actions pipeline planned.
5. **No IAM scoping** — currently using AdministratorAccess. Project-specific roles
   needed before production.

## AWS Resources Created

| Resource | Name | Stack |
|----------|------|-------|
| S3 Bucket | intelligence-engine-dev-runs-{account_id} | intelligence-engine-dev-storage |
| DynamoDB Table | intelligence-engine-dev-runs | intelligence-engine-dev-state |

All resources tagged: `Application=intelligence-engine`, `Environment=dev`, `ManagedBy=cloudformation`
