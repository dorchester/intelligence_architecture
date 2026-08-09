# Access model

Who can do what, and — more importantly — who deliberately cannot.

Everything here is enforced in IAM, deployed from the templates in
`infrastructure/cloudformation/`, and verifiable with
`python scripts/iac_coverage.py`. Nothing below is a convention or a
prompt-level rule.

---

## The one rule everything follows

> **No identity inside the system can change the system's boundaries.**

Every runtime identity — the console, the stage runner, the workbench, the
Databricks connection — holds an enumerated list of permissions and has **no
ability to modify CloudFormation stacks, IAM roles, or IAM policies.** The
reason is escalation: an identity that can rewrite IAM can grant itself
anything, so its *real* permission set is "everything, eventually." Keeping
`iam:*` and `cloudformation:CreateStack/UpdateStack` out of every runtime role
means the permission tables below are the truth, not a starting point.

The practical consequence people notice first: **the engineer workbench cannot
deploy.** That is not a gap — it is the design. The workbench seat can change
*code* (edit stages, rebuild images, rerun the pipeline); only the deployer
identity can change *infrastructure*, and infrastructure changes ride through
git where they leave a diff and an author.

## Who deploys, then?

| Identity | What it is | How it deploys |
|---|---|---|
| **Human operator** (IAM Identity Center SSO, `AdministratorAccess` permission set) | The only privileged identity. Short-lived credentials via `aws sso login`; no long-lived keys exist anywhere. | `./infrastructure/deploy.sh` from a checkout of this repo |

That is the complete list today. A single-account dev build with one operator
does not need more, and every additional deployer is additional escalation
surface. When this grows past one person, the intended next step is **not**
handing more humans admin — it is a CI/CD deployer role (e.g. GitHub Actions
OIDC → a role that may only run CloudFormation on `intelligence-engine-*`
stacks), so deploys become merge-gated and the human admin becomes the
break-glass path rather than the daily one.

---

## Runtime identities

Twelve roles exist, all named `intelligence-engine-dev-*`, all defined in this
repo. None can touch IAM or CloudFormation. Grouped by what they are for:

### People-facing seats

| Role | Assumed by | Can | Deliberately cannot |
|---|---|---|---|
| `workbench` | EC2 (the engineer's box, reached via SSM only — no SSH exists) | Invoke Bedrock; read/write the project bucket and run table; push images to ECR; start CodeBuild builds; read stacks, logs, metrics | Deploy or modify any stack; touch IAM; touch billing; touch non-project resources |
| `author` | EC2 (a second, narrower seat from `author-seat.yaml` — the beginning of per-person seats) | A subset of the workbench grants | Same exclusions, smaller surface |

The **consultant is not an IAM identity at all.** Consultants sign in through
Cognito to the hosted console; every AWS call is made *on their behalf* by the
App Runner instance role below. They can be granted or removed without any IAM
change.

### The application

| Role | Assumed by | Can | Deliberately cannot |
|---|---|---|---|
| `apprunner-instance` | The running console (App Runner) | Invoke Bedrock through the app's inference profiles; read/write run artifacts and run state; start workflow executions | Read the lakehouse tiers directly; deploy; touch IAM |
| `apprunner-access` | App Runner service (not the app) | Pull the console image from ECR | Everything else — it exists only so App Runner can fetch the image |

### The pipeline (one role per medallion boundary)

These four are where the governance model becomes physical. Each holds a
different combination of the tier policies (next section), so **no single
pipeline identity can both read and rewrite the same tier**, and moving data
across a tier boundary always means crossing into a different role.

| Role | Runs as | Tier grants |
|---|---|---|
| `conformance` | CodeBuild | Read landing (L1) → write foundational (L2); catalog-write; stewardship-write |
| `product-builder` | CodeBuild | Read foundational (L2) → write derived (L3) and contextualized (L4); catalog-write; stewardship-write |
| `stage-runner` | CodeBuild | Read foundational/derived/contextualized; write artifacts; invoke Bedrock (Anthropic + Titan embeddings); stewardship-write |
| `golden-replay` | CodeBuild | What the replay needs to re-run the suite in-account |

### Orchestration and plumbing

| Role | Assumed by | Purpose |
|---|---|---|
| `workflow` | Step Functions | Start/observe the CodeBuild stage tasks; nothing else |
| `wf-approval` | Lambda | Record a pending approval + task token in the run table; write its own logs. ~The smallest role in the account, on purpose — it handles human-approval tokens |
| `codebuild` | CodeBuild (image build) | Build and push the console image |
| `databricks-uc` | Databricks Unity Catalog (external ID trust) | Read the lakehouse in place — zero-copy, no export, revocable by deleting one role |

---

## The tier policies (data-plane grants)

Ten customer-managed policies, granted only in the combinations above. Read
and write are **always separate policies**, so a role's data access is legible
from its attachment list alone:

| Policy | Covers |
|---|---|
| `landing-write` | L1 source drops |
| `foundational-read` / `foundational-write` | L2 (pseudonymous — identifiers dropped) |
| `derived-read` / `derived-write` | L3 (aggregate products) |
| `contextualized-read` / `contextualized-write` | L4 (vectors + graph) |
| `stewardship-write` | Append-only gate-event and tool-call log |
| `catalog-write` | Glue table registration (`ie_dev_*` databases) |
| `artifacts-publish` | Run outputs for the console |

Notable absences are load-bearing:

- **No `stewardship-read` is granted to any pipeline role** — the audit log is
  append-only from the inside.
- **No role holds any grant on the vault** (sensitive annex). Its KMS key
  currently authorizes zero decrypt principals; granting the first one is a
  human, review-gated act.
- **No role holds read+write on the same tier it consumes** — a compromised
  consumer cannot rewrite its own input to cover its tracks.

---

## The human functions, mapped onto the seats

The twelve roles above are *machine* identities. The people questions — who
governs the data, who uploads datasets, who engineers the pipelines — map onto
a deliberately small set of human functions. The design goal is that **adding
a person means attaching existing policies to a seat, never minting new
roles.**

| Function | Who / what today | How it works |
|---|---|---|
| **Deployer / account owner** | The one human admin (SSO) | Deploys stacks, grants seats, breaks glass. The only identity that can change boundaries. |
| **Data steward (governs)** | The subscriber on the stewardship SNS topic + the standing decision gates | Governing here is *approving and auditing, not operating* — so the steward is intentionally **not an IAM writer**. Their instruments are the policy gates in code (`stages/_governance.py`), the append-only stewardship log, CloudTrail, and the escalation digest. Changing what the gates enforce is a code change, which means a diff, a review, and a redeploy. |
| **Data ingester (uploads new datasets)** | The human admin, today | `landing-write` exists as a policy but is attached to **no role** — a deliberate zero. Real ingestion is one of the standing decision gates (licensing/privacy signoff per source), so the first attachment of `landing-write` should happen when a specific source clears that gate, to a specific seat. One attachment; no new roles. |
| **Pipeline engineer (builds/adjusts)** | The workbench seat (Claude Code + full toolchain, SSM-only) | Edits stages, rebuilds images, reruns the pipeline, inspects everything. Cannot deploy — infra changes go back through git to the deployer. |
| **Data scientist / analyst** | Databricks via the `databricks-uc` role | Reads the lakehouse zero-copy through Unity Catalog. Never holds write on anything. |
| **Consultant (consumes)** | Cognito user in the hosted console | Not an IAM identity at all; the app acts for them within its own scoped role. |

Why this is enough, and why it should resist growing: every end-to-end
activity — ingest → conform → build products → analyze → consume → audit — has
exactly one function responsible for it, and each function's power is a
*combination of policies that already exist*. The failure mode to avoid is
inventing a new role per person or per tool; the policy layer is the stable
vocabulary, and seats are just attachment points for it.

---

## Escalation paths, considered and closed

| Path | Why it fails |
|---|---|
| Workbench edits a stack to widen its own role | No `cloudformation:*Stack*`, no `iam:*` — the API call is denied |
| App console rewrites guardrails at runtime | Guardrail editing is read-only when deployed; config changes require a commit and redeploy |
| Stage code writes back to its input tier | The role running it does not hold that tier's write policy |
| Anything covers its tracks | CloudTrail (multi-region, log-file validation, object-level data events on foundational/derived/vault) delivers to a locked bucket the runtime roles cannot write to |
| Long-lived key theft | There are no long-lived keys: SSO for the human, instance roles for EC2, service roles for everything else |

---

## Verifying this document

The claims above are checkable, not aspirational:

```bash
# every role and policy, live
aws iam list-roles   --query "Roles[?starts_with(RoleName,'intelligence-engine-dev')].RoleName"
aws iam list-policies --scope Local \
  --query "Policies[?starts_with(PolicyName,'intelligence-engine-dev')].PolicyName"

# what a given role can actually do
aws iam list-attached-role-policies --role-name intelligence-engine-dev-stage-runner
aws iam list-role-policies          --role-name intelligence-engine-dev-workbench

# nothing exists outside the templates
python scripts/iac_coverage.py --profile intelligence-dev
```

If `iac_coverage.py` reports drift, this document is out of date — fix the
template, not the doc.
