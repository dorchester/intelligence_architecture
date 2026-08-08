# Architecture

## Overview

The Intelligence Engine is an agentic analytical workflow that combines
deterministic Python analysis with LLM-driven interpretation to produce
structured intelligence reports.

## Architectural Layers

```
┌─────────────────────────────────────────────────────────┐
│                  Operator Interface                       │
│  (Future: web UI for starting runs, checkpoints, reports)│
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────┐
│              Agent / Control Plane                        │
│                                                          │
│  - Orchestrates the workflow stages                       │
│  - Maintains RunContext (authoritative state)             │
│  - Follows methodology playbooks                         │
│  - Decides when to invoke tools vs. generate narrative   │
│  - Enforces quality gates                                │
│                                                          │
│  Runtime: Amazon Bedrock AgentCore (production)           │
│           Local Python orchestrator (development)         │
└────────┬────────────────────────────────┬───────────────┘
         │                                │
┌────────▼────────────┐    ┌──────────────▼──────────────┐
│ Deterministic Tools  │    │  Sandboxed Dynamic Execution │
│                      │    │                              │
│ - Named Python       │    │  - Temporary code generated  │
│   functions          │    │    during a run              │
│ - Pure: same input → │    │  - Data repair scripts       │
│   same output        │    │  - Ad-hoc calculations       │
│ - Version-controlled │    │  - Runs in isolated sandbox  │
│ - Tested             │    │  - Output captured, code     │
│                      │    │    discarded after run       │
│ Examples:            │    │                              │
│ - Workforce analysis │    │  Runtime: AgentCore Code     │
│ - Chart generation   │    │  Interpreter (production)    │
│ - Report rendering   │    │  Local subprocess (dev)      │
└────────┬────────────┘    └──────────────┬──────────────┘
         │                                │
┌────────▼────────────────────────────────▼──────────────┐
│              Persistent Data / Artifacts                  │
│                                                          │
│  Per-run structure:                                       │
│    runs/{run_id}/input/    — source data for this run    │
│    runs/{run_id}/working/  — intermediate artifacts      │
│    runs/{run_id}/output/   — final report and metadata   │
│                                                          │
│  Storage: S3 (production), local filesystem (dev)        │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│           Authoritative Workflow State                    │
│                                                          │
│  - RunContext: run_id, client_id, stage, timestamps      │
│  - Checkpoint approvals                                  │
│  - Version references (methodology, code)                │
│                                                          │
│  Storage: DynamoDB (production), in-memory (dev)         │
└─────────────────────────────────────────────────────────┘
```

## Key Design Principles

### Separation of Deterministic and Generative Work

Deterministic tools (analysis, charting, rendering) are pure Python functions
that produce identical output given identical input. They are tested, versioned,
and reviewable.

LLM-driven work (interpretation, narrative, reasoning) is bounded by methodology
playbooks and quality gates, but its output is non-deterministic. The system
tracks which model version and methodology version produced each output.

### Structural Isolation

- Each run operates on its own data in its own path prefix.
- A run cannot read another run's data by construction (path/prefix scoping).
- Production agents cannot modify their own deployed code.
- Temporary generated code lives only within the run's sandbox.

### Methodology as Version-Controlled Guidance

The Markdown playbooks are not rigid DAGs — they preserve space for agent
reasoning and judgment. But they are version-controlled and the version is
recorded in every report, enabling reproducibility audits.

## Data Flow (V0 Thin Slice)

```
Input CSV → Workforce Analysis Tool → Metrics Dict
                                          │
                                          ├→ Chart Tool → PNG
                                          │
                                          └→ Narrative (stub/LLM) → Text
                                                    │
                                          ┌─────────┘
                                          ▼
                                    Report Template → report.html
```

## Future Components

| Component | Purpose | AWS Service |
|-----------|---------|-------------|
| Agent runtime | LLM orchestration | Bedrock AgentCore |
| Inference | Claude model calls | Amazon Bedrock |
| Code sandbox | Dynamic execution | AgentCore Code Interpreter |
| Artifact storage | Run data/reports | S3 |
| State store | Run/checkpoint state | DynamoDB |
| Observability | Logs, traces, metrics | CloudWatch / AgentCore |
| Data warehouse | Historical analytics | Redshift (later) |
