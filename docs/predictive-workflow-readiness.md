# Predictive Workflow Readiness

The Intelligence Engine's next consumer is an episodic report-generation
workflow: a short batch of containerized stages with no-bypass QA gates,
durable human approvals, and data drawn from several external channels. That
workflow lives in a separate, private repository — its stage semantics and
data-vendor relationships deliberately do not appear here. This document maps
its **functional requirements** onto what is deployed in this account, and
records what remains.

## Requirement map

| Functional requirement | Status | Where |
|---|---|---|
| Partitioned data domains with hard IAM walls | **Deployed** | `dataplane.yaml` — landing / lakehouse / artifacts / site / vault, one bucket each |
| Row-level input reads as enumerated grants | **Deployed** | `silver-read` managed policy; attached per stage, never by default |
| Aggregates-only outputs | **Pattern set** | An *output* rule — enforced by QA-gate validators (guardrail engine family), because IAM cannot inspect aggregation level |
| No-bypass gates | **Pattern set** | Only the post-gate stage holds `artifacts-publish`; writing without passing the gate is an IAM denial, not a convention |
| Sensitive dataset home with privacy controls | **Walls deployed, empty** | Vault bucket + dedicated KMS key, zero read grants until a privacy review signs off |
| Audited vendor delivery + human upload | **Deployed** | Object-created events on landing and vault → 90-day audit log group; vendor principals get write-only `landing-write` |
| Interactive governed SQL (entity diagnostics) | **Deployed (interim)** | Athena workgroup over the lakehouse, 10 GB scan cutoff; a warehouse data share or Databricks SQL can replace it without moving data |
| Author seat (judgement work, three personas) | **Deployed** | `author-seat.yaml` — read artifacts, invoke Bedrock, SSM sessions, changes only by pull request. Pilot seats authors on the engineer workbench |
| Per-client LLM cost attribution | **Deployed (pattern)** | Bedrock application inference profiles, two demo-client wrappers; metrics-level today |
| Spend ceiling | **Deployed (alerting)** | Monthly budget, 80% actual / 100% forecasted alerts. A hard cutoff is a production decision |
| Model quotas verified | **Checked — action needed** | Applied Haiku 4.5 quota is 50 req/min vs a 10,000 default; fine for 8-way concurrency, will throttle high-volume builds. Request an increase before production loads |
| Invocation logging | **Prepared, disabled** | `observability-logging.yaml` captures full prompts/responses; enabling is a privacy decision, not an engineering one |
| Durable, authenticated, days-long approvals | **Not built — next** | Cognito (identity), DynamoDB (state), and the checkpoint UX exist; the wait is still an in-process thread. Moving it to durable state is the top open build |
| Orchestration substrate for the stage batch | **Open decision** | Days-long zero-compute suspends justify Step Functions under this project's own rules; not built until confirmed |
| In-account regression replay | **Pattern proven** | CodeBuild runs project containers in-account today; a replay project is a template away |
| Hub-site serving | **Bucket deployed** | Private site bucket, CloudFront-ready. A public repository mirror of client-derived content is **recommended against** |
| Build-stage container contract | **Private-repo work** | Bedrock wrapper, env-var config, and the CodeBuild path here are the working reference |

## Decision gates (not engineering)

1. **Sensitive dataset ingestion** — privacy review before any read grant on the vault.
2. **Vendor licence reviews** — before any vendor dataset is bulk-copied into the lakehouse (interactive federated query is the interim posture).
3. **Invocation logging** — captures prompts and responses; needs a privacy decision plus KMS before enabling.
4. **Orchestration substrate** — Step Functions is justified and recommended; awaiting confirmation.
5. **Public mirroring of report sites** — recommended no; serve privately behind CloudFront + auth.
6. **Billing-level cost attribution** — requires activating cost-allocation tags in the billing console, an account setting this project does not touch.

## Databricks

The account-level Terraform (`infrastructure/databricks/`) provisions a
**serverless** workspace — no VPC, no NAT, no compute-plane IAM. Credentials
flow from SSM SecureString; they never appear in files or arguments. The AWS
side (`databricks-access.yaml`) provides a read-only Unity Catalog role over
the dataset prefix with a deliberately unusable placeholder external ID until
the storage credential exists.

Current state: OAuth machine-to-machine auth against the accounts API works;
workspace creation awaits the service principal being granted **account
admin**. After that: apply, then storage credential → real external ID →
second pass of `databricks-access.yaml` → external location over the existing
datasets, read in place, never copied.
