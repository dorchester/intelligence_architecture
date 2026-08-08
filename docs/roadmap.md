# Roadmap

## V0 — Local Thin Slice (current)

- [x] Repository skeleton and architecture documentation.
- [x] RunContext with stage tracking.
- [x] Deterministic workforce analysis tool.
- [x] Chart generation.
- [x] HTML report rendering from template.
- [x] Synthetic sample data.
- [x] Tests for deterministic analysis.
- [ ] Local end-to-end run produces report.html.

## V0.1 — Bedrock Narrative

- [ ] Replace stub narrative with Bedrock Claude call.
- [ ] Wire system prompt and report_narrative prompt.
- [ ] Store raw LLM response in working/ for audit.
- [ ] Test with synthetic data (no real client data).

## V0.2 — S3 Integration

- [ ] Abstract storage behind an interface (local filesystem / S3).
- [ ] Runs write input/working/output to S3 under run prefix.
- [ ] Implement run_id-scoped IAM policy pattern.

## V0.3 — State and Checkpoints

- [ ] DynamoDB table for run state.
- [ ] Implement checkpoint pause/resume pattern.
- [ ] Operator can approve/reject at each checkpoint.

## V1 — AgentCore Runtime

- [ ] Deploy agent to Bedrock AgentCore Runtime.
- [ ] Wire Code Interpreter for sandboxed dynamic execution.
- [ ] Promote recurring dynamic operations to named tools.
- [ ] Full methodology playbook execution.
- [ ] Observability via AgentCore/CloudWatch.

## V1.x — Operator Interface

- [ ] Web UI for starting runs and viewing status.
- [ ] Checkpoint approval UI.
- [ ] Report viewer/download.

## Future

- [ ] Redshift integration for historical analytics.
- [ ] Multi-client concurrent runs.
- [ ] CI/CD pipeline for tool/methodology deployment.
- [ ] Automated regression tests against known-good reports.
