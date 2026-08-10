# Forward-deployed engineer (FDE)

You own the workload: stages, prompts, evals, the report pipeline's behavior.
Your seat is the **workbench** — an EC2 box inside the account with Claude
Code, git, the AWS CLI, the Databricks CLI and Terraform preinstalled, reached
through SSM only. Your identity there is the instance role: enumerated grants,
no keys anywhere, every call CloudTrail-logged.

What you can do: invoke Bedrock, read/write the project bucket and run table,
push images to ECR, start CodeBuild builds, read logs/metrics/stack state.
What you deliberately cannot: deploy or modify stacks, touch IAM, touch
billing. Infrastructure proposals leave your seat as pull requests, not
API calls (see [Use case 7](#7-proposing-an-infrastructure-change)).

---

## 1. Sitting down

### Your seat, in IAM

Your access is the **`intelligence-engine-dev-fde` role** (from
`workbench.yaml`), and it is deliberately tiny: find the box, wake it, open a
session, put it back to sleep. That is *all* it grants — it cannot read a
data tier, invoke a model, or deploy anything (all four denials are verified
by probe). Every capability you actually use arrives from the **instance
role** once you are at the prompt. That split is the point: widening what an
FDE can do is a reviewed change to `WorkbenchRole` in a template, never a
grant handed to a person.

```ini
# ~/.aws/config
[profile intelligence-fde]
role_arn = arn:aws:iam::<account>:role/intelligence-engine-dev-fde
source_profile = <your-sso-profile>
region = us-east-1
```

### Three ways to the prompt

| Path | Needs | Use when |
|---|---|---|
| **Local terminal** | AWS CLI + Session Manager plugin, installed once (`winget install Amazon.SessionManagerPlugin` on Windows — then open a *fresh* terminal so PATH updates) | Your day-to-day. Best ergonomics, real scrollback, your own editor beside it |
| **Console → Session Manager** | Only console access + the FDE seat. No install, no plugin | A borrowed machine, or when the plugin fight is not worth it. EC2 → select the instance → **Connect** → *Session Manager* tab → **Connect**; or Systems Manager → Session Manager → Start session |
| **CloudShell** | Console access **plus** `cloudshell:*` in your permission set — which the FDE seat does **not** include | Only if your organisation's permission set happens to grant it. Do not design around this one |

The middle path is the honest answer to "what if I have no local setup" — it
is a full browser terminal on the same box, gated by the same seat, logged
the same way in CloudTrail.

```bash
# find the box (once)
aws ec2 describe-instances --profile intelligence-fde --region us-east-1 \
  --filters "Name=tag:Application,Values=intelligence-engine" \
  --query "Reservations[].Instances[].[InstanceId,State.Name]" --output text

# start it if stopped (it auto-stops after ~1h idle; this is normal)
aws ec2 start-instances --instance-ids <workbench-id> --profile intelligence-fde --region us-east-1

# connect - no SSH, no keys
aws ssm start-session --target <workbench-id> --profile intelligence-fde --region us-east-1

# inside (the shell lands you in the repo with the venv active):
sudo su - ec2-user
claude
```

**Claude Code here runs on Bedrock through the instance role** — no API key,
no personal Anthropic login, nothing to leak. The banner says
`Amazon Bedrock`, and the settings that do it (`CLAUDE_CODE_USE_BEDROCK=1`
plus the model ids) are written by the workbench template at first boot. The
consequence worth understanding: **your agent's inference stays inside the
account boundary** — same Bedrock account, same CloudTrail, same data path as
the product's own model calls. An engineer's exploratory prompts are not a
side channel out of the estate.

Set your git identity on first use — pushing is *your* identity, deliberately
not baked into the box:

```bash
git config --global user.name "Your Name"
git config --global user.email you@example.com
# plus an SSH key or token for push access to the GitHub repo
```

When done: `exit` until the session closes. The box stops itself; stopping it
explicitly costs nothing and is polite.

## 2. The iterate loop: testing pipeline variants

The stages in `stages/` are ordinary Python over S3 + Glue + Bedrock, and the
workbench role already holds the grants — so the fastest loop is direct:

```bash
python stages/enrich_profiles_llm.py sterling-pharma
```

Edit a prompt, rerun, inspect what it wrote (`analysis/<client_id>/…` in the
artifacts bucket — JSON, PNGs, HTML). Pull an artifact down with `aws s3 cp`,
or view it through the engineer console's dataset browser.

When the *real* execution environment matters, containerize:

```bash
./infrastructure/remote_build.sh --stage-image     # builds in CodeBuild, no Docker needed
# prints a tag AND a digest - the digest is what you run
```

Every Step Functions execution **requires `stage_image` in its input**
(`ImageOverride.$: "$.stage_image"` threads it through every stage task). So
you run the full harness against *your candidate digest* while the previous
digest stays what everyone else runs. Two pipeline versions coexist as two
digests, and each run's record says exactly which one it ran.

A typical experiment end to end:

```
branch → edit stage → run raw against synthetic data → build image →
run harness with new digest → diff artifacts against the old run (both in S3,
keyed by run) → merge
```

## 3. The regression floor

Before anything you changed reaches a consultant:

- `python -m pytest` — the code suite (runs anywhere).
- **Golden replay** — the same suite executed in-account by the
  `golden-replay` CodeBuild project, against the same data boundary as the
  workload.
- **Seeded-defect eval** (`tests/test_seeded_defects.py`) — checks *model
  behavior*, not code: four planted defect classes (arithmetic, unsupported
  figure, contradiction, and a trend asserted from point-in-time counts),
  scored as recall against a 2/3 floor. This is what catches "my new prompt
  quietly broke the reviewer."

Extending that suite is itself a normal FDE activity, and a good first task
to run through Claude Code on the workbench: add a defect class, run the eval
against live Bedrock, read the recall line. A run that reports
`reviewer recall 100% - caught [...], missed []` is evidence about the model,
not about the code.

If you touch a reviewer or extraction prompt, run the seeded eval. If recall
drops below the bar, that is a regression even though nothing errored.

## 4. Prompt work: the caching contract

`stages/_bedrock.py` is the wrapper — use it rather than raw boto3 in stages.
Things it enforces that you should know about:

- **Prompt caching has a real minimum**: 4,096 tokens for the current Claude
  generations (AWS's documented figure for Haiku 4.5 / Sonnet 4.5+; the
  1,024/2,048 numbers you may remember are older generations). A cacheable
  prefix below the minimum silently caches nothing — the wrapper prints a
  warning when that happens. Don't chase cache savings with a 2k-token
  system prompt; either grow the stable prefix past the threshold or accept
  the miss.
- Structured output goes through `invoke_json()` (retries with a repair
  round); concurrency and backoff are handled for you. The concurrency
  limiter is per process: each parallel build carries its own, and quota
  bursts across builds are absorbed by the jittered retries — so write
  stages as if throttling can happen, because occasionally it will, and the
  wrapper will ride it out.
- `summary()` gives you tokens/calls/cache stats per run — check it when a
  change might move cost.

## 5. Running the full harness

```bash
# against the blessed pipeline (what consultants get):
python scripts/start_harness_run.py --client sterling-pharma

# against your candidate:
python scripts/start_harness_run.py --client sterling-pharma \
  --stage-image <repo-uri>@sha256:<your-digest>
```

The script resolves the **blessed digest** from SSM when you don't name one,
and refuses a `--stage-image` that isn't digest-pinned — so every run is
either the blessed pipeline or an explicitly named candidate, never
"whatever `:latest` is right now."

Checkpoint decisions arrive as `{decision, feedback}` via the approval path;
revisions re-run stages with `REVISION_FEEDBACK` in their environment — your
stages should read it if they participate in revision. While an execution
waits at a checkpoint, nothing runs and nothing bills. Your seat can answer
its own test runs' checkpoints (`states:SendTaskSuccess` with the recorded
task token) — production runs are answered by the consultant in the console.

## 6. Version control and promotion

- **Code** (stages, prompts, webapp, tests): plain git on the workbench —
  branch, commit, push. Claude Code commits on your behalf as anywhere else.
- **Which version is live is always recorded**: images by digest in each
  execution's input; stage source packaged from committed state (`git archive`
  of HEAD, so every image maps to a checkout-able commit).
- **Promotion is explicit.** When your candidate has passed the harness and
  the regression floor, hand the digest to whoever holds the deployer seat;
  they run `scripts/promote_stage_image.py`, which verifies the digest exists
  in ECR and records it as the blessed image in SSM. Rollback is promoting
  the previous digest back — the script prints it at every promotion for
  exactly that reason. Releasing is a deployer-seat act on purpose: the same
  separation as infrastructure, applied to the pipeline default.

## 7. Proposing an infrastructure change

Your seat can *edit* templates but not *apply* them — no
`cloudformation:UpdateStack` exists on the workbench role, by design. The
flow:

```
edit infrastructure/cloudformation/<stack>.yaml → commit → push → PR →
platform engineer deploys from the deployer seat
```

That asymmetry is the enforced review boundary: your code takes effect
through image digests you control; boundary changes take effect only through
a template someone at the deployer seat applies.

## 8. Where your work shows up

The terminal is one surface, not the whole job. Everything you do lands
somewhere durable, and knowing which surface answers which question is most
of the operational skill:

| Question | Surface |
|---|---|
| What did I change? | `git` on the workbench → branch, diff, push → the PR on GitHub. Code review is the record |
| Did my pipeline version run, and which one? | Step Functions **Executions** — each input pins a `stage_image` digest |
| What did a stage actually do? | CodeBuild **build history** + its CloudWatch log stream, per stage run |
| What did the model see and say? | `/engineer` run traces — the exact context injected into every prompt |
| What did it produce? | `analysis/<client_id>/…` in the artifacts bucket; the dataset browser renders them |
| What did it cost, in tokens? | the CloudWatch Bedrock dashboard, and `summary()` from `_bedrock.py` per run |
| Did a governance gate fire? | your run's exception text; the recorded event lives in the steward's log |
| Is the estate still as the templates describe? | `python scripts/iac_coverage.py` |
| Who did what, account-wide? | CloudTrail — including your own SSM session and every call your agent made |

Two habits worth forming. **Work on a branch**, because the digest you test
and the commit you merged should be traceable to each other. And **read the
trace, not just the output** — a briefing that looks right and a prompt that
received the right context are different claims, and only the second one
survives a client asking "where did this number come from?"

## 9. Observability when something misbehaves

- **Engineer console** (`https://<ServiceUrl>/engineer`): run traces, the
  exact context injected into every prompt, dataset browser. Guardrails are
  read-only when deployed — edit `guardrails/config.yaml` and redeploy.
- **CodeBuild logs**: every stage run is a build with a log stream —
  `aws logs get-log-events` or the build history in the console.
- **Gate blocks** (`PolicyViolation`): the exception your run printed tells
  you which gate fired and why; the full event also lands in the stewardship
  log, which only the steward can read — your `stewardship-write` grant is
  append-only, so ask them if you need the recorded history.
- `python scripts/qa_sweep.py` — the system-wide health check; run it after
  anything invasive.

## 10. What working with the steward looks like

- You **cannot** drop new source data into `landing/` — that is admission,
  the steward's act. Hand them the file and the provenance story.
- Your stages *will* be blocked by `gate_anonymization()` if they try to
  write direct identifiers to governed tiers, and *will* warn on
  quasi-identifier combinations and inferred attributes. The gates are the
  steward's policy in code form: if a gate is wrong, the fix is a PR the
  steward reviews — not a workaround in your stage.
