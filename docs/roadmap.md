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
- [ ] Observability via AgentCore/CloudWatch integration.
- [ ] Project-specific IAM execution roles (least privilege).

## V1.x — Operator Interface

- [ ] Web UI or CLI improvements for starting runs and viewing status.
- [ ] Checkpoint approval UI (not stdin-based).
- [ ] Report viewer/download via presigned URLs.
- [ ] Async checkpoint notification (email/webhook).

## V2 — Evaluation and Quality

- [ ] Automated evaluation dimensions (methodology compliance, evidence grounding).
- [ ] Regression tests against known-good reports.
- [ ] Quantitative reconciliation validation.
- [ ] CI/CD pipeline for tool/methodology deployment.

## Future

- [ ] Multi-client concurrent runs.
- [ ] Historical analytics (Redshift or equivalent).
- [ ] Real methodology migration from existing workflow.
- [ ] provider_data_share review for production environment.
