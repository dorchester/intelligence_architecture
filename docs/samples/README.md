# Sample Data

Real output from the running system, committed so you can understand what the
engine produces **without needing AWS access**.

Everything here is synthetic. No real company or person is represented.

| File | What it is |
|---|---|
| `dataset_manifest.json` | All 20 client datasets with profile and posting counts |
| `sample_profiles.json` | Three employee profiles from `sterling-pharma` |
| `sample_postings.json` | Three job postings from the same company |
| `sample_dataset_summary.json` | The full ~30-signal summary the query layer computes |
| `sample_agent_context.txt` | **The exact text block injected into every LLM prompt** |

## Why `sample_agent_context.txt` matters most

This is the single most useful file for understanding the system. It is verbatim
what Claude receives alongside each prompt — the measured workforce evidence the
briefing is grounded in. Reading it tells you precisely what the model can and
cannot know about a company.

```
WORKFORCE DATASET — Sterling Life Sciences
Industry: Pharmaceuticals / Biotechnology | Reported headcount: 43,000
Sample: 500 employee profiles, 500 job postings (24 months)

WORKFORCE COMPOSITION
  Pharmaceutical Manufacturing & Operations: 103 (20.6%) | avg tenure 2.9y | flight risk 23%
  Commercial & Sales: 76 (15.2%) | avg tenure 2.9y | flight risk 37%
  Research & Drug Discovery: 66 (13.2%) | avg tenure 3.1y | flight risk 18%
  ...

TENURE & RETENTION
  Average tenure: 3.1 years
  Flight risk (elevated): 31.8% of workforce
  External hires vs internal promotions: 76% external
  ...
```

## Regenerating

These files are exported from S3 by:

```bash
python scripts/export_samples.py --profile intelligence-dev
```

## Note on the data

The departments, titles, and skills are industry-accurate because Bedrock
generates the organizational template per company before Python fills in volume.
A pharmaceutical client produces *Clinical Development & Medical Affairs* and
*GCP compliance*; a logistics client produces *Fleet Operations* and
*DOT regulations*. The numbers are synthetic; the shape is realistic.
