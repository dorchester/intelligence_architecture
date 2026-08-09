# Reference stages

Four stages that exist to prove a *class* of work runs on this substrate, not
to be the workload. A workload repository supplies its own stages; these are
the miniatures an engineer can read, run, and scale up.

Each runs in the workflow stage image (`infrastructure/stage-image/`) and is
invoked by the harness through a buildspec in the execution input — see
[`docs/integration-contract.md`](../docs/integration-contract.md).

| Stage | Class it proves | Why it is the hard one |
|---|---|---|
| `conform_to_silver.py` | Governed data path | landing → conformed parquet under `silver/`, Glue table registered. Direct identifiers are dropped **here**, which is what makes the `silver-read` grant safe to hand out. |
| `analyse_workforce.py` | Statistics under a build budget | IPF raking to known margins, a logistic fit, Kish effective *n*, and a chart — inside a 30-minute `BUILD_GENERAL1_SMALL` container. |
| `enrich_profiles_llm.py` | Multi-step LLM orchestration | Batched extraction, bounded concurrency against a 50 req/min quota, structured output with retries, second-pass synthesis, and an advisory reviewer that never gates. |
| `render_report.py` | Blocking QA gate | Loads the artifact in real Chromium and fails the build if it does not render. |

Shared helpers live in `_aws.py` (resolution and IO) and `_bedrock.py` (the
model wrapper — prompt caching, throttle handling, structured output).

## Running one

```bash
python stages/conform_to_silver.py sterling-pharma
```

Locally that needs credentials and the scientific stack. In the harness it
needs neither: the image carries the libraries and the CodeBuild role carries
the grants.

## What these deliberately do not do

They do not implement the workload's actual analysis. The numbers are
computed correctly but the questions are stand-ins. The point is that a
reviewer can confirm the *shape* — a stage can read governed rows, hold a
statistical model in memory, call a model under a quota, write an artifact,
and fail a build — before any real methodology is ported onto it.
