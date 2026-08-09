# Account admin

The shortest guide, on purpose: **if you are doing something with this role
on a normal day, something is misdesigned.** Every recurring act has a seat —
deploys (platform engineer), data and people admission (steward), engineering
(FDE), analysis (Databricks), running reports (console). The admin exists
for exactly two things.

---

## 1. Seating people

Each seat is an IAM role trusting the account; a person is "seated" by
granting their SSO principal permission to assume it. One statement per
person per seat, added to their Identity Center permission set (or a group
policy):

```json
{
  "Effect": "Allow",
  "Action": "sts:AssumeRole",
  "Resource": "arn:aws:iam::<account>:role/intelligence-engine-dev-steward"
}
```

Same pattern for `-deployer`. Unseating is removing the statement. The
workbench (FDE) seat is granted differently — it's reached through SSM, so
seating an FDE means granting `ssm:StartSession` on the workbench instance
(and `ec2:StartInstances` on it, so they can wake their own box).

Consultants are **not seated by you at all** — they're Cognito users the
steward manages.

## 2. Break-glass

You act directly only when the delegated paths themselves are broken:

- **The deploy channel is wedged** — e.g. the deployer stack itself is
  damaged, or a stack needs an operation outside the deployer's scope.
  Fix the minimum, through templates wherever possible, and get back out.
- **A seat must be revoked *now*** — pull the assume-role grant, or in a
  compromise, attach an explicit deny while you investigate. CloudTrail has
  the full history of what any seat did (every assumption and every call).
- **First-time bootstrap** — an empty account has no deployer seat yet, so
  the first `deploy.sh` run (which creates the seats) is yours. It's also
  the last routine deploy you ever run.

After any break-glass action: whatever you touched, reconcile it —
`python scripts/iac_coverage.py` must come back clean (`NO DRIFT`), and if
you changed behavior, the change gets retrofitted into a template and
committed. Break-glass that leaves untracked state behind isn't recovery,
it's new damage.

## 3. What you should observe, idly

Nothing is *required* of this role day-to-day, but a monthly glance costs
little:

```bash
python scripts/qa_sweep.py --profile <admin-profile>       # everything healthy, nothing idle-billing
python scripts/iac_coverage.py --profile <admin-profile>   # nothing exists outside templates
```

Both passing means the system is running itself the way the access model
says it should — which is the whole point of you having nothing to do.
