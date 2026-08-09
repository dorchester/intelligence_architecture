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
| `/` | **Consultant** | Client list, progress, checkpoints, the briefing. No infrastructure. |
| `/engineer` | **Engineer** | System state, run traces, guardrail editor, dataset browser, the exact LLM context. |

Locally that is `localhost:5000`; deployed it is the App Runner URL behind Cognito.

They are separate Flask blueprints in separate modules. Consultant templates
contain zero links to the engineer console, and it can be switched off entirely
with `ENGINEER_CONSOLE=0`.

---

## Quick start

```bash
pip install -e ".[dev]"
pytest -q                    # 69 tests, no AWS needed
python webapp/app.py         # → http://localhost:5000
```

Without AWS credentials the app still runs — you can research any company by
name. The 20 preset clients and their workforce datasets require S3.

### With AWS

```bash
aws sso login --profile intelligence-dev
./infrastructure/deploy.sh                                  # base stacks
python scripts/data_generation/generate_all.py --profile intelligence-dev
python webapp/app.py
```

The app runs locally and calls Bedrock, S3, and DynamoDB with your credentials.

### Hosted on AWS

```bash
./infrastructure/deploy.sh --with-app
```

One command takes it from nothing to a working HTTPS URL: base stacks, ECR
repository, container image, App Runner service, and a Cognito user pool. The
console is then reachable from anywhere, behind a login.

**No Docker required.** If Docker is running locally the image is built there;
if not, the script falls back to a CloudFormation-managed CodeBuild project that
builds inside AWS from committed state. Force either with `--build local` or
`--build remote`.

Cognito allows no self-signup, so create your own login once:

```bash
aws cognito-idp admin-create-user --profile intelligence-dev \
  --user-pool-id <UserPoolId from the stack outputs> \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

**This is the one thing in the project with a standing monthly cost** — roughly
**$5–10** for App Runner's provisioned container memory, billed whether or not
anyone uses it. Everything else is pay-per-use and idles at zero. Tear the
hosting down without touching your data:

```bash
aws cloudformation delete-stack --stack-name intelligence-engine-dev-app \
  --profile intelligence-dev
```

### The engineer workbench

```bash
./infrastructure/deploy.sh --with-workbench
aws ssm start-session --target <InstanceId> --profile intelligence-dev
```

An EC2 instance where the engineering happens — Claude Code, the repository,
the AWS CLI, the Databricks CLI, and Terraform, all inside the account rather
than on a laptop.

- **No SSH.** No key pair exists, and the security group has **zero inbound
  rules**. Access is Session Manager over outbound HTTPS, and every session is
  recorded in CloudTrail. This is the shape a firm's DevOps team will accept:
  no console, no bastion, no open port.
- **No NAT gateway.** A public subnet with an auto-assigned IP costs cents;
  a NAT gateway would be ~$32/month for the same egress.
- **Auto-stops.** A CloudWatch alarm stops the instance after an hour of low
  CPU, so forgetting about it costs the EBS volume and nothing else.

The instance role is broader than the App Runner one — this identity builds and
deploys — but still scoped to this project's bucket, table, and repository,
with read-only access to logs and metrics.

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
  auth.py           Cognito login; inert unless deployed
guardrails/
  engine.py         rule enforcement
  config.yaml       the tuning surface
datasets/
  query.py          S3 access + signal computation
agent/              Bedrock wrapper, run context
storage/            local + S3 backends, one interface
state/              DynamoDB run lifecycle
Dockerfile          console image (single worker, non-root)
infrastructure/
  deploy.sh         deploy every stack, optionally the hosted console
  build_and_push.sh build the image locally and push to ECR
  remote_build.sh   build it in AWS instead, via CodeBuild
  cloudformation/   storage, state, observability, ecr, build, app, logging
scripts/
  data_generation/  company archetypes + generator
  bedrock_usage.py  CloudWatch tokens + Cost Explorer
  export_samples.py refresh docs/samples from S3
docs/
  architecture-report.html   full visual walkthrough
  samples/                   real output, no AWS needed
tests/                       69 tests
```

---

## AWS resources

All CloudFormation-managed, tagged `Application=intelligence-engine`.

| Stack | Resource | Cost |
|---|---|---|
| `…-dev-storage` | S3 bucket — versioned, AES-256, TLS-only, private | per GB |
| `…-dev-state` | DynamoDB — on-demand, PITR, client_id GSI | per request |
| `…-dev-observability` | CloudWatch dashboard — tokens, latency, cache, errors | free |
| `…-dev-ecr` | ECR repository — scan on push, keeps 5 images | per GB |
| `…-dev-build` | CodeBuild project — builds the image in AWS | per build (~2¢) |
| `…-dev-app` | App Runner service + Cognito user pool | **~$5–10/mo** |
| `…-dev-workbench` | Engineer EC2 reached via SSM, auto-stops when idle | ~$0.02/hr running, ~$0.80/mo stopped |
| `…-dev-dataplane` | Partitioned data domains — landing, lakehouse, artifacts, site, sensitive vault (KMS) — plus Athena workgroup and upload audit trail | per GB / per query |
| `…-dev-author-seat` | IAM identity for the authoring persona — artifact read, Bedrock, no deploys | free |
| `…-dev-llm-controls` | Per-client Bedrock attribution profiles + monthly spend alerts | free |
| `…-dev-databricks-access` | Read-only Unity Catalog role over datasets + credential parameters | free |

The first four idle at zero. `…-dev-app` is the only standing cost, and deleting
that one stack removes it without touching data or datasets.

Runtime uses Bedrock on-demand: Claude Sonnet 4.6 for the engine, Haiku 4.5 for
entity verification.

The App Runner instance role is scoped deliberately: `bedrock:InvokeModel` on
Claude models only, read/write on the one bucket, four actions on the one table,
and `DescribeUserPoolClient` on its own Cognito client. No wildcard actions —
there is a test asserting it.

No account IDs appear anywhere in this repository.

---

## Documentation

- [`docs/architecture-report.html`](docs/architecture-report.html) — full visual walkthrough
- [`docs/samples/`](docs/samples/) — real system output, readable without AWS
- [`docs/predictive-workflow-readiness.md`](docs/predictive-workflow-readiness.md) — how this substrate maps to an episodic report-generation workflow, and what remains
- [`docs/corporate-deployment-architecture.md`](docs/corporate-deployment-architecture.md) — target model for a firm where DevOps owns infrastructure
- [`docs/bedrock-usage-monitoring.md`](docs/bedrock-usage-monitoring.md) — token and cost monitoring
- [`docs/decisions.md`](docs/decisions.md) — architectural decision log
- [`docs/build-status.md`](docs/build-status.md) — milestones and known limits
- [`CLAUDE.md`](CLAUDE.md) — context for Claude Code sessions

---

## Known limits

Stated plainly:

- **Runs are durable at checkpoints.** Every run persists to DynamoDB at each
  transition, and a run waiting for approval holds no thread — it survives
  restarts and redeploys, for minutes or days, consuming nothing. Approval is
  what spawns the next phase. The one seam left: a process death *mid-phase*
  (during an LLM call) marks the run `interrupted`, preserving everything up
  to the last checkpoint. The App Runner service stays pinned to one instance
  because an executing phase is still a process-local thread.
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
