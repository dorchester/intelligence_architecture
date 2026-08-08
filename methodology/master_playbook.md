# Master Playbook — Intelligence Engine

## Purpose

This playbook defines the high-level analytical workflow for an Intelligence
Engine run. It serves as the agent's primary guide for sequencing analysis,
quality checks, and report generation.

## Workflow Stages

### 1. Data Ingestion
- Receive client dataset reference (S3 path or local path).
- Validate expected schema: required columns, data types, row counts.
- Record data quality observations (nulls, outliers, duplicates).
- Stage: `DATA_LOADED`

### 2. Deterministic Analysis
- Execute registered analytical tools against validated data.
- Each tool produces a structured metrics dictionary.
- Tools are pure functions: same input → same output.
- Stage: `ANALYSIS_COMPLETE`

### 3. Interpretive Narrative
- Review metrics for notable patterns, risks, and anomalies.
- Generate narrative text grounded exclusively in computed metrics.
- Do not fabricate, extrapolate, or hallucinate data points.
- Stage: `NARRATIVE_COMPLETE`

### 4. Report Assembly
- Render final HTML report from template.
- Embed charts, metrics, and narrative.
- Include provenance metadata (run ID, versions, timestamp).
- Stage: `REPORT_GENERATED`

## Quality Principles

- **Reproducibility**: Given the same input and code version, the deterministic
  stages must produce identical results.
- **Traceability**: Every claim in the report must trace to a computed metric.
- **Separation**: Deterministic computation and LLM interpretation are distinct
  stages with distinct responsibilities.

## Human Checkpoints (Future)

Approximately five points where an operator may review, approve, or redirect:
1. After data validation — confirm dataset is correct.
2. After initial analysis — confirm scope is appropriate.
3. After narrative draft — approve interpretive framing.
4. Before final render — last-look review.
5. After report delivery — acknowledge completion.
