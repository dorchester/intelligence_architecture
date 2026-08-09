# Integration contract

This document is the boundary between **the substrate** (this repository —
deployed infrastructure) and **the workload** (a separate, private workflow
repository that supplies stage content).

It exists because that boundary is easy to misread. Reviewing the
CloudFormation alone, it is not obvious that the orchestration harness is
already deployed and deliberately content-free: it takes buildspecs as
*runtime input*, so an inspection looking for stage commands finds none and
can conclude the harness is unbuilt. The opposite is true. The harness is
built, deployed, and has completed executions end to end. What it does not
contain — by design — is anything specific to a workload.

Everything in **Part 1** exists in the account today. Everything in
**Part 2** is the workload's responsibility. **Part 3** lists the seams where
the contract is genuinely incomplete.

---

## Part 1 — What the substrate provides

### 1.1 Stage base image

**ECR repository:** `intelligence-engine-<env>-stages`
(`StagesRepositoryUri` output of the `workflow` stack)

Built from [`infrastructure/stage-image/Dockerfile`](../infrastructure/stage-image/Dockerfile):

| Property | Value |
|---|---|
| Base | `python:3.12-slim-bookworm` (pinned — trixie renames the font packages Playwright's `--with-deps` expects) |
| Python | `boto3`, `pandas`, `pyyaml`, `jinja2` |
| Analytical | `numpy`, `scipy`, `scikit-learn`, `matplotlib` (`MPLBACKEND=Agg`), `openpyxl`, `pyarrow` |
| Node | `nodejs`, `npm`, `playwright@1.49.1` |
| Browser | Chromium, at `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright` |
| User | non-root `stage` (uid 10002) |
| Workdir | `/workspace` (writable) |

Chromium is present so a render check can be a **blocking** gate — a stage can
load generated HTML in a real browser and fail the build if it does not render.

The workload layers its own code on top of this image, or installs at stage
runtime. The base image intentionally contains no pipeline code.

### 1.2 Stage runner

**CodeBuild project:** `intelligence-engine-<env>-stage-runner`

- Image: the stages repository at `:latest`, pulled with `SERVICE_ROLE` credentials
- Compute: `BUILD_GENERAL1_SMALL`, Linux
- Timeout: **30 minutes per stage**
- Source: the packaged repository at `s3://<bucket>/build/source.zip`, so a
  buildspec is a *command* rather than a whole program inlined into the
  execution input. The harness owns which command runs; the repository owns
  what it does. The default buildspec fails deliberately with `exit 1`.

**Role:** `intelligence-engine-<env>-stage-runner`. The complete grant set:

| Grant | Scope |
|---|---|
| `s3:GetObject` | `<lakehouse>/silver/*` only (via `…-silver-read`) |
| `s3:ListBucket` | lakehouse bucket |
| `s3:PutObject`, `s3:GetObject` | `<artifacts>/*` (via `…-artifacts-publish`) |
| `bedrock:InvokeModel`, `…WithResponseStream` | `us.anthropic.*` inference profiles, `application-inference-profile/*`, `foundation-model/anthropic.*` |
| `ssm:GetParameter(s)`, `GetParametersByPath` | `/intelligence-engine/<env>/stages/*` only |
| `kms:Decrypt` | only via `ssm.<region>.amazonaws.com` (`kms:ViaService` condition) |
| `ecr:*` (pull only) | the stages repository |
| `logs:*` | `/aws/codebuild/intelligence-engine-<env>-stage*` |

Row-level silver access is an **enumerated grant**, not blanket access — this
is what makes the data boundary an IAM property rather than a prompt
instruction. Note what is absent: no vault, no DynamoDB, no bucket-wide
access (see Part 3).

### 1.7 Stage configuration and credentials

Stages resolve configuration from **one enumerated SSM path**:

```
/intelligence-engine/<env>/stages/*
```

`SecureString` works — the role carries a `kms:Decrypt` grant conditioned on
`kms:ViaService = ssm.<region>.amazonaws.com`, so the key is usable through
SSM and nowhere else.

```python
import boto3, json
cfg = json.loads(boto3.client("ssm").get_parameter(
    Name="/intelligence-engine/dev/stages/demo-config",
    WithDecryption=True)["Parameter"]["Value"])
```

Granting the namespace is an infrastructure decision and is done. **What is
permitted to go into it** — which vendor credentials, under which licence —
is a separate decision that has not been made. The path reaching nothing
sensitive today is the point: stages can be written and tested against real
config now, and adding a credential later needs no harness redeploy.

Storage topology belongs here too. Absolute local paths must resolve from this
namespace rather than from a developer filesystem.

### 1.3 Orchestration

**State machine:** `intelligence-engine-<env>-report-build` (Standard)

```
PreApprovalStages ─► MidpointApproval ─► PostApprovalStages ─► FinalApproval ─► Done
   (Map, seq)         (zero compute)        (Map, seq)          (zero compute)
```

**Execution input — the contract's core shape:**

```json
{
  "run_label": "acme-2026-q1",
  "pre_approval_stages": [
    { "buildspec": "version: 0.2\nphases:\n  build:\n    commands:\n      - python pipeline/01_verify.py acme" },
    { "buildspec": "version: 0.2\nphases:\n  build:\n    commands:\n      - python pipeline/02_gather.py acme" }
  ],
  "post_approval_stages": [
    { "buildspec": "version: 0.2\nphases:\n  build:\n    commands:\n      - python pipeline/09_render.py acme" }
  ]
}
```

All three keys are **required**. `run_label` is referenced as `$.run_label`
with no default; omitting it fails the execution. Each array element must be
an object with a `buildspec` string.

`MaxConcurrency: 1` — stages within a phase run **sequentially**. Parallelism
inside a stage (e.g. concurrent model calls) is the workload's own business
and is where quota limits bite.

**The harness does not define a stage command convention.** `python
pipeline/<stage>.py <slug>` is a workload choice; the substrate accepts any
buildspec. This is deliberate — it keeps workload semantics out of
infrastructure.

### 1.4 Approvals

Both checkpoints use `lambda:invoke.waitForTaskToken`. A suspended execution
holds **no compute, no container, no poller**, for up to **7 days**
(`TimeoutSeconds: 604800`) per checkpoint.

The approval Lambda (`intelligence-engine-<env>-wf-approval`) writes the token
to the runs table:

| Attribute | Value |
|---|---|
| `run_id` | `wf#<execution-name>#<checkpoint>` — **the sole key attribute** |
| `client_id` | `workflow` (an attribute, *not* part of the key) |
| `stage` | `waiting_for_approval` |
| `checkpoint` | `midpoint_review` or `final_review` |
| `task_token` | the token to resume with |

The runs table has a partition key only. Passing `client_id` in the `--key`
argument fails with `ValidationException: The provided key element does not
match the schema` — an easy mistake, because `client_id` is present on every
item and indexed by a GSI.

**The approval payload carries a decision and feedback, not a boolean.** The
engine's actual interaction is revise-with-feedback, so the harness models the
same thing:

```bash
aws dynamodb get-item --table-name intelligence-engine-<env>-runs \
  --key '{"run_id":{"S":"wf#<execution>#midpoint_review"}}'

# continue
aws stepfunctions send-task-success --task-token <token> \
  --task-output '{"decision":"approve","feedback":""}'

# re-run the phase with direction
aws stepfunctions send-task-success --task-token <token> \
  --task-output '{"decision":"revise","feedback":"Biologics pipeline is understated."}'

# stop the build
aws stepfunctions send-task-success --task-token <token> \
  --task-output '{"decision":"reject","feedback":"Wrong client."}'
```

Both fields are **required**. On `revise` the state machine loops that phase's
stage Map again with `REVISION_FEEDBACK` set in the stage environment, then
returns to the same approval — bounded by `max_revisions` (default 2), after
which the build proceeds rather than looping forever. `reject` fails the
execution with `RejectedByReviewer`.

Prefer reading the token programmatically over echoing it — anyone holding it
can approve.

### 1.4a Conformance is a separate identity

`intelligence-engine-<env>-conformance` is the only role holding
`silver-write` and `catalog-write`. It reads landing records, drops direct
identifiers, writes parquet under `silver/`, and registers the Glue partition.

This split is the point: a stage that can read microdata **cannot rewrite
it**. Widening the stage runner instead would have made "read-only"
meaningless.

### 1.5a Prompt caching has a threshold, and it is not the documented one

Marking a shared prefix `cache_control` is how batched extraction becomes
affordable — the prefix is billed once at write cost and then at roughly a
tenth. A prefix below the model's minimum is **accepted and silently
ignored**: no error, no warning, `usage` simply reports zero.

Measured on this account, Haiku 4.5 through an application inference profile:

| Prefix | Result |
|---|---|
| 2,233 tokens | no cache |
| 4,082 tokens | no cache |
| 4,887 tokens | **cached** (write, then read on the next call) |

**The real boundary is 4,096 tokens — twice the published 2,048 for Haiku.**
A prompt sized against the documentation looks cached, bills in full, and on
a ten-call batch is roughly a 10× overspend that nothing surfaces.

`stages/_bedrock.py` warns when a prefix is marked cacheable and falls below
the threshold. Re-measure when changing model or region rather than trusting
either the docs or this table.

### 1.5 LLM access

Application inference profiles wrap the foundation models so usage is
attributable per client:

- `intelligence-engine-<env>-client-demo-haiku` — Haiku 4.5
- `intelligence-engine-<env>-client-demo-sonnet` — Sonnet 4.6

Invoke **through the profile ARN**, not the bare model id — that is what makes
per-client cost attribution work. The stage-runner role already permits
`application-inference-profile/*`, so additional per-client profiles need no
IAM change.

Monthly spend alerting is configured in the `llm-controls` stack.

### 1.6 Golden replay

**CodeBuild project:** `intelligence-engine-<env>-golden-replay`

- Source: `s3://<project-bucket>/build/source.zip`
- Buildspec (**fixed, in-template**): `pip install -q -e ".[dev]"` then `pytest -q`
- Image: `aws/codebuild/amazonlinux2-x86_64-standard:5.0`
- Role grants: `s3:GetObject` on `<bucket>/build/*` and its own log group — nothing else

In-account by construction, so replay can touch data hosted runners must
never see.

---

## Part 2 — What the workload supplies

1. **Stage code**, layered onto the base image or installed at stage runtime.
2. **Buildspecs**, one per stage, passed in the execution input (§1.3).
3. **Env-driven storage paths.** Any absolute local path (e.g. a synced
   OneDrive location) must become an env-driven governed-storage location.
   Container filesystems do not have them, and this fails at the first stage.
4. **Bedrock-routed model calls.** Direct vendor-SDK clients with API keys
   must become Bedrock calls through the profile ARNs in §1.5. Prompt caching
   (`cache_control`) belongs in that same wrapper.
5. **Aggregates-only validators.** IAM grants access to a *prefix*; it cannot
   inspect the aggregation level of what a stage writes. If output must be
   aggregates-only, that is enforced by workload code at the QA gate. The
   substrate cannot do it.
6. **A `pytest`-compatible replay entrypoint** packaged into
   `build/source.zip`, or a buildspec change to §1.6.
7. **Machine-generated publish inputs**, so publishing consumes run artifacts
   rather than hand edits.

Items 3, 4 and 5 have **no infrastructure dependency** — they can proceed
before any outstanding decision lands.

---

## Part 3 — Known gaps in this contract

Stated plainly, because each will surface as a concrete failure.

### 3.1 No Secrets Manager, and no credentials yet

The SSM namespace in §1.7 covers configuration and credentials. Secrets
Manager is deliberately **not** granted — one mechanism is easier to audit
than two, and rotation is not yet a requirement.

What remains open is not infrastructure: **no vendor credential has been
placed in the namespace**, because which vendors are in scope is a licensing
question. A stage depending on one will fail until that decision lands and the
parameter is written.

### 3.2 Stages cannot write run state

No DynamoDB grant. Stages communicate through S3 artifacts and build exit
codes only. Intentional (it keeps the runs table owned by the approval path),
but it means a stage cannot post progress.

### 3.3 Resolved — executions pin an image digest

`stage_image` is now a **required** field in the execution input and is passed
to every stage as `ImageOverride`. There is deliberately no default: an
execution cannot run without naming the image it ran on.

```json
"stage_image": "<account>.dkr.ecr.<region>.amazonaws.com/intelligence-engine-dev-stages@sha256:<digest>"
```

`remote_build.sh --stage-image` prints the digest line after the tag line, so
the value is available at build time. A tag can be moved after the fact; a
digest is the image's commit-equivalent, and it restores end to end the
provenance the `git archive` source packaging gives the console build.

### 3.4 Two checkpoint *positions*, each revisable

`midpoint_review` and `final_review` remain structural positions. Each can now
loop with feedback, so the interaction is right even though the count is not:
a playbook with nine checkpoints still has to collapse them into two
positions, and which ones collapse into which is a workload decision that has
not been made.

### 3.5 Approval is CLI plus IAM

There is no console UI and no screenshot context at the approval moment.
Approving means reading a token from DynamoDB and calling `send-task-success`.
Anyone with the token can approve; authorization is IAM on the state machine.

### 3.6 Stage egress is open — a decision, not an oversight

CodeBuild stages run with default internet access. Recording the decision
explicitly, because silence here reads as an accident:

**Accepted for this environment.** Confining stages to a VPC means private
subnets plus either a NAT gateway (~$32/month standing, against a design whose
entire cost story is scale-to-zero) or a set of interface endpoints for S3,
ECR, SSM, KMS, Logs, Bedrock and STS — roughly $7/month each, so comparable
cost and materially more to maintain.

What makes it tolerable is that egress is not the control doing the work here.
The data boundary is enforced by IAM: a stage can only read `silver/*`, and it
cannot reach the vault at all. An exfiltration path would need credentials the
role does not hold.

**What would change the answer:** real client microdata in silver, or a
vendor credential landing in the SSM namespace. Either makes open egress the
weakest link, and at that point the interface-endpoint variant — not NAT — is
the right build. It is a day of work, not a redesign, and the enumerated
grants already name every service the endpoint list would need.

### 3.7 Architecture is split

The workbench is ARM (Graviton `t4g`); CodeBuild builds x86_64. Stage images
must be built for the architecture that will run them — CodeBuild — not the
one they were authored on.

---

## Two-pass deploys

Two stacks cannot reach a working state in a single pass. Any CI/CD pipeline
must encode both.

**`app`** — deploy, read the App Runner URL from outputs, redeploy with that
URL as the Cognito OAuth callback and logout URL. The outputs are named
`CallbackUrlToSet` / `LogoutUrlToSet` for exactly this reason.

**`databricks-access`** — first pass with a placeholder `ExternalId` that is
deliberately unusable; create the Unity Catalog storage credential; second
pass with the real external id and `EnableSelfAssume=true`, because an IAM
role cannot name itself in its own trust policy while being created.

---

## Verified end to end

This contract is not a design document. Execution `contract-proof-2` ran the
whole path on the deployed harness and **SUCCEEDED**:

| Step | Evidence from the build logs |
|---|---|
| SSM `SecureString` read + decrypt | `SSM SecureString resolved, keys: ['artifacts_bucket', 'model_profile_arn']` |
| Bedrock via application inference profile | `Bedrock via application inference profile: OK` |
| Artifact published to the artifacts domain | `artifact published, bytes: 593` |
| Zero-compute suspend at `midpoint_review` | token recorded in the runs table; resumed via `send-task-success` |
| Artifact retrieved in a later stage | `artifact retrieved, bytes: 593` |
| **Blocking render gate in real Chromium** | `RENDER GATE PASSED \| headline: Workflow harness contract proof \| body chars: 416` |
| Second suspend at `final_review`, then completion | execution `SUCCEEDED` |

The render gate is genuinely blocking: the stage exits non-zero on an empty
headline or a short body, which fails the CodeBuild stage and the execution
with it.

**The data path is proven too.** The lakehouse was empty until conformance
ran — the `silver-read` grant pointed at nothing:

| Step | Evidence |
|---|---|
| Landing read, identifiers dropped | `dropped direct identifiers: ['first_name', 'last_name', 'full_name', 'headline']` |
| Conformed parquet written | `silver/profiles/client_id=sterling-pharma/part-0000.parquet` (52,137 bytes, 17 columns) |
| Catalogue registered | `created glue table intelligence_engine_dev.profiles_silver` + partition |
| Queryable | Athena `SUCCEEDED` — 500 rows grouped by seniority |
| Read back **through `silver-read`** | `read 500 conformed rows through the silver-read grant` |
| Statistics inside the build budget | IPF rake converged in 9 iterations; effective *n* 412 of 500 (82.4% design efficiency); **8.9 s** against a 30-minute timeout |

The raked mean tenure moves 3.074y → 2.658y, and the unweighted 3.074
reproduces what the Python layer and Databricks both compute from the same
files — three engines, one number.

Two failures found by running it — both now fixed and worth knowing about:

- The **first attempt failed** because the SSM value was written through a
  Windows shell that stripped the inner double quotes, so `json.loads` threw.
  Write structured parameter values with an SDK, not a shell argument.
- The approval lookup initially used a two-attribute key. The runs table has a
  partition key only (§1.4).

## Quick verification

Confirm the substrate is live before debugging a workload problem:

```bash
aws stepfunctions describe-state-machine \
  --state-machine-arn <StateMachineArn from workflow stack outputs>

aws ecr describe-images --repository-name intelligence-engine-dev-stages

aws codebuild batch-get-projects \
  --names intelligence-engine-dev-stage-runner intelligence-engine-dev-golden-replay
```

A successful prior execution is the strongest evidence the harness works;
`aws stepfunctions list-executions` will show it.
