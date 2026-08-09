# Role guides

One guide per human seat. Each covers the real use cases for that role — the
commands are the ones that actually work against the deployed system, not
illustrations.

| Guide | Role | Seat |
|---|---|---|
| [`fde.md`](fde.md) | Forward-deployed engineer (FDE) | Workbench (EC2 + Claude Code, SSM-only) |
| [`platform-engineer.md`](platform-engineer.md) | Platform engineer | Deployer seat (`sts:AssumeRole`) |
| [`data-steward.md`](data-steward.md) | Data steward | Steward seat (`sts:AssumeRole`) |
| [`analyst.md`](analyst.md) | Data scientist / analytics engineer | Databricks (Unity Catalog, read-only) |
| [`consultant.md`](consultant.md) | Consultant / engagement lead | Hosted console (Cognito) |
| [`admin.md`](admin.md) | Account admin | SSO — dormant by design |

Two conventions apply across all of them:

1. **One hat at a time.** A person holding several functions assumes the seat
   for the function they are performing — steward seat for steward work,
   deployer seat for deploys — never raw admin. The audit trail should read
   as functions, not people.
2. **Nobody blocks what they don't own.** Only the consultant is critical to
   a run (their own checkpoints); only the FDE and platform engineer are
   critical to engineering. Every other role's controls act before
   (admission), during-but-automatically (gates in code), or after (audit) —
   see [`../access-model.md`](../access-model.md).
