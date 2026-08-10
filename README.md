# Intelligence Engine

**Durable, human-governed agentic analysis on AWS** — a pre-engagement
intelligence platform for management consulting, built as a working reference
for how agentic LLM systems get engineered responsibly.

[![ci](https://github.com/dorchester/intelligence_architecture/actions/workflows/ci.yml/badge.svg)](https://github.com/dorchester/intelligence_architecture/actions/workflows/ci.yml)

Enter a company. The engine verifies it's real before spending tokens, grounds
itself in measured workforce data, researches the organization through Claude
on Amazon Bedrock, identifies engagement opportunities, and produces a
briefing — pausing at five checkpoints where a consultant validates, corrects,
and redirects the work.

**📖 Documentation site: [dorchester.github.io/intelligence_architecture](https://dorchester.github.io/intelligence_architecture/)** —
start there for the visual architecture report, real system output, and the
full requirement map. Nothing in it requires AWS access.

## This is a real build, on a personal AWS account

Nothing here is a diagram of a system that might exist. Every stack in this
repository is deployed, and the claims below are quoted from logs of runs that
actually executed:

- The workflow harness has completed executions end to end — containerised
  stages, zero-compute approval suspends, a blocking Chromium render gate.
- Conformance reads landing records, drops direct identifiers, writes parquet,
  registers a Glue partition; Athena queries it; a stage reads it back through
  an IAM grant scoped to exactly that prefix.
- A raking stage converges in 9 iterations and finishes in **8.9 s** against a
  30-minute container budget.
- Databricks reads the same S3 files in place through Unity Catalog and
  computes the same average tenure — **3.07 years** from three independent
  engines, with zero data copied.

**It runs on a sandbox account, engineered to stay near free-tier
economics.** That constraint shaped the architecture rather than being
retrofitted onto it:

| Choice | Instead of | Why |
|---|---|---|
| CodeBuild per stage | An always-on runner | Billed by the minute, idles at nothing |
| Public subnet + SSM | A NAT gateway | NAT is ~$32/month standing, for egress alone |
| DynamoDB on-demand | Provisioned capacity | No floor under an idle table |
| Bedrock on-demand | Provisioned throughput | Per-token, no reservation |
| `waitForTaskToken` | A polling loop | A 7-day wait costs nothing to hold |
| Auto-stopping workbench | A persistent dev box | Forgetting it costs the volume only |

Everything above is free-tier eligible or pay-per-use except one component:
**App Runner carries a standing cost of roughly $5–10/month** for provisioned
container memory, billed whether anyone signs in or not. Deleting that one
stack removes it without touching data — see
[Hosted on AWS](#hosted-on-aws).

To be exact about the evidence: the figures here are **published AWS rates and
architectural choices, not a reading off this account's bill.** Cost Explorer
is not enabled on the sandbox and turning it on is a billing-configuration
change this project does not make on its own. Verify with your own numbers:

```bash
python scripts/bedrock_usage.py --profile intelligence-dev   # tokens + inference cost
```

What *is* measured is the shape that drives the bill — stages that exit in
seconds, waits that hold no compute, an instance that stops itself, and no
resource anywhere that bills for being idle.

The discipline is the point. An architecture that only works with a NAT
gateway and a provisioned cluster cannot be evaluated by one engineer on a
personal account, and a substrate nobody can afford to run is a substrate
nobody validates.

## Why it is technically interesting

Six properties most agent demos skip, all implemented and tested here:

1. **Durable human-in-the-loop.** Execution is segmented: each phase runs to a
   checkpoint, persists to DynamoDB, and its thread *ends*. Waits hold zero
   compute and survive restarts — proven by killing a live run mid-wait and
   approving it from a fresh process. The same property exists at pipeline
   scale via Step Functions `waitForTaskToken` (7-day zero-compute suspends).
2. **Structural client isolation.** Storage and dataset calls require
   `client_id`/`run_id`; keys are built from them; cross-client reads fail by
   construction and tests attempt them. Prompts are never the boundary.
3. **Guardrails as configuration.** Fourteen YAML rules — entity verification
   before token spend, PII blocking, revision and spend ceilings — hot-reloaded
   from an engineer console, enforced *before* inference.
4. **Grounded, not recalled.** ~30 measured workforce signals are injected into
   every prompt; an independent Databricks SQL engine reproduces the same
   figures from the same S3 files, with zero data copied.
5. **An enumerated-grant data plane.** Five S3 domains behind hard IAM walls,
   including a KMS vault with zero read grants until privacy signoff; only the
   post-QA-gate stage may publish artifacts — no-bypass is an IAM denial.
6. **Engineering inside the boundary.** An SSM-only, auto-stopping workbench
   hosts Claude Code, Terraform, and the Databricks CLI in-account; CI holds no
   credentials and scans every push for secrets; in-account golden replay
   covers what hosted runners must never touch.

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

## The six human roles

The consoles are two of six working surfaces. Every human function has a
seat with enumerated permissions, a guide with its real commands, and a
deliberate answer to "can this person block the system?" — the headlines,
consolidated from [`docs/guides/`](docs/guides/):

| Role | Seat | Headline | Critical to |
|---|---|---|---|
| **Consultant / engagement lead** ([guide](docs/guides/consultant.md)) | Hosted console (Cognito — no cloud permissions at all) | Runs reports and owns the checkpoints: approve, request bounded revisions with feedback injected into the prompts, or reject. Can close the browser mid-run and lose nothing. | **Execution** — the only human a running report ever waits for |
| **Forward-deployed engineer (FDE)** ([guide](docs/guides/fde.md)) | Workbench — EC2 + Claude Code inside the account, SSM-only, no SSH | Owns the workload: edits stages and prompts, runs them raw against synthetic data, then runs the full harness against a candidate image digest while the blessed digest serves everyone else. Regression floor: tests + in-account replay + seeded-defect eval of model behavior. | Engineering |
| **Platform engineer** ([guide](docs/guides/platform-engineer.md)) | Deployer seat (`sts:AssumeRole`) | Turns merged templates into deployed stacks through `cfn-exec` — the only channel by which boundaries change — and promotes tested stage images to "blessed." Can mutate nothing directly; rollback is one command. | Engineering |
| **Data steward** ([guide](docs/guides/data-steward.md)) | Steward seat (`sts:AssumeRole`) | The only identity that can admit data (`landing/`) or console users; the only one that can read the stewardship log and audit trail. Their policy runs as code in the gates, so a steward absence stops new admissions — never a report. | Nothing at runtime — by design |
| **Data scientist / analyst** ([guide](docs/guides/analyst.md)) | Databricks (Unity Catalog, zero-copy) | Reads every governed tier in place with no AWS credentials; holds write on nothing, so notebook freedom can't corrupt the pipeline. New products enter through reviewed stage changes, not side doors. | Nothing |
| **Account admin** ([guide](docs/guides/admin.md)) | SSO — dormant | Two jobs only: seating people (one `sts:AssumeRole` grant each) and recovery if the deploy channel itself breaks. Used on a normal day = design failure. | Nothing, by design |

Machine identities (`conformance`, `product-builder`, `stage-runner`, …) are
never accountable for anything — each is a human's work wearing
least-privilege grants. The full identity-by-identity model, including who
deliberately cannot do what, is [`docs/access-model.md`](docs/access-model.md).

---

## Quick start

```bash
pip install -e ".[dev]"
pytest -q                    # 70 tests, no AWS needed
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
  rules**. Access is Session Manager over outbound HTTPS. This is the shape a
  firm's DevOps team will accept: no console, no bastion, no open port.
  Sessions appear in CloudTrail *Event History*, which is on by default and
  keeps 90 days of management events — but **no durable trail is configured**,
  so there is no long-term retention, no data-event logging, and no log-file
  integrity validation. See [Data governance](#data-governance).
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
stages/             reference workflow stages - one per class of work
  conform_to_silver.py   landing -> governed parquet, identifiers dropped
  analyse_workforce.py   IPF raking, logistic fit, Kish effective n, chart
  enrich_profiles_llm.py batched extraction, synthesis, advisory reviewer
  _bedrock.py            caching, bounded concurrency, structured output
Dockerfile          console image (single worker, non-root)
infrastructure/
  deploy.sh         deploy every stack, optionally the hosted console
  build_and_push.sh build the image locally and push to ECR
  remote_build.sh   build it in AWS instead, via CodeBuild
  stage-image/      workflow stage base image (python + node + playwright)
  databricks/       Terraform (serverless workspace) + asset bundle
  cloudformation/   storage, state, observability, ecr, build, app,
                    dataplane, workflow, workbench, author-seat,
                    llm-controls, databricks-access, logging
scripts/
  data_generation/  company archetypes + generator
  bedrock_usage.py  CloudWatch tokens + Cost Explorer
  export_samples.py refresh docs/samples from S3
  qa_sweep.py       read-only health check over every deployed stack
docs/
  architecture-report.html   full visual walkthrough
  samples/                   real output, no AWS needed
tests/                       70 tests
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
| `…-dev-workflow` | Step Functions harness — CodeBuild stages, zero-compute approvals, stage image ECR, golden replay | per run |

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

**[dorchester.github.io/intelligence_architecture](https://dorchester.github.io/intelligence_architecture/)**
is the front door — a designed landing page over everything below.

- [`docs/aws-walkthrough.md`](docs/aws-walkthrough.md) — **everything on AWS, in the order it makes sense to meet it**: deploy from nothing, the medallion tiers, the connective thread, governance, and how to verify it all
- [`docs/architecture-report.html`](docs/architecture-report.html) — full visual walkthrough
- [`docs/samples/`](docs/samples/) — real system output, readable without AWS
- [`docs/integration-contract.md`](docs/integration-contract.md) — **the boundary between this substrate and a workload that runs on it**: exact IAM grants, execution input shape, approval mechanics, and the seams that are genuinely open
- [`docs/access-model.md`](docs/access-model.md) — **who can do what, and who deliberately cannot**: every identity, every grant, why no runtime role can deploy, and how to verify it all against live IAM
- [`docs/guides/`](docs/guides/) — **a user guide per human role**: forward-deployed engineer, platform engineer, data steward, analyst, consultant, admin — real commands for each seat's actual use cases
- [`docs/diagrams.md`](docs/diagrams.md) — **every architecture diagram as Mermaid source**: one block per activity (report run, data admission, deploy channel, release, audit, zero-copy analytics), renderable on GitHub or exportable to slides
- [`scripts/build_role_guide_docx.py`](scripts/build_role_guide_docx.py) — generates the **illustrated Word edition** of the role guides from your own deployment's console screenshots (the document itself stays internal — live screenshots expose account-specific values)
- [`docs/predictive-workflow-readiness.md`](docs/predictive-workflow-readiness.md) — how this substrate maps to an episodic report-generation workflow, and what remains
- [`docs/corporate-deployment-architecture.md`](docs/corporate-deployment-architecture.md) — target model for a firm where DevOps owns infrastructure
- [`docs/bedrock-usage-monitoring.md`](docs/bedrock-usage-monitoring.md) — token and cost monitoring
- [`docs/decisions.md`](docs/decisions.md) — architectural decision log
- [`docs/build-status.md`](docs/build-status.md) — milestones and known limits
- [`CLAUDE.md`](CLAUDE.md) — context for Claude Code sessions

---

## Data governance

Five S3 domains, all encrypted, versioned, TLS-only and public-access-blocked,
and inside the lakehouse three **medallion tiers** — `foundational/` (conformed),
`derived/` (aggregate products with owner and lineage), `contextualized/`
(vectors and a typed graph). Each tier has its own catalogue schema and its own
*pair* of read and write grants held by *different* roles, so a stage that
consumes a tier cannot rewrite it. The
sensitive vault's KMS key grants decrypt to **no principal** and holds zero
objects, pending privacy signoff.

Conformance drops direct identifiers on the way into `silver/` — verified: the
catalogued table carries no name, headline or exact age. **That makes the data
pseudonymous, not anonymous.** Department, location, title, tenure and a stable
`profile_id` remain, and that combination re-identifies in a small population.

Two policy gates sit on every path — permissibility before a read,
anonymisation before a write — and an append-only steward log records what they
found, with an SNS digest for escalations. A multi-region CloudTrail with
**data events** over the governed tiers and the vault answers "who read this
row".

**Real gaps, stated plainly:** no retention rule on the lakehouse (so no
deletion story), catalogue access is plain IAM rather than Lake Formation, and
no automated PII discovery. These are acceptable *only* because the data is
synthetic — every one becomes blocking the moment real workforce records land.

Full status, with what is measured versus assumed:
[`docs/integration-contract.md`](docs/integration-contract.md#data-governance-status).

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
- **Authentication is Cognito, not corporate SSO.** The hosted console sits
  behind a Cognito user pool with no self-signup; run locally it is
  unauthenticated. A firm deployment would federate to the corporate IdP, and
  both consoles currently share one pool — the engineer console is not
  separately grouped.
- **CI tests; it does not deploy.** GitHub Actions runs the suite and scans
  every push for secrets, holding no AWS credentials. Deployment is a deliberate
  manual step, and the in-account golden replay covers what hosted runners must
  never touch.

---

## License

MIT — see [LICENSE](LICENSE).
