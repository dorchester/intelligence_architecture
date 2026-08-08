# Thin Slice Methodology — V0

## Scope

This methodology covers the minimal end-to-end path:
one dataset → one analysis → one chart → one report.

## Steps

1. **Initialize run context** with client_id, client_name, unique run_id.
2. **Load synthetic CSV** into the run's input directory.
3. **Run workforce analysis** — compute headcount, department distribution,
   tenure statistics, and turnover risk percentages.
4. **Generate chart** — horizontal bar chart of department headcount.
5. **Produce narrative** — summarize key findings in 2–3 sentences.
   (V0: stubbed; future: Bedrock LLM call.)
6. **Render report** — populate HTML template with metrics, chart, narrative,
   and provenance metadata.
7. **Write output** — save report.html to run's output directory.

## Success Criteria

- report.html exists in `runs/{run_id}/output/`.
- All metrics are derived from the input CSV.
- Chart matches the computed department breakdown.
- No external API calls required for the local thin slice.
