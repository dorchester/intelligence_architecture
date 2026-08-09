# CLAUDE.md — Intelligence Engine

## What this is

A pre-engagement intelligence tool for management consultants (organization,
workforce, change). Enter a company → verify it's real → load workforce dataset
→ 4 LLM phases → HTML briefing, with 5 consultant checkpoints throughout.

## Quick start

```bash
pip install -e ".[dev]"
pytest -q                          # 70 tests, no AWS
python webapp/app.py               # consultant → :5000, engineer → :5000/engineer
ENGINEER_CONSOLE=0 python webapp/app.py   # consultant only
```

## AWS

```bash
aws sso login --profile intelligence-dev   # expires often; refresh first
aws sts get-caller-identity --profile intelligence-dev
./infrastructure/deploy.sh                 # base stacks
./infrastructure/deploy.sh --with-app      # + hosted console (costs ~$5-10/mo)
```

Region `us-east-1`. Stacks: `intelligence-engine-dev-{storage,state,
observability,ecr,build,app,workbench,dataplane,author-seat,llm-controls,
databricks-access,workflow}`.

Workflow harness: state machine `intelligence-engine-dev-report-build` -
stages run in CodeBuild from the `-stages` ECR image (python+node+playwright),
approvals are waitForTaskToken records in the runs table, approved with
`aws stepfunctions send-task-success`. Golden replay:
`aws codebuild start-build --project-name intelligence-engine-dev-golden-replay`
(source refreshed by remote_build.sh or a manual s3 cp of `git archive HEAD`).

Workbench: `aws ssm start-session --target <InstanceId from workbench stack>`.
Databricks account-level IaC is Terraform in `infrastructure/databricks/`,
run from the workbench via `apply.sh` (credentials come from SSM, never files).
See `docs/predictive-workflow-readiness.md` for the gap map and open
decision gates.

`--with-app` builds the image (local Docker, or CodeBuild when Docker is
absent), creates App Runner + Cognito, then re-deploys the app stack a second
time to bind Cognito's callback to the service URL — that second pass is not a
bug, the URL does not exist until the service does.

**Only `-app` has a standing cost.** Delete that stack to stop it; data survives.

**Datasets are already generated and in S3** — 20 companies, 10,000 profiles,
10,000 postings. Regenerate only if needed:
```bash
python scripts/data_generation/generate_all.py --profile intelligence-dev
```

## Architecture

```
webapp/app.py       consultant console + orchestration (4 LLM phases, 5 checkpoints)
webapp/engineer.py  engineer console — separate Blueprint at /engineer
webapp/runtime.py   shared state (runs dict, guardrails, AWS accessors)
webapp/run_store.py DynamoDB run persistence - checkpoint waits survive restarts
webapp/auth.py      Cognito login — inert unless COGNITO_* env vars are set
Dockerfile          console image; single worker on purpose (in-process runs)
guardrails/         YAML-configured rule engine, 14 rules
datasets/query.py   S3 dataset access + ~30 workforce signals
agent/              Bedrock wrapper, RunContext
storage/            local + S3 backends
state/              DynamoDB run lifecycle (used by CLI orchestrator, not webapp)
infrastructure/     deploy.sh + CloudFormation
scripts/            data generation, usage reporting, sample export
```

## Models

| Purpose | Model |
|---|---|
| Engine runtime | `us.anthropic.claude-sonnet-4-6` |
| Entity verification | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Claude Code (engineering) | whatever the session is set to — **different thing** |

Model IDs are config, never hardcoded in analytical code. See `agent/model.py`
and `webapp/runtime.py:DEFAULT_MODEL`.

## Key design decisions

1. **Client isolation is structural.** Storage and dataset calls take
   `client_id`/`run_id` as required args; keys are built from them. No code path
   constructs a key without them. Tests assert cross-client reads fail.
2. **Guardrails are config, not code.** `guardrails/config.yaml` holds every
   rule's enabled flag, severity (block/warn/log), and parameters. The engineer
   console edits and hot-reloads it.
3. **Revision loops, not rejection.** Each checkpoint is a `while True` — request
   a revision and that phase re-runs with the feedback. Bounded by
   `max_revisions_per_checkpoint`.
4. **Feedback compounds.** Checkpoint N's feedback enters phases N+1 onward and
   is written to `feedback_log.json`.
5. **Two consoles, two blueprints.** Consultant templates have zero engineer
   links. Engineer console is disableable.
6. **Datasets ground the LLM.** `DatasetSummary.to_agent_context()` renders
   measured signals into every prompt so the briefing cites figures rather than
   recalling them.

## Gotchas hit before

- **Windows Unicode crash.** Background threads died silently on emoji in LLM
  output (cp1252). Fixed with `_safe_str()` + top-level try/except in
  `_execute_run`. Don't remove those.
- **Truncated JSON from Bedrock.** A single large template request exceeded
  `max_tokens` and returned unparseable JSON. Split into two calls
  (`generate_departments`, `generate_posting_categories`) with `_llm_json()`
  retrying at escalating token limits.
- **Partial trailing quarter.** Hiring velocity compared the newest quarter,
  which is partial, showing a false "-67% slowing". Now compares only complete
  quarters (`quarters[1:-1]`).
- **SSO expires constantly.** If anything AWS fails, check credentials first.
- **CloudFormation rejects YAML anchors** (`&x`/`*x`) even though local YAML
  parsers accept them — templates must expand everything.
- **CFN "EarlyValidation" hook failures carry no detail.** Call the service
  API directly with the same properties to get the real error (that is how
  the Bedrock description-regex rejection was found).
- **Bedrock name/description fields** reject consecutive separators
  (pattern `([0-9a-zA-Z:.][ _-]?)+`) — no " - ".
- **IAM self-trust needs two passes**: a role cannot name itself as a
  principal while being created. `databricks-access.yaml` gates it behind
  `EnableSelfAssume=true` on the second deploy.
- **No profile in the container.** `AWS_PROFILE_NAME` is unset when deployed so
  boto3 falls through to the instance role. Never pass `profile_name` directly —
  go through `runtime._session()` or accept `profile: str | None`.
- **App Runner must stay at one instance.** Run state and the phase threads are
  in-process. `MaxSize: 1` in `app.yaml` is load-bearing, and a test asserts it.

## Testing

```bash
pytest -q                                              # all
pytest tests/test_guardrails.py -q                     # 29 guardrail tests
pytest tests/test_deployment.py -q                     # templates, IAM, container
python scripts/bedrock_usage.py --profile intelligence-dev --hours 24
```

## Known limits

- Runs are durable at checkpoints (DynamoDB); mid-phase interruption marks the
  run interrupted rather than resuming the phase
- Research is model knowledge, not live retrieval
- Datasets are synthetic
- No auth, no CI/CD
- `provider_data_share` enabled on the account — sandbox only, review before
  production

## For GitHub viewers without AWS

`docs/architecture-report.html` is the full visual walkthrough.
`docs/samples/` holds real system output including
`sample_agent_context.txt` — the exact text the LLM receives.

## Corporate deployment

`docs/corporate-deployment-architecture.md` — engineer uses Claude Code on an
EC2 instance via SSM (no console), DevOps owns IaC, consultants reach App Runner
behind SSO, production agent on AgentCore Runtime.
