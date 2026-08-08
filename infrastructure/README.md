# Infrastructure

This directory will contain infrastructure-as-code for the Intelligence Engine's
AWS deployment. **No deployable infrastructure exists yet.**

## Planned AWS Components

### Amazon Bedrock
- **Purpose**: LLM inference (Claude) for narrative generation and agent reasoning.
- **Configuration**: Model access, inference profiles, guardrails.

### Amazon Bedrock AgentCore Runtime
- **Purpose**: Production agent execution environment.
- **Configuration**: Agent definition, tool bindings, session management.
- **Key feature**: Manages long-running (30–60 min) asynchronous agent sessions
  with human checkpoint support.

### AgentCore Code Interpreter
- **Purpose**: Isolated sandbox for runtime-generated Python/shell execution.
- **Configuration**: Ephemeral execution environment scoped to a single run.
- **Constraint**: No network access, no persistent storage outside run prefix.

### Amazon S3
- **Purpose**: Durable storage for run artifacts.
- **Structure**:
  ```
  s3://{bucket}/runs/{run_id}/input/    — source data
  s3://{bucket}/runs/{run_id}/working/  — intermediate artifacts
  s3://{bucket}/runs/{run_id}/output/   — final report
  ```
- **Access**: IAM-scoped per run to enforce isolation.

### Amazon DynamoDB
- **Purpose**: Authoritative run and checkpoint state.
- **Schema** (provisional):
  - Partition key: `run_id`
  - Attributes: client_id, stage, created_at, checkpoints, versions.

### Amazon CloudWatch
- **Purpose**: Observability — logs, metrics, traces.
- **Integration**: AgentCore native logging plus custom metrics.

## Deployment Approach (Future)

Infrastructure will be defined using either:
- AWS CDK (Python), or
- Terraform

Decision deferred until V0.2+ when we begin provisioning resources.

## What This Directory Does NOT Contain

- No AWS credentials or account IDs.
- No deployable CloudFormation/CDK/Terraform templates (yet).
- No live configuration pointing to real environments.
