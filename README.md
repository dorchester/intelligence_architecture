# Intelligence Engine

An agentic analytical workflow that combines deterministic Python analysis with
LLM-driven interpretation to produce structured intelligence reports.

## What This Is

The Intelligence Engine follows a methodology-driven workflow:

1. **Ingest** structured data for a client.
2. **Analyze** it using deterministic, tested Python tools.
3. **Interpret** results using an LLM agent (Amazon Bedrock / Claude).
4. **Generate** a formatted HTML report with metrics, charts, and narrative.

The agent reads a Markdown methodology playbook, reasons about each step, and
invokes tools via the Bedrock tool-use API. Each run is isolated, traceable,
and reproducible at the deterministic layer.

## Architecture (Summary)

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────┐
│  Methodology │────▶│   Agent Engine     │────▶│   Report     │
│  Playbooks   │     │  (Bedrock Claude)  │     │  (S3 HTML)   │
└──────────────┘     │  reasons + tools   │     └──────────────┘
                     └─────┬─────────┬────┘
                           │         │
                     ┌─────▼───┐ ┌───▼──────────┐
                     │  Tools  │ │  Run State   │
                     │ (Python)│ │  (DynamoDB)  │
                     └─────────┘ └──────────────┘
```

- **Tools** are pure Python functions — deterministic, tested, versioned.
- **Methodology** is version-controlled Markdown that guides agent reasoning.
- **Agent** orchestrates the workflow via Bedrock tool-use API.
- **State** is tracked in DynamoDB with checkpoint support.
- **Artifacts** are stored in S3 with client/run isolation.

See [docs/architecture.md](docs/architecture.md) for the full architectural breakdown.

## Quick Start

### Prerequisites

- Python 3.11+
- AWS CLI configured with profile `intelligence-dev` (for AWS features)

### Install

```bash
pip install -e ".[dev]"
```

### Run Tests (No AWS Required)

```bash
pytest -v
```

### Run Locally (No AWS)

```bash
python run_local.py
```

### Run with Bedrock Narrative (Requires AWS)

```bash
python run_local.py --use-bedrock
```

### Full AWS End-to-End

```bash
python orchestrator.py run --auto-approve
```

This will:
- Create a unique run scoped by client_id and run_id
- Upload synthetic input to S3
- Pause at a checkpoint (auto-approved with flag)
- Run the agent through the methodology playbook
- Generate analysis, chart, narrative, and HTML report
- Store all artifacts in S3
- Track full lifecycle in DynamoDB

### Check Status / Download

```bash
python orchestrator.py status <run_id>
python orchestrator.py download <run_id>
```

## Project Structure

```
intelligence_architecture/
├── agent/              — agent engine, model abstraction, run context
├── methodology/        — workflow playbooks (Markdown)
├── tools/              — deterministic analytical tools (Python)
├── prompts/            — LLM prompt templates
├── templates/          — HTML report templates (Jinja2)
├── storage/            — storage abstraction (local + S3)
├── state/              — DynamoDB run state management
├── infrastructure/     — CloudFormation templates
│   └── cloudformation/
├── config/             — configuration (example only)
├── sample_data/        — synthetic/fictional data for testing
├── tests/              — automated tests
├── docs/               — architecture and design documentation
├── orchestrator.py     — full AWS orchestrator with checkpoints
├── run_local.py        — local runner (optional Bedrock)
└── run_s3.py           — S3-backed runner
```

## Key Design Decisions

1. **Deterministic analysis is separated from LLM narrative.** Tools produce
   reproducible metrics; the LLM interprets them.

2. **Methodology lives in Markdown, not rigid code.** Playbooks guide the agent's
   reasoning without reducing it to a mechanical DAG.

3. **Run isolation is structural.** Client/run prefix in storage and state.
   Never trust-based.

4. **Agent uses tool-use API.** Maps directly to AgentCore Runtime's model.

5. **Infrastructure is code.** CloudFormation stacks, not manual resources.

See [docs/decisions.md](docs/decisions.md) for the full decision log.

## Security

See [docs/security-boundaries.md](docs/security-boundaries.md).

Key principle: **prompts are not a security boundary**. Data isolation, code
immutability, and execution sandboxing are enforced by infrastructure, not
instructions.

## Status

**V0 — AWS Thin Slice Complete.** Full end-to-end with S3, DynamoDB, Bedrock
agent reasoning, deterministic tools, checkpoint approval, and HTML report
generation. See [docs/build-status.md](docs/build-status.md) for details.

## License

MIT — see [LICENSE](LICENSE).
