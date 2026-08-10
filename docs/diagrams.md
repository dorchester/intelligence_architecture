# Architecture diagrams, as source

Every diagram anyone needs to explain this platform, as Mermaid source —
copy any block into a README, a slide tool that speaks Mermaid, the
[Mermaid live editor](https://mermaid.live) (for PNG/SVG export), or GitHub
markdown (which renders these natively). Each diagram maps to one activity
from the [role guides](guides/), so a user standing up a deck for any
audience can start from the block that matches their story.

Conventions: rectangles are identities/surfaces, cylinders are storage,
diamonds are decisions. Names match the deployed resources
(`intelligence-engine-dev-*` prefixes dropped for readability).

---

## 1. The whole system on one page

```mermaid
flowchart LR
    subgraph "People"
        C[Consultant<br/>Cognito login]
        F[FDE<br/>workbench seat]
        S[Steward<br/>sts:AssumeRole]
        P[Platform engineer<br/>deployer seat]
    end
    subgraph "Product"
        APP[Hosted console<br/>App Runner]
        SF[Step Functions<br/>report-build]
        CB[CodeBuild<br/>stage-runner]
    end
    subgraph "Data plane"
        L[(landing/)]
        FD[(foundational/)]
        DV[(derived/)]
        CX[(contextualized/)]
        ST[(stewardship/)]
    end
    subgraph "Governance"
        CT[CloudTrail]
        SNS[Steward digest SNS]
    end
    C --> APP --> SF --> CB
    CB --> FD & DV & CX
    CB -- append-only --> ST
    S -- admits data --> L
    L -- conformance --> FD -- product-builder --> DV --> CX
    S -- reads --> ST
    ST -. escalations .-> SNS -.-> S
    F -- builds/tests --> CB
    P -- deploys via cfn-exec --> APP
    CT -. logs every call .-> SF
```

## 2. A report run, end to end (the consultant's activity)

```mermaid
sequenceDiagram
    actor Consultant
    participant Console as Console (apprunner-instance)
    participant SFN as Step Functions (workflow)
    participant CB as CodeBuild (stage-runner)
    participant S3 as derived/ + artifacts
    Consultant->>Console: pick client, start run
    Console->>Console: entity verification (fails fast on nonsense)
    Console->>SFN: StartExecution {run_id, client_id, stage_image@digest}
    SFN->>CB: run stages (image pinned by digest)
    CB->>S3: read governed tiers, write artifacts
    SFN-->>Consultant: MIDPOINT checkpoint (waitForTaskToken - zero cost while waiting)
    Consultant->>SFN: {decision: revise, feedback: "..."}
    SFN->>CB: re-run stages with REVISION_FEEDBACK (bounded by max_revisions)
    SFN-->>Consultant: FINAL checkpoint
    Consultant->>SFN: {decision: approve}
    SFN->>S3: publish artifacts
    Console-->>Consultant: briefing
```

## 3. Data admission through the medallion tiers (the steward's + data engineer's activity)

```mermaid
flowchart LR
    SRC[Source dataset] -->|steward signoff +<br/>landing-write| L[(landing/ L1)]
    L -->|conformance role:<br/>drop identifiers, band ages| F[(foundational/ L2<br/>pseudonymous)]
    F -->|product-builder role:<br/>aggregate, suppress cells < 5| D[(derived/ L3<br/>aggregates)]
    F -->|product-builder role:<br/>embed + graph| X[(contextualized/ L4<br/>vectors + graph)]
    D --> T[Governed tool /<br/>console briefings]
    X --> R[Hybrid search /<br/>GraphRAG retrieval]
    G{{gate_engagement_permissibility<br/>gate_anonymization}} -.checks every write.-> F & D & X
    G -->|BLOCK / WARN / NOTE| ST[(stewardship/<br/>append-only)]
```

## 4. The deploy channel (the platform engineer's activity)

```mermaid
flowchart LR
    DEV[FDE edits<br/>infrastructure/*.yaml] -->|commit + PR| GIT[git main]
    GIT --> PE[Platform engineer<br/>assumes deployer seat]
    PE -->|"deploy.sh --cfn-role"| CFN[CloudFormation]
    CFN -->|assumes| EXEC[cfn-exec<br/>trusts ONLY cloudformation.amazonaws.com]
    EXEC -->|creates/updates| RES[Stack resources]
    PE -. cannot touch resources directly .-> RES
    HUMAN[Any human] -. cannot assume .-> EXEC
    CT[CloudTrail] -.logs every stack operation.- CFN
```

## 5. Pipeline release: candidate → blessed (the FDE + platform engineer handoff)

```mermaid
flowchart LR
    E[FDE edits stages/] --> B["remote_build.sh --stage-image<br/>(CodeBuild, no local Docker)"]
    B --> DG["candidate digest sha256:..."]
    DG --> H["start_harness_run.py --stage-image candidate<br/>(blessed digest still serves everyone else)"]
    H --> V{tests + golden replay +<br/>seeded-defect eval pass?}
    V -- no --> E
    V -- yes --> PR[merge to main]
    PR --> PM["platform engineer:<br/>promote_stage_image.py"]
    PM --> SSM[("SSM blessed-image<br/>(digest-pinned)")]
    SSM --> RUNS[all runs without explicit<br/>--stage-image use blessed]
    PM -. prints previous digest .-> RB[rollback = re-promote previous]
```

## 6. The access model at a glance (who can block what)

```mermaid
flowchart TB
    subgraph "Critical to execution"
        C[Consultant - own checkpoints only]
    end
    subgraph "Critical to engineering"
        F[FDE - workbench]
        P[Platform engineer - deployer seat]
    end
    subgraph "Accountable, never blocking"
        S[Steward - admission before, gates-in-code during, audit after]
        A[Analyst - Databricks read-only]
        AD[Admin - dormant: seating + recovery only]
    end
    C --> RUN[report runs]
    F & P --> ENG[system changes]
    S -.-> RUN
    S -.-> ENG
```

## 7. The audit surface (the steward's / auditor's activity)

```mermaid
flowchart LR
    subgraph "Writers - cannot read what they write"
        CONF[conformance]
        PB[product-builder]
        SR[stage-runner]
    end
    CONF & PB & SR -->|stewardship-write<br/>append-only| LOG[(stewardship/<br/>gate-events + tool-calls)]
    LOG -->|stewardship-read<br/>steward seat ONLY| STW[Data steward]
    LOG -. escalations .-> SNS[SNS digest] -.-> STW
    ALL[Every identity] --> CT[(CloudTrail<br/>multi-region, validated,<br/>object-level data events)]
    CT -->|locked bucket -<br/>runtime roles cannot write| STW
```

## 8. The analyst's zero-copy path (Databricks)

```mermaid
flowchart LR
    LK[(Lakehouse tiers<br/>S3, in place)] -->|external location<br/>databricks-uc role, READ ONLY| UC[Unity Catalog]
    UC --> SQL[SQL over derived/]
    UC --> AIQ["ai_query() batch inference"]
    UC --> NB[notebook exploration<br/>of vectors + graph]
    NB -. no write path exists .-> LK
    REQ[needs a new product?] --> FDE[reviewed stage change<br/>via the FDE] --> LK
```

## 9. Peer benchmarks: crossing clients without breaking isolation

```mermaid
flowchart LR
    subgraph "Per-client, governed"
        A[(client A<br/>derived/)]
        B[(client B<br/>derived/)]
        C[(client C<br/>derived/)]
        D[(client D<br/>derived/)]
    end
    A & B & C & D -->|"read in place, read-only credential"| UC[Unity Catalog view<br/>workforce_composition_all]
    UC -->|"one SQL statement:<br/>median share by seniority"| AGG{{"suppression:<br/>>= 3 contributing clients"}}
    AGG -->|"aggregate rows only"| ST["stages/peer_benchmarks.py<br/>(AWS writes, Databricks does not)"]
    ST --> P[(derived/peer_benchmarks/)]
    ST -.registers lineage.-> G[Glue catalog of record]
    P -->|"a run reads the PRODUCT,<br/>never another client's rows"| RUN[Report run<br/>scoped to one client]
    ISO{{cross_company_isolation<br/>guardrail}} -.still holds.-> RUN
```

Isolation is preserved because the crossing happens **upstream of any run**
and only a suppressed aggregate survives it. Databricks holds no write path:
it computes, the stage writes, and Glue stays the catalog of record.

---

## Regenerating and extending

- These render natively on GitHub and in the hosted docs; for slide decks,
  paste a block into [mermaid.live](https://mermaid.live) and export SVG/PNG.
- When an activity changes, change its diagram in the same PR — diagrams
  here are documentation of record, reviewed like code.
- To diagram a new activity: copy the closest block, keep the conventions
  (identity → storage → decision shapes; deployed-resource names), and link
  it from the relevant role guide.
