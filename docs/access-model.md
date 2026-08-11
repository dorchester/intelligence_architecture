# Access model

Who can do what, and — more importantly — who deliberately cannot.

Everything here is enforced in IAM, deployed from the templates in
`infrastructure/cloudformation/`, and verifiable with
`python scripts/iac_coverage.py`. Nothing below is a convention or a
prompt-level rule.

Per-role working guides — the day-to-day commands for each seat described
here — live in [`guides/`](guides/).

---

## The one rule everything follows

> **Boundaries change through exactly one channel — a reviewed template,
> deployed as a logged stack operation. No identity inside the system can
> change them any other way.**

Every runtime identity — the console, the stage runner, the workbench, the
Databricks connection — holds an enumerated list of permissions and has **no
ability to modify CloudFormation stacks, IAM roles, or IAM policies.** The
reason is escalation: an identity that can rewrite IAM can grant itself
anything, so its *real* permission set is "everything, eventually." Keeping
`iam:*` and direct stack operations out of every runtime role means the
permission tables below are the truth, not a starting point.

The practical consequence people notice first: **the engineer workbench cannot
deploy.** That is not a gap — it is the design. The workbench seat changes
*code* (edit stages, rebuild images, rerun the pipeline); *infrastructure*
changes ride through git to the deployer seat, where they leave a diff, an
author, and a CloudTrail entry.

## The second rule: who is allowed to be a bottleneck

The permission question ("who *can* do what") has a twin that governance
reviews usually miss: the liveness question — **whose absence stops the
system?** The design principle here is that almost nobody's should:

- **Critical to execution: the consultant, and only the consultant.** Their
  checkpoint approvals are the deliberate human-in-the-loop on *their own
  report*. Nothing else in the run path waits on a human: the governance
  gates are code and run automatically, the stewardship digest is
  notify-only, and no steward, engineer, or admin action sits between
  "run" and "report."
- **Critical to engineering: the forward-deployed engineer (FDE), plus the platform engineer**
  who turns merged infrastructure changes into deployed stacks. Two
  functions, no more.
- **Critical to nothing, accountable for much: the steward.** Their signoff
  governs what enters the system (admission, *before* any run), their policy
  is enforced *during* runs by the gates in code, and they audit *after*.
  A steward being unavailable stops the admission of a new source — never a
  report.
- **The admin is a dormant recovery path, not a dependency.** No routine
  process — running, engineering, deploying, admitting — requires it. Its
  two remaining acts are seating people (a one-time `sts:AssumeRole` grant
  per person per seat) and recovering the system if the deploy channel
  itself breaks. If the admin is being used on a normal day, something is
  misdesigned.

## Who deploys: the deployer seat

Deploys are the most routine critical act in the system, so they get their own
seat rather than the admin's keys — the standard two-role CloudFormation
pattern, from `deployer.yaml`:

| Role | What it is |
|---|---|
| `deployer` | The platform engineer's seat (assumed like the steward's). Can operate CloudFormation **on `intelligence-engine-*` stacks only**, start image builds, and write the stage-config SSM parameter. It can mutate **nothing directly** — no direct S3, no direct IAM, no direct anything. |
| `cfn-exec` | The execution role CloudFormation itself assumes to create stack resources. Broad on purpose (stacks legitimately create IAM roles, KMS keys, trails) — but its trust policy names only `cloudformation.amazonaws.com`, so **no human or workload can assume it**. It acts only when a template says so. |

The net property: **infrastructure changes flow through exactly one channel —
a template, deployed as a logged stack operation.** A deployer could author a
template that widens IAM; that risk is inherent to deploying and is mitigated
where it belongs: templates ride through git with diffs and authors, every
stack operation lands in CloudTrail, and the seat can be revoked. What the
deployer *cannot* do is change anything quietly — no direct-write path exists
that would leave no diff.

Working from the seats is a named profile per hat (assumption is a logged STS
call; sessions cap at 4 hours):

```ini
# ~/.aws/config
[profile intelligence-steward]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-steward
source_profile = intelligence-dev
region = us-east-1

[profile intelligence-deployer]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-deployer
source_profile = intelligence-dev
region = us-east-1
```

```bash
# deploys from the deployer seat go through the exec role:
./infrastructure/deploy.sh --profile intelligence-deployer \
  --cfn-role arn:aws:iam::<account>:role/intelligence-engine-dev-cfn-exec
```

Seating a second person on any seat is one `sts:AssumeRole` grant on their
principal — not a new role, not a policy change. The merge-gated CI/CD
variant (GitHub Actions OIDC assuming the deployer seat) remains the natural
next step; it changes *who triggers* deploys, not the channel they flow
through.

## Running Claude Code outside the account

Bedrock originally sat only on *machine* identities — the workbench instance
role, App Runner, the CodeBuild stage roles. Reaching a model therefore meant
first reaching the workbench. That is a reasonable default and a poor
requirement, so `bedrock-seats.yaml` grants the model calls to the seats a
person actually assumes. The grant is Anthropic models only and carries **no
data access**: a laptop can call a model, and still cannot read a tier.

Why tiers stay out: `foundational-read` and `derived-read` are held
exclusively by machine identities inside the account — the workbench instance
role and the CodeBuild stage roles. That is the data boundary, not a gap.
`foundational/` is pseudonymous personal data; the surfaces that read it are
governed, in-account, and logged object-by-object in CloudTrail. A seat is a
person on an arbitrary laptop, so granting it a tier would move personal data
onto unmanaged disks and outside the audited perimeter. Model calls have no
such property — the model sees what you paste into it, which is already in
your hands — which is why widening Bedrock did not require touching this.

Three ways to authenticate, in the order worth reaching for:

| | How | Lifetime | Needs |
|---|---|---|---|
| **Assumed seat** | `AWS_PROFILE=intelligence-fde`, `CLAUDE_CODE_USE_BEDROCK=1` | 4h session | Nothing new — the credential chain already works |
| **Short-term API key** | `python scripts/bedrock_api_key.py --profile intelligence-fde` (or `--role-arn …/intelligence-engine-dev-fde` to skip the profile entry), or Bedrock console → API keys → short-term | ≤12h | Nothing new; the key inherits that session's permissions |
| **Long-term API key** | Deploy `bedrock-api-key-user.yaml` per person, then `create-service-specific-credential` | Until deleted | A dedicated IAM user |

```ini
# ~/.aws/config - the same per-hat pattern as every other seat
[profile intelligence-fde]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-fde
source_profile = intelligence-dev
region = us-east-1
```

```bash
aws sso login --profile intelligence-dev
export AWS_PROFILE=intelligence-fde CLAUDE_CODE_USE_BEDROCK=1
claude
```

**API-key auth is a second grant, not a side effect of the first.** Presenting
a bearer token is its own action — `bedrock:CallWithBearerToken` — checked in
addition to `InvokeModel`. Without it a key mints happily and is then refused
at the call, which is a confusing failure to debug from the token alone. The
seat policy grants it; it permits *presenting* a token as that identity and
confers no model access by itself.

All of this was verified against the deployed stack rather than reasoned
about: the `fde` seat calls a model, a key minted from that seat authenticates
to Bedrock on its own, Claude Code runs with the key as its only credential,
and the same seat is still denied `derived/`, `foundational/` and
`cloudformation:UpdateStack`.

**On long-term keys.** A long-term Bedrock API key is an IAM service-specific
credential, and those can only hang off an IAM *user* — there is no version of
it that does not create one. This estate otherwise has zero users on purpose:
every identity is federated and every session expires. So the long-term path
exists, declared in a template rather than improvised in the console, but it
is the last resort rather than the default. The user it creates can do exactly
one thing, cannot log in, and holds no access keys, so a leaked key spends
your Bedrock budget and reaches nothing else — which is why the per-client
spend alarms in `llm-controls.yaml` are the control that matters for it.
Revoking a leaver is deleting their stack.

Who gets model access is a deploy-time parameter, not a code change. All
three human seats — `fde`, `steward`, `deployer` — hold it by default: the
grant is model calls only, so it changes what a seat can *ask*, never what it
can *touch*. Admission still cannot deploy, deploying still flows through
`cfn-exec`, and no seat gained a byte of data access. Deleting the stack
revokes all of it and leaves every other permission intact.

---

## Runtime identities

Sixteen roles exist, all named `intelligence-engine-dev-*`, all defined in
this repo. Outside the deploy channel described above, none can touch IAM or
CloudFormation. Grouped by what they are for:

### People-facing seats

| Role | Assumed by | Can | Deliberately cannot |
|---|---|---|---|
| `fde` | A human, via `sts:AssumeRole` (max 4-hour sessions) | Find, wake, reach and sleep the workbench; invoke Anthropic models (from `bedrock-seats.yaml`, so Claude Code runs on a laptop as well as on the box) | Read any tier; deploy; touch IAM — verified denied by probe. The model grant carries **no** data access with it |
| `workbench` | EC2 (the FDE's box, reached via SSM only — no SSH exists) | Invoke Bedrock; read/write the project bucket and run table; push images to ECR; start CodeBuild builds; read stacks, logs, metrics | Deploy or modify any stack; touch IAM; touch billing; touch non-project resources |
| `steward` | A human, via `sts:AssumeRole` (max 4-hour sessions) | Admit data (`landing-write`); admit people (create/disable console users in the project Cognito pool); read the stewardship log and CloudTrail; invoke Anthropic models (`bedrock-seats.yaml`) | Deploy; touch IAM; write to any lakehouse tier; reach the vault. The steward governs — it does not operate the pipeline |
| `deployer` | A human, via `sts:AssumeRole` (max 4-hour sessions) | Operate CloudFormation on `intelligence-engine-*` stacks (through `cfn-exec`); start image builds; write stage config; invoke Anthropic models (`bedrock-seats.yaml`) | Mutate any resource directly; operate any non-project stack; assume `cfn-exec` itself |
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
| `cfn-exec` | CloudFormation only (its trust policy admits no human and no workload) | Create/update stack resources when a template says so — the far end of the deploy channel |
| `databricks-uc` | Databricks Unity Catalog (external ID trust) | Read the `derived` and `contextualized` tiers in place — zero-copy, no export, revocable by deleting one role. Also still reads the raw dataset drop, which is the one path that bypasses the tiers (see [`guides/analyst.md`](guides/analyst.md)) |

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

Most of the sixteen roles above are *machine* identities. The people questions — who
governs the data, who uploads datasets, who engineers the pipelines — map onto
a deliberately small set of human functions. The design goal is that **adding
a person means attaching existing policies to a seat, never minting new
roles.**

| Function | Who / what today | How it works | Critical to |
|---|---|---|---|
| **Platform engineer (deploys)** | The deployer seat (`sts:AssumeRole`) | Turns merged template changes into deployed stacks, through `cfn-exec` — the only channel by which boundaries change. Mutates nothing directly. | Engineering |
| **Data steward (governs + admits)** | The steward seat (`sts:AssumeRole`) + the standing decision gates | **Admitting data** (`landing-write` — admission is the steward's decision, so the grant follows the accountability), **admitting people** (console user management in Cognito), and **reading the audit surface** (stewardship log, CloudTrail). Their policy is enforced during runs by the gates *in code* — the steward signs off on what may enter, not on each run. Changing what the gates enforce is a code change — diff, review, redeploy. | Nothing at runtime — admission only |
| **Forward-deployed engineer / FDE (builds/adjusts)** | The workbench seat (Claude Code + full toolchain, SSM-only) | Edits stages, rebuilds images, reruns the pipeline, inspects everything. Cannot deploy — infra changes go back through git to the platform engineer. | Engineering |
| **Data scientist / analyst** | Databricks via the `databricks-uc` role | Reads the derived and contextualized tiers zero-copy through Unity Catalog, on a read-only credential. Never holds write on anything. | Nothing |
| **Consultant (consumes + approves)** | Cognito user in the hosted console | Not an IAM identity at all; the app acts for them within its own scoped role. Their checkpoint decisions are the one human dependency in the run path — on their own report, by design. | Execution |
| **Account admin** | SSO, dormant | Seats people (one grant per person per seat); recovers the system if the deploy channel breaks. Used on a normal day = design failure. | Nothing, by design |

Why this is enough, and why it should resist growing: every end-to-end
activity — admit → conform → build products → analyze → consume → audit — has
exactly one function responsible for it, and each function's power is a
*combination of policies that already exist*. The failure mode to avoid is
inventing a new role per person or per tool; the policy layer is the stable
vocabulary, and seats are just attachment points for it.

### End to end: the humans behind the machines

The pipeline roles have machine-sounding names (`conformance`,
`product-builder`, `stage-runner`), which obscures the question that matters
for a real team: *which human stands behind each one?* The full report
lifecycle, human-first:

| # | Lifecycle step | Human accountable | Executes as |
|---|---|---|---|
| 1 | Admit a source (signoff + drop to `landing/`) | Data steward | steward seat |
| 2 | Conform it (L1→L2: identifiers dropped, schema registered) | Data engineer | `conformance` |
| 3 | Build data products (L3 aggregates, L4 vectors/graph) | Data engineer / product owner — each derived table names its `ie.owner` in catalog metadata | `product-builder` |
| 4 | Validate the products | Analyst / data scientist | Databricks, read-only |
| 5 | Build & tune the report pipeline (stages, prompts, evals) | Forward-deployed engineer (FDE) | workbench; runs as `stage-runner` |
| 6 | Run the report | Consultant / engagement lead | console → `workflow` → `stage-runner` |
| 7 | Approve checkpoints, request revisions | Consultant (human-in-the-loop by design) | `wf-approval` records it |
| 8 | Onboard people (console logins) | Data steward | steward seat |
| 9 | Audit afterward | Steward / auditor | steward seat (log + CloudTrail) |
| 10 | Change the system itself | Platform engineer | deployer seat, through `cfn-exec` |

A machine role is never accountable for anything — it is the enumerated
identity a human's work executes under. `product-builder` is the data
engineer's transformations wearing their least-privilege grants; `ie.owner`
on every derived table records which human answers for the product. In a
one-person shop the same human holds most rows; because each row is already
a distinct seat, handing one to a new person is a seating action, not a
redesign.

### If a traditional team walked in tomorrow

The seats are named for functions, not job titles — but they map cleanly onto
a conventional data/platform team, which is the test that the model is
workable beyond an AI-centric setup:

| Traditional title | Sits at | Notes |
|---|---|---|
| Platform / DevOps engineer | Deployer seat | Owns `infrastructure/`; the only function that changes boundaries, and only through templates |
| Data engineer | Workbench seat + Databricks | The conform/build stages are ordinary Python over S3 + Glue; nothing requires an agent — Claude Code on the workbench is leverage, not a prerequisite |
| Forward-deployed engineer (FDE) | Workbench seat | Same seat as the data engineer, different work: prompts, stages, evals, the model layer |
| Analytics engineer / BI analyst | Databricks (read-only via Unity Catalog) | SQL over the derived tier; never needs AWS credentials at all |
| Data steward / governance lead | Steward seat | Admission and audit, no pipeline power |
| Security / compliance auditor | Steward seat (read side) or a read-only SSO permission set | Everything they need is readable: CloudTrail, the stewardship log, this repo |
| Engagement lead / product owner | The hosted console (Cognito) | Runs, checkpoints, approvals — no cloud access involved |

Two people can cover all of it (steward+deployer hats on one, engineer on
another); a seven-person traditional team maps on without a single new role.
The one convention to hold: **one hat at a time** — a person wears the
steward seat for steward work and the deployer seat for deploys, never raw
admin for either, so the audit trail reflects the function, not the person.

---

## Escalation paths, considered and closed

| Path | Why it fails |
|---|---|
| Workbench edits a stack to widen its own role | No `cloudformation:*Stack*`, no `iam:*` — the API call is denied |
| App console rewrites guardrails at runtime | Guardrail editing is read-only when deployed; config changes require a commit and redeploy |
| Stage code writes back to its input tier | The role running it does not hold that tier's write policy |
| Deployer mutates a resource directly, leaving no diff | The seat holds no direct-mutation grants at all — verified by probe (direct IAM write, direct S3 write: both denied). Everything it does is a stack operation on a template |
| Deployer authors a template that widens IAM | Not closed by IAM — inherent to deploying. Mitigated where it belongs: templates ride through git with diffs and authors, every stack operation is CloudTrail-logged, and the seat is revocable. This is the one path that relies on review rather than denial, and it is named here so nobody mistakes it for closed |
| A human assumes `cfn-exec` to use its broad grants | Its trust policy admits only `cloudformation.amazonaws.com` — verified by probe (denied) |
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
