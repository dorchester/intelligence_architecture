# Data steward

You own admission and accountability: what data may enter, who may use the
console, and whether the controls actually held. You are deliberately **not
critical to any run** — your policy is enforced during runs by gates in code,
so a report never waits on you. What waits on you is anything *new*: a new
source, a new user, a question about what happened.

Your seat holds exactly three capabilities — admit data, admit people, read
the audit surface — and cannot deploy, write to any governed tier, invoke
models, or reach the vault.

---

## 1. Sitting down

```ini
# ~/.aws/config
[profile intelligence-steward]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-steward
source_profile = <your-sso-profile>
region = us-east-1
```

Assumption is a logged STS call; sessions cap at 4 hours. Also confirm the
**stewardship email subscription** (the governance stack subscribes your
address to the escalation topic; AWS sends a one-time confirmation email) —
that is how gate escalations reach you without you polling.

## 2. Admitting a new source

Admission is a decision first and an upload second. The decision gates —
licensing/terms, privacy basis, sensitivity of what could be inferred — are
standing organizational calls; nothing below is a substitute for them. Once
a source clears:

```bash
aws s3 cp ./the-drop.jsonl \
  s3://<landing-bucket>/landing/<client_id>/<source>/<date>/ \
  --profile intelligence-steward
```

Your seat is the **only** identity that can write to `landing/` — which
means every object there is something a steward deliberately placed. From
that point the pipeline takes over under its own identities (conformance
drops identifiers on the way to `foundational/`), and the anonymization gate
checks every governed write.

What you admit constrains what downstream can claim: the current build runs
**synthetic data only**, and admitting the first real source is the moment
the standing privacy/licensing signoffs stop being theoretical.

## 3. Admitting and removing people

Console users are Cognito, not IAM — creating one grants no cloud
permissions of any kind. No self-signup exists; every user is one a steward
made:

```bash
# create, then set a permanent password (no email delivery is configured,
# so the temp-password flow would strand the user - skip it deliberately):
aws cognito-idp admin-create-user --profile intelligence-steward \
  --user-pool-id <UserPoolId> --username them@example.com \
  --user-attributes Name=email,Value=them@example.com Name=email_verified,Value=true

aws cognito-idp admin-set-user-password --profile intelligence-steward \
  --user-pool-id <UserPoolId> --username them@example.com \
  --password '<initial-password>' --permanent

# offboard (reversible), or remove entirely:
aws cognito-idp admin-disable-user --profile intelligence-steward \
  --user-pool-id <UserPoolId> --username them@example.com
aws cognito-idp admin-delete-user  --profile intelligence-steward \
  --user-pool-id <UserPoolId> --username them@example.com
```

(`UserPoolId` is in the app stack's outputs.)

## 4. Reading the audit surface

Three layers, all yours to read and nobody else's:

**The stewardship log** — every gate evaluation and governed tool call,
append-only from the inside (writers cannot read it; you cannot write it):

```bash
aws s3 ls s3://<lakehouse-bucket>/stewardship/gate-events/ --recursive --profile intelligence-steward
aws s3 cp s3://<lakehouse-bucket>/stewardship/gate-events/<file>.jsonl - --profile intelligence-steward
```

Each event names the gate, the caller identity, the decision
(BLOCK/WARN/NOTE), and what triggered it. `stewardship/tool-calls/` records
every governed-tool invocation with caller and parameters.

**CloudTrail** — who touched what, at the API level, including object-level
reads of the foundational/derived tiers and the vault:

```bash
aws cloudtrail lookup-events --profile intelligence-steward \
  --lookup-attributes AttributeKey=ResourceName,AttributeValue=<bucket-or-role> \
  --max-results 20
```

**The escalation digest** — SNS to your inbox when a gate escalates; the
absence of mail is itself signal, but verify occasionally against the log
rather than trusting silence.

## 5. Changing what the gates enforce

The gates (`stages/_governance.py`) are your policy in executable form:
admission permissibility, direct-identifier blocking, quasi-identifier and
inferred-attribute warnings. You do not edit them from this seat — a policy
change is a code change, which means a diff, your review, and a redeploy.
That is deliberate: it makes every change to enforcement attributable and
reversible, including yours.

The review you owe on such PRs: does the gate still block direct
identifiers on governed writes, does it still flag inferred attributes
(scores like `turnover_risk_score` are personal data about a person, even
computed ones), and does anything newly written to `derived/` clear the
aggregation/suppression bar (`MIN_CELL`).

## 6. The vault

The sensitive annex (`vault` bucket) has **zero decrypt principals** on its
KMS key. Granting the first one is not a steward act — it is a reviewed
template change through the platform engineer, gated on the standing privacy
signoff. Your role in that: be the person who says whether the signoff
actually happened.

## 7. What you cannot do, and why that protects you

| You cannot | Why it's right |
|---|---|
| Write to foundational/derived/contextualized | Your accountability is admission and audit; holding pipeline write would make you an operator of the thing you audit |
| Read foundational-tier records | Pseudonymous ≠ anonymous; the steward function needs the log and the catalog, not row-level personal data |
| Deploy or change gates directly | Enforcement changes must be attributable — including to you |
| Be required mid-run | A steward absence should stop new admissions, never a consultant's report |
