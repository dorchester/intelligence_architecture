# Platform engineer

You own the boundaries: the CloudFormation templates in
`infrastructure/cloudformation/`, and the act of making the live account
match them. Your seat is the **deployer role** — it can operate
CloudFormation on `intelligence-engine-*` stacks (through the `cfn-exec`
execution role no human can assume), start image builds, and write stage
config. It can mutate **nothing directly**: no direct S3, no direct IAM. If
you need to change something, the change is a template, and the template is
a commit.

---

## 1. Sitting down

One profile entry, once:

```ini
# ~/.aws/config
[profile intelligence-deployer]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-deployer
source_profile = <your-sso-profile>
region = us-east-1
```

Assuming the seat is a logged STS call; sessions cap at 4 hours. Get the
`cfn-exec` ARN once from the deployer stack's outputs:

```bash
aws cloudformation describe-stacks --profile intelligence-deployer \
  --stack-name intelligence-engine-dev-deployer \
  --query "Stacks[0].Outputs" --output table
```

## 2. The routine deploy

A merged PR touched `infrastructure/`. From a clean checkout of main:

```bash
./infrastructure/deploy.sh --profile intelligence-deployer \
  --cfn-role arn:aws:iam::<account>:role/intelligence-engine-dev-cfn-exec
```

Single stacks deploy directly the same way:

```bash
aws cloudformation deploy --profile intelligence-deployer \
  --role-arn <cfn-exec-arn> \
  --stack-name intelligence-engine-dev-<name> \
  --template-file infrastructure/cloudformation/<name>.yaml \
  --capabilities CAPABILITY_NAMED_IAM --parameter-overrides Environment=dev \
  --no-fail-on-empty-changeset
```

Quirks worth knowing before they surprise you:

- **Two stacks need two passes.** `app` deploys, yields its URL, then
  redeploys with that URL as the Cognito callback. `databricks-access` needs
  a placeholder external id first, then the real one.
- **`steward` takes `UserPoolId`** from the app stack's outputs (its
  people-admission grant is scoped to that pool).
- **`workbench` takes `VpcId`/`SubnetId`**; unspecified parameters reuse
  their previous values on update, so routine updates don't need them.

## 3. Verify after every deploy

```bash
python scripts/qa_sweep.py     --profile intelligence-deployer   # health: stacks, auth, data, harness, idle billing
python scripts/iac_coverage.py --profile intelligence-deployer   # drift: nothing live that isn't in a template
```

Both are read-only and exit non-zero on failure, so they can gate anything.
If `iac_coverage` reports an unmanaged resource, someone changed the account
outside a template — treat it as an incident, not housekeeping: either
adopt the resource into a template or remove it, and find out which seat
made it.

## 4. Releases: promoting the stage image

The FDE hands you a tested digest. Blessing it as the pipeline default is
your act:

```bash
python scripts/promote_stage_image.py --profile intelligence-deployer --digest sha256:...
```

The script verifies the digest exists in ECR, records it at
`/intelligence-engine/dev/stages/blessed-image`, and prints the previous
digest — **rollback is running it again with that previous value.** Runs
started without an explicit image use the blessed one, so promotion takes
effect for the next run, never mid-run (each execution pinned its digest at
start).

## 5. Rolling back infrastructure

Templates are the source of truth, so rollback is deploying the truth you
want back:

```bash
git checkout <last-good-commit> -- infrastructure/cloudformation/<name>.yaml
aws cloudformation deploy ... # as in §2
git checkout main -- infrastructure/cloudformation/<name>.yaml   # if this was a test
```

For a stack stuck in `ROLLBACK_COMPLETE` after a failed *create* (not
update), CloudFormation requires delete-then-recreate:

```bash
aws cloudformation delete-stack --profile intelligence-deployer --stack-name <stack>
# wait, then redeploy
```

## 6. Adding a new stack

Conventions that keep the estate legible — hold them:

- Stack name `intelligence-engine-<env>-<name>`, template
  `infrastructure/cloudformation/<name>.yaml`, one purpose per stack.
- Tags on everything: `Application=intelligence-engine`,
  `Environment=<env>`, `ManagedBy=cloudformation`.
- Cross-stack references by `Fn::ImportValue` of exported names, so
  dependency order is explicit and a missing dependency fails loudly at
  deploy time.
- **Cost gate**: anything with a meaningful always-on cost (NAT gateways,
  provisioned capacity, always-on compute) needs an explicit decision before
  it enters a template — the estate is deliberately scale-to-zero, and
  `qa_sweep.py` checks for idle billers.
- IAM in stacks follows the house rules: read and write as separate managed
  policies, no runtime role gets `iam:*` or stack operations, and any new
  human capability is a *seat* (assumable role) rather than a broader grant
  on an existing one.

## 7. Image builds

```bash
./infrastructure/remote_build.sh                 # console image
./infrastructure/remote_build.sh --stage-image   # stage image; prints tag + digest
```

Both build in CodeBuild from committed state (`git archive` of HEAD) — no
Docker needed locally, and every image maps to a checkout-able commit. Your
seat can start these builds and read their logs.

## 8. What you cannot do, and what to do instead

| You cannot | Because | Instead |
|---|---|---|
| Edit a bucket policy, role, or object directly | The seat holds zero direct-mutation grants | Change the template, deploy it |
| Operate a non-project stack | CloudFormation grants are scoped to `intelligence-engine-*` | That stack isn't yours; use the account admin path if it's real |
| Assume `cfn-exec` yourself | Its trust policy admits only the CloudFormation service | You never need to — pass it as `--role-arn` |
| Seat a new person | Seating is an admin act | Ask the admin for one `sts:AssumeRole` grant |

The honest residual power of this seat: you could author a template that
widens IAM, and CloudFormation would apply it. That is inherent to being the
deployer — it is mitigated by review (templates are commits with authors)
and by CloudTrail (every stack operation is logged), not by IAM. Treat your
own PRs to `infrastructure/` with corresponding seriousness.
