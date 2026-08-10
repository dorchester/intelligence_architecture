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

## 5. How the connection is wired, and what it proves

Zero-copy is a claim worth testing rather than repeating. The chain is:

1. An **IAM role** (`databricks-uc`, from `databricks-access.yaml`) holding
   read on `derived/` and `contextualized/`. `foundational/` is deliberately
   excluded — record-grain pseudonymous data should be a separate, reviewed
   grant, not a side effect of connecting a BI tool.
2. A **storage credential** in Unity Catalog backed by that role, marked
   *limit to read-only use*.
3. An **external location** (`ie_dev_derived`) scoped to a single governed
   prefix, inheriting the read-only flag.

Creating step 3 runs a live permission check against AWS, and the result
states the governance model in Databricks' own words: **Read, List, Path
Exists, Assume Role and External ID Condition all pass; the only failures
are the write-dependent file-event resources**, because the credential
cannot write. A read-only analyst path that fails its write checks is
working exactly as designed.

With that in place, SQL over the governed aggregates runs with no copy, no
export, and no AWS credentials in the analyst's hands:

```sql
SELECT department, seniority_level, headcount, round(mean_tenure_years, 2) AS tenure
FROM read_files('s3://<lakehouse>/derived/workforce_composition/', format => 'parquet')
ORDER BY headcount DESC
```

Two boundaries show up the moment you push further:

- **You cannot write.** The credential is read-only, so nothing in a
  notebook can reach back into a governed tier.
- **You cannot publish a view either.** The catalog schema is owned by the
  infrastructure service principal, so `CREATE VIEW` returns
  `PERMISSION_DENIED`. Publishing an analyst-facing view is a reviewed
  change by the team that owns the pipeline — the same rule that governs
  everything else here, applied to the catalog.

### The one place the medallion model is still bypassed

The same credential **also** holds read on the raw dataset drop
(`datasets/*` in the runs bucket), which is what the pre-existing
`sterling_profiles` view reads — and that view exposes `first_name`,
`last_name`, `full_name` and `headline`. That is pre-conformance data on an
analyst surface. The governed path now exists beside it; retiring the raw
view, and splitting the credential in two so the analyst path cannot reach
`datasets/` at all, is the outstanding piece of work.

## 6. Peer benchmarks: the one thing only Databricks can do here

Every run is scoped to one client, and the `cross_company_isolation`
guardrail enforces it. So a report can say *"Clinical is 22% of headcount"*
but never *"…against a 17% peer median"* — the numbers that would answer
that live in other clients' data.

`stages/peer_benchmarks.py` closes that gap **without weakening the rule**.
The cross-client aggregation happens upstream of any run, in Databricks,
over the already-governed L3 tier; only a suppressed aggregate comes back,
and a run reads that *product* rather than another client's rows.

```
seniority      clients  median share           range  median tenure
Entry                4         41.9%      39.3-47.4%          1.14y
Mid                  4         21.4%      19.5-21.9%          2.75y
Senior               4         18.6%      13.6-23.0%          4.23y
Director             4         12.2%       9.5-12.7%          5.61y
VP/Executive         4          6.5%        5.0-6.8%          7.42y
```

Four design points worth knowing before you use it:

- **Databricks has no write path.** It computes; the stage writes; the
  catalog of record stays in Glue. Provenance is not split across two
  catalogues.
- **Suppression twice.** The L3 input is already suppressed, and a cell
  needs at least three contributing clients to survive here — a median over
  two clients can still be re-identifying.
- **Seniority, not department.** An empirical choice: every seniority level
  is shared by all onboarded clients, while the most widely shared
  department appears in only two. Department taxonomies are
  industry-specific, so a cross-industry department median compares things
  that are not alike.
- **Share, not headcount.** Absolute headcount mostly measures company size;
  seniority *shape* is comparable between a 400-person client and a
  40,000-person one.

It is **optional by construction**: missing parameters, an unreachable
warehouse or an empty result all log why and exit 0, and the report builds
exactly as it does without Databricks.

## 7. Why you can't write

The `databricks-uc` role holds no write on anything — so nothing you do in a
notebook can corrupt a governed tier, mislabel lineage, or leak into the
pipeline's inputs. Your outputs live in your own workspace until they've
earned a place in the pipeline through the front door (a reviewed stage
change). This is what makes it safe to hand analysts real freedom on the
read side.
