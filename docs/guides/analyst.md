# Data scientist / analytics engineer

You work the data: exploration, validation, models, SQL over the governed
tiers. Your surface is **Databricks**, reading the lakehouse in place through
Unity Catalog — zero-copy, no exports, no AWS credentials in your hands at
any point. The `databricks-uc` role that makes this possible is read-only
and revocable by deleting one role.

---

## 1. What you see, and what each tier is for

The external location maps the lakehouse bucket; the tiers appear as
folders/tables:

| Tier | Contents | Use it for |
|---|---|---|
| `foundational/` | Pseudonymous records — direct identifiers dropped, ages banded | Feature work and modeling that needs record grain. **Treat as personal data**: identifiers are gone but re-identification from quasi-identifiers is not impossible — that's why it isn't called anonymous |
| `derived/` | Aggregate products (e.g. `workforce_composition`), small cells suppressed | BI, dashboards, anything shared onward. This is the tier whose outputs are designed to leave |
| `contextualized/` | Embedding vectors + typed graph (nodes/edges) | Retrieval experiments, graph analysis |
| `stewardship/` | Not yours — steward-only audit log | — |

Every product table carries its governance as ordinary table properties:
`ie.owner` (the human who answers for it), `ie.lineage.source_table`,
`ie.aggregation`, `ie.contains_personal_data`. Read them before arguing with
a number — they say where it came from and what was suppressed.

## 2. Working patterns

**SQL over derived** is the everyday case — it's a normal external table.

**Batch inference** without moving data: Databricks `ai_query()` over a
tier, e.g. classifying or summarizing rows in SQL. Keep it off
`foundational/` unless your purpose clears the same bar the pipeline's own
enrichment does — the data doesn't stop being personal because the query
engine changed.

**Retrieval experiments**: `contextualized/profile_vectors` ships the
embedding beside its metadata columns precisely so you can prototype hybrid
search (filter first, then similarity) in a notebook.

## 3. The suppression rule you must not undo

`derived/` aggregates suppress cells below `MIN_CELL` (5). Joining,
differencing or unioning derived outputs to reconstruct suppressed cells
defeats the control the tier exists to provide. If an analysis genuinely
needs finer grain, that's a `foundational/`-tier question with its
`foundational/`-tier obligations — not a workaround.

## 4. Requesting what doesn't exist yet

You read; the pipeline writes. When you need a new product table, a new
column, or different aggregation:

- **New/changed product** → the data engineer (FDE seat): it's a change to
  `stages/build_derived.py` or `build_context.py`, reviewed and re-run under
  the `product-builder` identity, with lineage metadata updated.
- **New source entirely** → starts with the steward (admission), not with
  engineering.
- **Access you don't have** (e.g. a new schema) → the platform engineer; it's
  a template change to the external location / catalog grants.

## 5. What the connection can actually reach today

Worth stating plainly, because the picture above describes the intended
shape and the deployment is one step behind it:

- The Unity Catalog credential now holds **read on `derived/` and
  `contextualized/`** in the lakehouse — the governed products, granted in
  `databricks-access.yaml`. `foundational/` is deliberately excluded:
  record-grain pseudonymous data should be a separate, reviewed grant, not
  a side effect of connecting a BI tool.
- It **also still holds read on the raw dataset drop** (`datasets/*` in the
  runs bucket), which is what the existing `sterling_profiles` view reads —
  and that view exposes `first_name`, `last_name`, `full_name` and
  `headline`. That is pre-conformance data reaching an analyst surface, and
  it is the one place where the medallion model is currently bypassed.
- Turning the governed grant into queryable tables needs one Databricks-side
  step that IAM cannot do for you: an **external location** over
  `s3://<lakehouse>/derived/` bound to the existing storage credential, then
  views or external tables on top. Until that exists, a `read_files()` call
  against the lakehouse returns `UNAUTHORIZED_ACCESS` even though the AWS
  role permits it.

The honest summary: AWS-side access to the governed tiers is now correct;
the Unity Catalog objects that expose them are not yet created, and the raw
view should be retired once they are.

## 6. Why you can't write

The `databricks-uc` role holds no write on anything — so nothing you do in a
notebook can corrupt a governed tier, mislabel lineage, or leak into the
pipeline's inputs. Your outputs live in your own workspace until they've
earned a place in the pipeline through the front door (a reviewed stage
change). This is what makes it safe to hand analysts real freedom on the
read side.
