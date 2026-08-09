# AWS walkthrough

Everything this project runs on AWS, in the order it makes sense to meet it —
what each piece is, why it exists, and the command that proves it.

Every output quoted below is from a run that actually executed. Nothing here
is illustrative.

**Conventions.** Region `us-east-1`, profile `intelligence-dev`, resources
named `intelligence-engine-dev-*` and tagged `Application=intelligence-engine`.
No account identifier appears in this repository; every command resolves names
from CloudFormation outputs.

---

## Contents

1. [The shape in one picture](#1-the-shape-in-one-picture)
2. [Prerequisites](#2-prerequisites)
3. [Deploy from nothing](#3-deploy-from-nothing)
4. [The data plane and its medallion tiers](#4-the-data-plane-and-its-medallion-tiers)
5. [The connective thread, end to end](#5-the-connective-thread-end-to-end)
6. [The consultant console](#6-the-consultant-console)
7. [The engineer console](#7-the-engineer-console)
8. [The workflow harness](#8-the-workflow-harness)
9. [Governance: gates, stewardship, audit](#9-governance-gates-stewardship-audit)
10. [Databricks over the same files](#10-databricks-over-the-same-files)
11. [Verify everything](#11-verify-everything)
12. [What it costs, and how to stop it](#12-what-it-costs-and-how-to-stop-it)

---

## 1. The shape in one picture

```
                     ┌──────────────────────── consultant ─────┐
                     │  App Runner + Cognito                    │
                     │  / consultant console  /engineer console │
                     └───────────────┬──────────────────────────┘
                                     │ Bedrock, S3, DynamoDB
                                     ▼
  ┌──────────────────────── governed data plane ────────────────────────┐
  │                                                                      │
  │  landing/          L1 source        raw synthetic drops              │
  │      │  conformance  (gates: permissibility, anonymisation)          │
  │      ▼                                                              │
  │  foundational/     L2 conformed     identifiers dropped, pseudonymous│
  │      │  product builder                                             │
  │      ▼                                                              │
  │  derived/          L3 products      aggregate-only, owner + lineage  │
  │      │  product builder                                             │
  │      ▼                                                              │
  │  contextualized/   L4 retrieval     vectors + typed graph            │
  │      │                                                              │
  │      ▼  governed tool (typed, read-only, logged)                    │
  │                                                                      │
  │  vault             sensitive annex  KMS key, ZERO decrypt grants     │
  │  stewardship/      gate events + tool calls, append-only             │
  └──────────────────────────────────────────────────────────────────────┘
                                     ▲
  ┌──────────────────────── build plane ──────────────────────────┐
  │  Step Functions harness → CodeBuild stages → approvals        │
  │  ECR stage image (python + node + playwright + scipy stack)   │
  │  CloudTrail with data events over foundational, derived, vault│
  └───────────────────────────────────────────────────────────────┘
```

Two planes. The **service plane** is what a consultant touches; the **build
plane** is where episodic work runs. They meet at the data plane, and every
crossing is an enumerated IAM grant.

---

## 2. Prerequisites

```bash
aws sso login --profile intelligence-dev
pip install -e ".[dev]"
pytest -q                       # 72 tests, no AWS needed
```

If the browser callback does not land, `--use-device-code` avoids the local
redirect entirely.

---

## 3. Deploy from nothing

Stacks are ordered by dependency. `deploy.sh` handles the base set:

```bash
./infrastructure/deploy.sh                 # storage, state, observability, dataplane
./infrastructure/deploy.sh --with-app      # + ECR, image build, App Runner, Cognito
./infrastructure/deploy.sh --with-workbench
```

The governance, workflow and LLM-control stacks deploy directly:

```bash
for s in governance llm-controls workflow author-seat; do
  aws cloudformation deploy --profile intelligence-dev --region us-east-1 \
    --stack-name intelligence-engine-dev-$s \
    --template-file infrastructure/cloudformation/$s.yaml \
    --capabilities CAPABILITY_NAMED_IAM --parameter-overrides Environment=dev
done
```

Then write the one parameter every stage resolves from:

```bash
python scripts/set_stage_config.py --profile intelligence-dev
```

> **Two stacks need two passes.** `app` deploys, yields its App Runner URL,
> then redeploys with that URL as the Cognito callback — the outputs are named
> `CallbackUrlToSet` for exactly this reason. `databricks-access` needs a
> placeholder external id first, then the real one plus `EnableSelfAssume`,
> because a role cannot name itself in its own trust policy while being
> created.

**No Docker required.** If Docker is running the image builds locally;
otherwise a CloudFormation-managed CodeBuild project builds it inside AWS from
committed state (`git archive` of `HEAD`), so every image maps to a checkout-able
commit.

```bash
./infrastructure/remote_build.sh --stage-image   # prints tag and digest
```

---

## 4. The data plane and its medallion tiers

Five domains, each its own bucket so the boundary is IAM-enforceable rather
than a prefix convention. Inside the lakehouse, three medallion tiers.

| Tier | Location | Schema | Who may write | Who may read |
|---|---|---|---|---|
| L1 source | `landing/` | — | vendor delivery (write-only) | conformance |
| L2 foundational | `foundational/` | `ie_dev_foundational` | conformance only | analytical stages |
| L3 derived | `derived/` | `ie_dev_derived` | product builder | consumers, tools |
| L4 contextualized | `contextualized/` | `ie_dev_contextualized` | product builder | retrieval stages |
| Sensitive annex | vault bucket | — | nobody yet | **nobody** |
| Stewardship | `stewardship/` | `ie_dev_stewardship` | any stage (append) | stewards |

**Read and write are never the same grant.** `foundational-read` and
`foundational-write` are separate managed policies held by separate roles, so
a stage that consumes a tier cannot rewrite it. That is what makes lineage
worth trusting — a product cannot quietly edit its own source.

```bash
aws iam list-policies --scope Local --profile intelligence-dev \
  --query "Policies[?starts_with(PolicyName,'intelligence-engine-dev-')].PolicyName" --output table
```

The **vault** is the pattern worth noticing: its KMS key exists, rotation is
on, and it grants decrypt to **no principal**. The wall is built before any
data arrives. Confirm it holds nothing and permits nothing:

```bash
aws s3 ls s3://$(aws cloudformation describe-stacks --profile intelligence-dev \
  --stack-name intelligence-engine-dev-dataplane \
  --query "Stacks[0].Outputs[?OutputKey=='VaultBucketName'].OutputValue" --output text)/
```

---

## 5. The connective thread, end to end

One synthetic drop, through every tier, to one governed read. Each stage runs
in the container under the role that owns its tier.

```bash
# L1 → L2  conformance: drop identifiers, register the table
aws codebuild start-build --profile intelligence-dev \
  --project-name intelligence-engine-dev-conformance \
  --buildspec-override "$(printf 'version: 0.2\nphases:\n  build:\n    commands:\n      - cd stages\n      - python conform_to_foundational.py sterling-pharma\n')"
```

Observed output:

```
GATE NOTE  engagement_permissibility: permissibility assumed for sterling-pharma
read 500 landing records for sterling-pharma
GATE WARN  anonymization: pseudonymous, not anonymous - quasi-identifier combination retained
GATE WARN  anonymization: inferred attributes about individuals retained
wrote s3://…/foundational/profiles/client_id=sterling-pharma/part-0000.parquet (52,137 bytes, 17 columns)
created glue table ie_dev_foundational.profiles
steward log: 3 event(s) -> s3://…/stewardship/gate-events/…jsonl
steward digest published for 2 escalation(s)
CONFORM OK | 500 rows
```

Then the remaining links, each on `intelligence-engine-dev-product-builder`
(L2→L3, L3→L4) and `intelligence-engine-dev-stage-runner` (retrieval,
consumption):

| Stage | Command | Result observed |
|---|---|---|
| Derived product | `build_derived.py sterling-pharma` | `500 rows -> 37 aggregate cells`, 21 suppressed below min cell 5 |
| Retrieval structures | `build_context.py sterling-pharma` | `120 vectors \| 629 nodes, 4,558 edges` |
| Retrieval demos | `retrieve_context.py sterling-pharma` | filter `120 -> 27`, then rank; `6 bridging skills` |
| Governed tool | `governed_tool.py sterling-pharma` | `37 aggregate cell(s)`, call logged |

Run the whole thread with the helper if you prefer:

```bash
python scripts/qa_sweep.py --profile intelligence-dev   # confirms the tiers are populated
```

**What each link proves.**

- *Conformance* — identifiers are dropped **on the way in**, so the grant handed
  to downstream stages is over already-conformed data. The warnings are the
  honest part: this makes the data pseudonymous, not anonymous.
- *Derived* — a **product**, not an output file. The Glue table carries
  `ie.owner`, `ie.lineage.source_table`, `ie.lineage.built_by` and
  `ie.aggregation`, so a consumer answers "where did this come from" without
  reading the code.
- *Contextualized* — vectors with **filterable metadata beside them**, and typed
  edges (`WORKS_IN`, `HOLDS`, `HAS_SKILL`, `AT_LEVEL`).
- *Retrieval* — hybrid search **filters before it ranks** (120 → 27 candidates,
  then ANN). Graph traversal answers "which skills bridge two departments" in
  one hop — an awkward self-join with no natural key in a flat table.
- *Consumption* — the agent gets **one typed, read-only, logged tool** over the
  aggregate product. It has no route to a row about a person.

Inspect the lineage the product carries:

```bash
aws glue get-table --profile intelligence-dev \
  --database-name ie_dev_derived --name workforce_composition \
  --query "Table.Parameters" --output json
```

---

## 6. The consultant console

```
https://<ServiceUrl from the app stack>
```

Cognito allows no self-signup. Create a login once, then set a password:

```bash
aws cognito-idp admin-create-user --profile intelligence-dev \
  --user-pool-id <UserPoolId> --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

Four things worth doing deliberately:

1. Pick **Sterling Pharma** — the dataset Databricks also reads.
2. At **Checkpoint 1, request a revision.** Watch the phase regenerate with
   your feedback in the prompt.
3. **Close the browser mid-run**, come back later. The run is exactly where you
   left it, having consumed nothing while waiting.
4. Try a nonsense company — entity verification blocks it before Sonnet spend:
   ~700 tokens instead of ~63,000.

---

## 7. The engineer console

```
https://<ServiceUrl>/engineer
```

Run traces, the **exact context injected into every prompt**, the dataset
browser, and the guardrail surface.

> Guardrail editing is **read-only on deployed instances**. Hot reloading
> enforcement config from a browser leaves no diff, no review and no author,
> which contradicts the rule every other change here follows. Edit
> `guardrails/config.yaml` and redeploy.

The web view is the *observation* surface. The engineer's actual working seat
is the **workbench** — an EC2 box inside the account (SSM access only, no SSH)
with Claude Code, the AWS CLI, the Databricks CLI, and Terraform preinstalled
and the repo cloned at first boot:

```bash
aws ec2 start-instances --instance-ids <workbench-id> --profile intelligence-dev
aws ssm start-session   --target <workbench-id>       --profile intelligence-dev
# then, inside:
sudo su - ec2-user && cd /work/intelligence_architecture && claude
```

The instance role is the credential — no keys are configured or stored. What
that seat can and cannot do (notably: it cannot deploy, on purpose) is the
subject of [`access-model.md`](access-model.md).

---

## 8. The workflow harness

Step Functions Standard. Stages run as CodeBuild jobs from the stage image;
approvals suspend on `waitForTaskToken` holding **no compute, no container, no
poller** for up to seven days.

```bash
aws stepfunctions start-execution --profile intelligence-dev \
  --state-machine-arn <StateMachineArn> --name run-1 --input file://execution-input.json
```

Execution input — `stage_image` is **required**, deliberately: an execution
cannot run without naming the immutable image it ran on.

```json
{
  "run_label": "acme-q1",
  "stage_image": "<repo>@sha256:<digest>",
  "pre_approval_stages":  [{"buildspec": "version: 0.2\nphases:\n  build:\n    commands:\n      - cd stages\n      - python build_derived.py acme"}],
  "post_approval_stages": [{"buildspec": "…"}]
}
```

Approvals carry a **decision and feedback**, not a boolean:

```bash
aws stepfunctions send-task-success --task-token <token> \
  --task-output '{"decision":"revise","feedback":"Biologics pipeline is understated."}'
```

`revise` re-runs that phase with `REVISION_FEEDBACK` in the stage environment
and returns to the same checkpoint, bounded by `max_revisions`. `reject` fails
the execution. The token is in the runs table under
`run_id = wf#<execution>#<checkpoint>` — a **partition key only**, so passing
`client_id` in `--key` fails.

---

## 9. Governance: gates, stewardship, audit

**Two policy gates** sit on every path, in `stages/_governance.py`:

- `gate_engagement_permissibility` — MSA/SOW permissibility, checked **before
  an agent reads**. Pass-through today; the call site is the point, so wiring a
  contract registry later is one function, not an audit of every stage.
- `gate_anonymization` — checked **before persistence**. Refuses a direct
  identifier outright, and warns when a quasi-identifier combination or an
  inferred attribute survives.

**Stewardship** is an append-only log plus one notification path:

```bash
aws s3 ls s3://<lakehouse>/stewardship/ --recursive --profile intelligence-dev
```

Gate events land under `stewardship/gate-events/`, tool calls under
`stewardship/tool-calls/`. Escalations publish an SNS digest to a named steward
role. The grant is `PutObject` only — a stage cannot rewrite history it
dislikes.

**Audit.** One multi-region trail with **data events** over `foundational/`,
`derived/` and the vault, log-file validation on, delivered to a locked bucket
with a one-year lifecycle:

```bash
aws cloudtrail get-trail-status --profile intelligence-dev \
  --name intelligence-engine-dev --query "IsLogging"
```

This closes the gap the governance inventory found: without data events, a read
of a row-level object is not provably logged — usually the first control an
auditor asks about.

---

## 10. Databricks over the same files

Unity Catalog reads the same S3 objects **in place**. Nothing is copied.

```bash
cd infrastructure/databricks/bundle
databricks bundle deploy -t dev
databricks bundle run profile_enrichment -t dev
```

`ai_query()` does per-row LLM enrichment in one SQL statement — parallelism,
queuing and retries are the platform's problem, which is the argument for it
over a hand-rolled loop.

The number to check: average tenure **3.07 years**, identical from the Python
layer, Athena, and Databricks. Three engines, one copy of the data.

---

## 11. Verify everything

Two read-only sweeps, both non-zero on failure so they can gate a deploy.

```bash
python scripts/qa_sweep.py --profile intelligence-dev
python scripts/iac_coverage.py --profile intelligence-dev
```

`qa_sweep` checks stack health, that both consoles redirect to auth, that the
tiers are populated, that the revision loop and digest pinning are live in the
**deployed** definition, and that nothing bills while idle.

`iac_coverage` reports any project resource CloudFormation does not own — the
repository is meant to be the complete description of what runs, so anything it
lists is drift.

> A trap worth knowing: `urlopen` follows redirects, so a naive auth check sees
> the login page's 200 and reports a gated console as wide open. Inverted, the
> same bug **passes** on a console with auth removed. Both scripts refuse
> redirects and assert the 302 directly.

---

## 12. What it costs, and how to stop it

Free-tier eligible or pay-per-use throughout, with one exception.

| Choice | Instead of | Why |
|---|---|---|
| CodeBuild per stage | always-on runner | billed by the minute |
| Public subnet + SSM | NAT gateway | NAT is ~$32/month standing |
| DynamoDB on-demand | provisioned | no floor under an idle table |
| `waitForTaskToken` | polling loop | a 7-day wait costs nothing |
| Auto-stopping workbench | persistent dev box | forgetting it costs the volume only |

**App Runner is the one standing cost, roughly $5–10/month.** Remove it without
touching data:

```bash
aws cloudformation delete-stack --profile intelligence-dev \
  --stack-name intelligence-engine-dev-app
```

Stop the workbench when you are done:

```bash
aws ec2 stop-instances --profile intelligence-dev --instance-ids <InstanceId>
```

> These are published rates and architectural choices, **not a reading off this
> account's bill** — Cost Explorer is not enabled here, and enabling it is a
> billing-configuration change this project does not make on its own. Inference
> cost is visible via `python scripts/bedrock_usage.py`.

---

## Related

- [`integration-contract.md`](integration-contract.md) — the boundary between this substrate and a workload, plus the full governance status
- [`predictive-workflow-readiness.md`](predictive-workflow-readiness.md) — how this maps to an episodic report workflow
- [`architecture-report.html`](architecture-report.html) — the visual walkthrough
