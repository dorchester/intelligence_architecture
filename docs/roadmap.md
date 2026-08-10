# Roadmap

## V0 — AWS Thin Slice (complete)

- [x] Repository skeleton and architecture documentation.
- [x] RunContext with stage tracking.
- [x] Deterministic workforce analysis tool.
- [x] Chart generation.
- [x] HTML report rendering from template.
- [x] Synthetic sample data.
- [x] Tests for deterministic analysis and run isolation.
- [x] Local end-to-end run produces report.html.
- [x] Bedrock model abstraction (configurable, inference profiles).
- [x] Narrative generation via Bedrock Claude.
- [x] Storage abstraction (LocalStorage + S3Storage).
- [x] S3 bucket with encryption, versioning, private access (CloudFormation).
- [x] DynamoDB run state table with client GSI (CloudFormation).
- [x] Checkpoint approval mechanism (pause/resume).
- [x] Agent engine with methodology-driven tool-use reasoning.
- [x] Structured logging with run_id correlation.
- [x] Full orchestrator integrating all components.
- [x] Cross-run/client isolation verified on S3.
- [x] End-to-end synthetic run verified.

## V1 — AgentCore Runtime

- [ ] Deploy agent to Bedrock AgentCore Runtime.
- [ ] Wire Code Interpreter for sandboxed dynamic execution.
- [ ] Promote recurring dynamic operations to named tools.
- [ ] Full methodology playbook execution (beyond thin slice).
- [x] Observability via CloudWatch — token, latency and cache dashboard; the
      AgentCore half remains open.
- [x] Project-specific IAM execution roles (least privilege) — one role per
      medallion boundary, read and write always separate policies.

## V1.x — Operator Interface

- [x] Web UI for starting runs and viewing status (hosted console behind Cognito).
- [x] Checkpoint approval UI — approve / revise-with-feedback / reject, bounded
      revision loops, zero compute while suspended.
- [ ] Report viewer/download via presigned URLs.
- [ ] Async checkpoint notification (email/webhook).

## V2 — Evaluation and Quality

- [ ] Automated evaluation dimensions (methodology compliance, evidence grounding).
- [x] Regression floor — 72-test suite, in-account golden replay, and a
      seeded-defect eval that scores the reviewer's recall against planted
      arithmetic, unsupported-figure, contradiction and stale-trend defects.
- [ ] Quantitative reconciliation validation.
- [ ] CI/CD pipeline for tool/methodology deployment.

## Shipped since this roadmap was written

Work that did not exist as a planned line but is now deployed and documented:

- [x] Medallion data plane — landing, foundational, derived, contextualized,
      stewardship — with policy gates enforced in code on every governed write.
- [x] Governance surface — CloudTrail with object-level data events, an
      append-only stewardship log, and an escalation topic.
- [x] Delegated human seats — steward, deployer (with a `cfn-exec` role no human
      can assume) and forward-deployed engineer, so the account admin is dormant.
- [x] Step Functions harness with digest-pinned stage images and an explicit
      candidate-to-blessed promotion path.
- [x] Zero-copy Databricks reads of the governed tiers on a read-only credential.

## Future

- [ ] Multi-client concurrent runs.
- [ ] Historical analytics (Redshift or equivalent).
- [ ] Real methodology migration from existing workflow.
- [ ] provider_data_share review for production environment.
