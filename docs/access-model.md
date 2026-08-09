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

## Who deploys, then — and the break-glass corollary

| Identity | What it is | How it deploys |
|---|---|---|
| **Human operator** (IAM Identity Center SSO, `AdministratorAccess` permission set) | The only privileged identity. Short-lived credentials via `aws sso login`; no long-lived keys exist anywhere. | `./infrastructure/deploy.sh` from a checkout of this repo |

The rule above has a corollary that is easy to miss: **if every recurring
critical act routes through the admin, the admin is not break-glass — it is an
active operator**, and that is its own governance failure. An identity that
powerful should be *rarely used*, and rarity is only possible if the routine
acts live somewhere narrower.

So the recurring acts are delegated to named seats, and the admin's remaining
jobs are the ones that *should* be rare: deploying infrastructure changes,
seating people (granting a principal the right to assume a seat), and
break-glass. The delegation that exists today:

- **Data admission, people admission, audit reading** → the **steward seat**
  (`intelligence-engine-dev-steward`, from `steward.yaml`) — see below.
- **Everyday engineering** → the **workbench seat** — code, builds, reruns.
- **Deploys** are the one act still on the admin, and the intended successor
  is not a person: a CI/CD deployer role (GitHub Actions OIDC → CloudFormation
  on `intelligence-engine-*` stacks only), making deploys merge-gated and
  demoting the admin to genuine break-glass.

To wear the steward hat rather than the admin one for routine acts, assume the
role (a logged STS call) — most simply as a named profile:

```ini
# ~/.aws/config
[profile intelligence-steward]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-steward
source_profile = intelligence-dev
region = us-east-1
```

Seating a second person as steward later is one `sts:AssumeRole` grant on
their principal — not a new role, not a policy change.

---

## Runtime identities

Thirteen roles exist, all named `intelligence-engine-dev-*`, all defined in
this repo. None can touch IAM or CloudFormation. Grouped by what they are for:

### People-facing seats

| Role | Assumed by | Can | Deliberately cannot |
|---|---|---|---|
| `workbench` | EC2 (the AI engineer's box, reached via SSM only — no SSH exists) | Invoke Bedrock; read/write the project bucket and run table; push images to ECR; start CodeBuild builds; read stacks, logs, metrics | Deploy or modify any stack; touch IAM; touch billing; touch non-project resources |
| `steward` | A human, via `sts:AssumeRole` (max 4-hour sessions) | Admit data (`landing-write`); admit people (create/disable console users in the project Cognito pool); read the stewardship log and CloudTrail | Deploy; touch IAM; write to any lakehouse tier; invoke models; reach the vault. The steward governs — it does not operate the pipeline |
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

Eleven customer-managed policies, granted only in the combinations above. Read
and write are **always separate policies**, so a role's data access is legible
from its attachment list alone:

| Policy | Covers |
|---|---|
| `landing-write` | L1 source drops — held only by the steward seat |
| `foundational-read` / `foundational-write` | L2 (pseudonymous — identifiers dropped) |
| `derived-read` / `derived-write` | L3 (aggregate products) |
| `contextualized-read` / `contextualized-write` | L4 (vectors + graph) |
| `stewardship-write` | Append-only gate-event and tool-call log |
| `stewardship-read` | Read the log + CloudTrail lookups — held only by the steward seat |
| `catalog-write` | Glue table registration (`ie_dev_*` databases) |
| `artifacts-publish` | Run outputs for the console |

Notable absences are load-bearing:

- **No `stewardship-read` is granted to any pipeline role** — the log is
  append-only from the inside. The identities that write it cannot read it,
  and the identity that reads it (the steward) cannot write anything except
  the L1 drop zone.
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
| **Deployer / account owner** | The one human admin (SSO) | Deploys stacks, seats people, breaks glass — and by design does *nothing recurring*. The only identity that can change boundaries. |
| **Data steward (governs + admits)** | The steward seat (`sts:AssumeRole`) + the standing decision gates | Holds the three recurring acts that would otherwise make the admin an active operator: **admitting data** (`landing-write` — admission is the steward's decision, so the grant follows the accountability), **admitting people** (console user management in Cognito), and **reading the audit surface** (stewardship log, CloudTrail). Changing what the *gates* enforce remains a code change — diff, review, redeploy. |
| **AI engineer (builds/adjusts)** | The workbench seat (Claude Code + full toolchain, SSM-only) | Edits stages, rebuilds images, reruns the pipeline, inspects everything. Cannot deploy — infra changes go back through git to the deployer. |
| **Data scientist / analyst** | Databricks via the `databricks-uc` role | Reads the lakehouse zero-copy through Unity Catalog. Never holds write on anything. |
| **Consultant (consumes)** | Cognito user in the hosted console | Not an IAM identity at all; the app acts for them within its own scoped role. |

Why this is enough, and why it should resist growing: every end-to-end
activity — admit → conform → build products → analyze → consume → audit — has
exactly one function responsible for it, and each function's power is a
*combination of policies that already exist*. The failure mode to avoid is
inventing a new role per person or per tool; the policy layer is the stable
vocabulary, and seats are just attachment points for it.

### If a traditional team walked in tomorrow

The seats are named for functions, not job titles — but they map cleanly onto
a conventional data/platform team, which is the test that the model is
workable beyond an AI-centric setup:

| Traditional title | Sits at | Notes |
|---|---|---|
| Platform / DevOps engineer | Deployer (admin today, CI/CD role as successor) | Owns `infrastructure/`; the only function that changes boundaries |
| Data engineer | Workbench seat + Databricks | The conform/build stages are ordinary Python over S3 + Glue; nothing requires an agent — Claude Code on the workbench is leverage, not a prerequisite |
| AI engineer | Workbench seat | Same seat as the data engineer, different work: prompts, stages, evals, the model layer |
| Analytics engineer / BI analyst | Databricks (read-only via Unity Catalog) | SQL over the derived tier; never needs AWS credentials at all |
| Data steward / governance lead | Steward seat | Admission and audit, no pipeline power |
| Security / compliance auditor | Steward seat (read side) or a read-only SSO permission set | Everything they need is readable: CloudTrail, the stewardship log, this repo |
| Engagement lead / product owner | The hosted console (Cognito) | Runs, checkpoints, approvals — no cloud access involved |

Two people can cover all of it (admin+steward hats on one, engineer on
another); a seven-person traditional team maps on without a single new role.
The one convention to hold: **one hat at a time** — the person holding admin
assumes the steward role for steward work, so the audit trail reflects the
function, not the person.

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
