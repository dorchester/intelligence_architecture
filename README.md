# Intelligence Engine

An agentic analytical workflow that combines deterministic Python analysis with
LLM-driven interpretation to produce structured intelligence reports.

## What This Is

The Intelligence Engine follows a methodology-driven workflow:

1. **Ingest** structured data for a client.
2. **Analyze** it using deterministic, tested Python tools.
3. **Interpret** results using an LLM (Amazon Bedrock / Claude).
4. **Generate** a formatted HTML report with metrics, charts, and narrative.

Each run is isolated, traceable, and reproducible at the deterministic layer.

## Architecture (Summary)

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────┐
│  Methodology │────▶│   Agent (Claude)   │────▶│    Report    │
│  Playbooks   │     │  orchestrates the  │     │   (HTML)     │
└──────────────┘     │  analytical flow   │     └──────────────┘
                     └─────┬─────────┬────┘
                           │         │
                     ┌─────▼───┐ ┌───▼────────────┐
                     │  Tools  │ │  LLM Narrative  │
                     │ (Python)│ │  (Bedrock)      │
                     └─────────┘ └────────────────┘
```

- **Tools** are pure Python functions — deterministic, tested, versioned.
- **Methodology** is version-controlled Markdown that guides agent reasoning.
- **Agent** orchestrates the workflow, invoking tools and generating narrative.
- **Reports** are rendered from Jinja2 templates with embedded metrics and charts.

See [docs/architecture.md](docs/architecture.md) for the full architectural breakdown.

## Quick Start (Local V0)

### Prerequisites

- Python 3.11+
- pip or uv

### Install

```bash
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest
```

### Run the Thin Slice

```bash
python run_local.py
```

This will:
- Create a run directory under `runs/`.
- Analyze the synthetic workforce data.
- Generate a chart and HTML report.
- Print the path to the output report.

Open the generated `report.html` in a browser to view results.

## Project Structure

```
intelligence_architecture/
├── agent/          — orchestration and run context
├── methodology/    — workflow playbooks (Markdown)
├── tools/          — deterministic analytical tools (Python)
├── prompts/        — LLM prompt templates
├── templates/      — HTML report templates (Jinja2)
├── config/         — configuration (example only; real config gitignored)
├── sample_data/    — synthetic/fictional data for testing
├── tests/          — automated tests
├── infrastructure/ — future AWS infrastructure docs
├── docs/           — architecture and design documentation
└── run_local.py    — local thin-slice runner
```

## Key Design Decisions

1. **Deterministic analysis is separated from LLM narrative.** Tools produce
   reproducible metrics; the LLM interprets them. This makes the analytical
   layer testable and auditable independently.

2. **Methodology lives in Markdown, not rigid code.** Playbooks guide the agent's
   reasoning without reducing it to a mechanical DAG. They are versioned and
   the version is stamped on every report.

3. **Run isolation is structural.** Each run has its own directory prefix.
   In production, IAM policies enforce this boundary — it is never trust-based.

4. **No agent framework dependency yet.** The V0 orchestrator is plain Python.
   Framework selection (if needed) happens when we target AgentCore Runtime.

## Security

See [docs/security-boundaries.md](docs/security-boundaries.md).

Key principle: **prompts are not a security boundary**. Data isolation, code
immutability, and execution sandboxing are enforced by infrastructure, not
instructions.

## Status

**V0 — Local Thin Slice.** Runs entirely locally with synthetic data. No AWS
resources are created or modified. See [docs/roadmap.md](docs/roadmap.md) for
planned progression.

## License

MIT — see [LICENSE](LICENSE).
