# Architectural Decisions

## AD-001: Bedrock Inference Profiles over Direct Model IDs

**Decision**: Use Bedrock inference profile IDs (e.g. `us.anthropic.claude-sonnet-4-6`)
rather than raw model IDs for invocation.

**Rationale**: AWS requires inference profiles for on-demand invocation of current
models. Direct model IDs return `ValidationException`. Inference profiles provide
cross-region routing and are the forward-compatible path.

## AD-002: Claude Sonnet 4.6 as Default Runtime Model

**Decision**: Use Claude Sonnet 4.6 as the V0 runtime model.

**Rationale**: Claude Fable 5 is ACTIVE in the inference profile list but returns
"unavailable for this account" on invocation — an AWS account-access limitation.
Sonnet 4.6 provides strong tool-use capabilities at lower cost and is confirmed
working. The model is configurable via `--model` flag; no code changes needed
when Fable becomes available.

## AD-003: Storage Abstraction (Local + S3)

**Decision**: Abstract storage behind a `StorageBackend` interface with `LocalStorage`
and `S3Storage` implementations.

**Rationale**: Enables development without AWS credentials while using the same code
paths. Tests run against `LocalStorage`; production uses `S3Storage`. The interface
enforces client_id/run_id scoping by construction.

## AD-004: Client/Run Prefix Structure in S3

**Decision**: S3 key structure is `runs/{client_id}/{run_id}/{category}/{filename}`.

**Rationale**: Structural isolation — any future IAM policy scoping to a
`runs/{client_id}/*` prefix automatically enforces multi-tenant isolation.
client_id is the outer scope because per-client IAM policies are more likely
than per-run policies.

## AD-005: DynamoDB PAY_PER_REQUEST

**Decision**: Use on-demand (pay-per-request) billing for the DynamoDB state table.

**Rationale**: Near-zero cost at low scale (5-10 consultants, occasional runs).
No provisioned capacity to manage or pay for when idle. Scales automatically
if needed.

## AD-006: Methodology-Driven Agent via Tool-Use API

**Decision**: The agent reads Markdown methodology as part of its system prompt
and uses the Bedrock tool-use API to invoke deterministic Python tools.

**Rationale**: This preserves the "agent reasons, tools compute" separation.
The agent decides *when* to call each tool based on methodology guidance,
but tools are deterministic and tested. This mirrors how the eventual
AgentCore Runtime will work (agent + registered tools).

## AD-007: Checkpoint as State Machine (Not Process Termination)

**Decision**: Checkpoints update DynamoDB state to `waiting_for_approval` and
pause in-process (V0). The approval resumes without restarting.

**Rationale**: Preserves in-memory context between checkpoint request and
resolution. For V0, the process blocks. For production (AgentCore), the
runtime would suspend the agent session and resume on callback.

## AD-008: provider_data_share Acknowledged as Sandbox-Only

**Decision**: Document that `provider_data_share` is enabled for prototyping
but must be disabled/reviewed before handling confidential client data.

**Rationale**: Required for the Fable experiment. Acceptable for synthetic data
in a personal sandbox. Not acceptable for production consulting engagements.
Explicitly tracked as a known issue rather than silently inherited.

## AD-009: No AgentCore Runtime in V0

**Decision**: V0 runs the agent loop locally in Python rather than deploying
to AgentCore Runtime.

**Rationale**: AgentCore Runtime adds deployment complexity without changing
the logical architecture. The V0 agent engine already uses the tool-use API
pattern that maps directly to AgentCore. Deployment to AgentCore is a
separate milestone that doesn't require rearchitecting.

## AD-010: CloudFormation over CDK/Terraform

**Decision**: Use CloudFormation directly for infrastructure-as-code.

**Rationale**: AWS-native, no additional tooling or compilation step. V0
infrastructure is simple (one bucket, one table). CDK/Terraform can be
introduced later if template complexity warrants it.
