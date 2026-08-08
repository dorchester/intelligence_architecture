# Architecture

## Overview

The Intelligence Engine is an agentic analytical workflow that combines
deterministic Python analysis with LLM-driven interpretation to produce
structured intelligence reports.

## Deployed Architecture (V0)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Operator (CLI: orchestrator.py)                    │
│  - Start runs        - Check status        - Download reports        │
│  - Approve checkpoints                                               │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────┐
│                   Agent Engine (agent/engine.py)                      │
│                                                                      │
│  - Reads methodology playbook (methodology/thin_slice.md)            │
│  - Reasons about next step via Bedrock Claude tool-use               │
│  - Invokes deterministic tools based on methodology                  │
│  - Generates narrative as part of reasoning                          │
│  - Logs every turn, tool call, and timing                            │
│                                                                      │
│  Model: Claude Sonnet 4.6 via Bedrock inference profile              │
│  API: Bedrock InvokeModel with tool definitions                      │
└────────┬────────────────────────────────────────────┬───────────────┘
         │                                            │
┌────────▼────────────────┐          ┌────────────────▼──────────────┐
│  Deterministic Tools     │          │  Run State (DynamoDB)          │
│                          │          │                                │
│  tools/basic_analysis.py │          │  - Stage tracking              │
│  tools/chart.py          │          │  - Checkpoint lifecycle        │
│  tools/report.py         │          │  - Provenance metadata         │
│                          │          │  - Client GSI for querying     │
│  Pure Python, tested,    │          │                                │
│  version-controlled      │          │  Table: intelligence-engine-   │
└────────┬────────────────┘          │    dev-runs                    │
         │                            └────────────────┬──────────────┘
         │                                             │
┌────────▼─────────────────────────────────────────────▼──────────────┐
│              Persistent Artifacts (S3)                                │
│                                                                      │
│  Bucket: intelligence-engine-dev-runs-{account_id}                   │
│                                                                      │
│  Structure:                                                          │
│    runs/{client_id}/{run_id}/input/    ← source data                │
│    runs/{client_id}/{run_id}/working/  ← charts, metrics, narrative │
│    runs/{client_id}/{run_id}/output/   ← final report.html          │
│                                                                      │
│  Properties:                                                         │
│    - Versioned                                                       │
│    - Encrypted (AES-256)                                             │
│    - No public access                                                │
│    - TLS-only policy                                                 │
│    - Intelligent Tiering after 30d                                   │
└─────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | Location |
|-----------|---------------|----------|
| Orchestrator | Operator entry point, checkpoint flow | orchestrator.py |
| Agent Engine | Methodology-driven reasoning + tool dispatch | agent/engine.py |
| Model Abstraction | Configurable Bedrock model access | agent/model.py |
| Run Context | Per-run state and artifact access | agent/context.py |
| Storage | Abstract file I/O (local/S3) | storage/ |
| State Manager | DynamoDB run lifecycle | state/run_state.py |
| Workforce Analysis | Deterministic pandas analysis | tools/basic_analysis.py |
| Chart Generation | Matplotlib charts | tools/chart.py |
| Report Rendering | Jinja2 HTML templating | tools/report.py |
| Methodology | Agent guidance playbooks | methodology/ |
| Prompts | System/narrative prompt templates | prompts/ |

## Data Flow

```
1. Operator → orchestrator.py run --auto-approve
2. Input CSV → S3 (runs/{client_id}/{run_id}/input/)
3. DynamoDB: create run record, stage=data_loaded
4. Checkpoint: wait for approval
5. Agent Engine reads methodology, receives input file list
6. Agent reasons → calls run_workforce_analysis tool
7. Tool produces metrics → stored in working/metrics.json
8. Agent reasons → calls generate_chart tool
9. Chart PNG → stored in working/headcount_by_department.png
10. Agent generates narrative within its reasoning turn
11. Agent reasons → calls generate_report tool
12. Report HTML → stored in output/report.html
13. DynamoDB: stage=completed, output_location recorded
```

## Security Model

See [security-boundaries.md](security-boundaries.md) for the full security model.

Key points:
- Storage isolation is structural: client_id/run_id prefix in all paths
- No cross-client or cross-run access possible through normal interfaces
- S3 bucket is encrypted, versioned, private, TLS-only
- No credentials in repository
- Prompts guide behavior but do not enforce security boundaries

## Infrastructure

All infrastructure is defined as CloudFormation:

| Stack | Resources | Template |
|-------|-----------|----------|
| intelligence-engine-dev-storage | S3 bucket + policy | infrastructure/cloudformation/storage.yaml |
| intelligence-engine-dev-state | DynamoDB table + GSI | infrastructure/cloudformation/state.yaml |

## Observability

Two views:

**Operator**: run stage, checkpoint status, completion, report location
(via `orchestrator.py status <run_id>`)

**Engineer**: structured JSON logs with run_id, tool calls, turn timing,
model identifiers (via `--log-file` flag producing JSON lines)

## Configuration

Model selection is configurable at runtime:
- `--model` flag on orchestrator
- `ModelConfig` dataclass in agent/model.py
- No hardcoded model IDs in analytical code

## Known Sandbox-Specific Decisions

- `provider_data_share` is enabled on the Bedrock account for prototyping.
  This is NOT acceptable for production with confidential client data.
  Must be reviewed before any non-sandbox deployment.

## Future: AgentCore Runtime

The V0 architecture is designed to port to Amazon Bedrock AgentCore Runtime:
- Tool definitions map directly to AgentCore tool schemas
- Agent reasoning loop parallels AgentCore's agent runtime model
- Storage abstraction supports both local dev and S3 production
- Code Interpreter can be wired for sandboxed dynamic execution
