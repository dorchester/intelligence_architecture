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

1. Runs persist to DynamoDB at every transition; checkpoint waits survive
   restarts and hold no compute. Mid-phase interruptions are detected and
   marked; the phase itself is not resumable (yet).
2. Research is model knowledge, not live retrieval. Checkpoint 1 exists so a
   consultant validates it.
3. Datasets are synthetic — they demonstrate the grounding pattern, not facts
   about real companies.
4. No authentication. Local use only until SSO is added.
5. No CI/CD pipeline.
6. `provider_data_share` enabled — sandbox only.
7. Claude Fable 5 unavailable for this account (external AWS limitation).

## Hosting

The console is deployable as an App Runner service behind a Cognito login, in
one command: `./infrastructure/deploy.sh --with-app`.

| Piece | Where |
|---|---|
| Container image | `Dockerfile` — single worker, non-root, port 8080 |
| Registry | `cloudformation/ecr.yaml` — scan on push, keeps 5 images |
| Build | `cloudformation/build.yaml` — CodeBuild, so no local Docker is needed |
| Service + identity | `cloudformation/app.yaml` — App Runner, Cognito, IAM roles |

Three things are worth knowing about this deployment.

**It is pinned to one instance.** `MaxSize: 1` in the autoscaling config is
load-bearing, not conservatism. Run state and the threads executing each phase
live in the process, so a second instance would not see a run started by the
first. Moving run state to DynamoDB is what unlocks scaling, and it remains the
top item below.

**The callback URL needs two passes.** Cognito needs the App Runner URL, which
does not exist until the service is created. `deploy.sh` deploys the app stack
with a placeholder, reads the service URL, and deploys again with the real
value. Idempotent, and re-running it is harmless.

**It is the only standing cost in the project.** App Runner bills for
provisioned container memory whether or not anyone is using the service —
roughly $5–10/month. Deleting the `-app` stack removes that cost and leaves
storage, datasets, and run state untouched.

The instance role grants `bedrock:InvokeModel` on Claude models, read/write on
the single project bucket, four actions on the single table, and
`DescribeUserPoolClient` on its own Cognito client. `tests/test_deployment.py`
asserts no policy contains a wildcard action and no template contains a literal
account ID.

## Predictive workflow substrate

The engine's next consumer is an episodic report-generation workflow in a
separate private repository. `docs/predictive-workflow-readiness.md` maps its
functional requirements onto this account; the short version:

- **Deployed**: partitioned data plane with hard IAM walls and an empty
  sensitive vault; author-seat identity; per-client Bedrock attribution +
  spend alerts; Athena diagnostic SQL; audited upload trail; Databricks
  bridge (AWS side) and serverless-workspace Terraform.
- **Verified**: Haiku 4.5 applied quota is 50 req/min (vs 10,000 default) —
  increase needed before high-volume builds.
- **Built**: durable approvals — checkpoint waits persist to DynamoDB,
  survive restarts/redeploys, and hold no thread or compute; approval spawns
  the next phase. Proven live by killing the process mid-wait.
- **Open gates**: orchestration substrate, privacy signoff for the vault,
  vendor licence reviews, invocation logging.

## Next

1. Unpin the App Runner instance count (waits are durable; executing phases
   are still process-local threads)
2. CI/CD with secret scanning and staged promotion
3. AgentCore Runtime for session suspend/resume at checkpoints
4. Least-privilege IAM roles per persona
5. VPC endpoints for Bedrock, S3, DynamoDB
6. Custom domain and firm SSO federated into the Cognito pool
