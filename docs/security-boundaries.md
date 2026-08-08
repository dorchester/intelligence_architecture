# Security Boundaries

## Core Principle

Security in the Intelligence Engine is enforced through **structural
mechanisms**, not prompts. Prompts guide behavior but are not security controls.

## Boundary Definitions

### 1. Prompts Are Not a Security Boundary

LLM prompts (system prompts, methodology playbooks, instructions) shape agent
behavior but cannot prevent a sufficiently adversarial input from causing
unintended actions. Therefore:

- Access control is enforced by IAM policies, not by prompt instructions.
- Data isolation is enforced by path/prefix scoping, not by telling the agent
  "do not access other clients' data."
- Code execution boundaries are enforced by sandboxing, not by asking the agent
  to limit itself.

Prompts define *intent*. Infrastructure enforces *constraints*.

### 2. Run Isolation Is Structural

Each run operates within a scoped prefix:

```
runs/{run_id}/input/
runs/{run_id}/working/
runs/{run_id}/output/
```

In production (S3), this becomes an IAM-scoped prefix. The agent's session
credentials grant access only to its own run's prefix. A run cannot read,
write, or list objects belonging to another run — not because it is told not to,
but because its credentials do not permit it.

### 3. Permanent Code Changes Flow Through Git

The production agent **cannot**:
- Modify its own deployed source code.
- Alter methodology playbooks that will persist beyond the current run.
- Change tool implementations.
- Update infrastructure configuration.

All permanent changes follow the engineering workflow:
```
Engineer → Git commit → Pull request → Review → Merge → Deploy
```

The agent may suggest changes, but it cannot enact them in production outside
this pipeline.

### 4. Runtime-Generated Code Is Temporary

During a run, the agent may generate and execute Python code for:
- Data repair or transformation.
- Ad-hoc calculations not covered by existing tools.
- Format conversions.

This code:
- Executes inside an isolated sandbox (AgentCore Code Interpreter).
- Has access only to the current run's data.
- Is **not** persisted as part of the deployed codebase.
- Is logged for audit but has no lasting effect on the system.

If the engineer observes that a particular dynamic operation recurs across runs,
they may promote it to a named, tested, version-controlled tool — through the
standard Git workflow.

### 5. Client Data Never Enters This Repository

This repository is public. It contains:
- Architecture and methodology (generic, non-proprietary).
- Code (tools, templates, agent logic).
- Sample/synthetic data (fictional organizations only).
- Documentation.

It does **not** and must **never** contain:
- Real client data.
- Client names or identifiers.
- Proprietary purchased datasets.
- Internal consulting-firm data.
- AWS credentials, account IDs, or connection strings.
- Generated reports from actual client engagements.

Client data lives exclusively in the production AWS environment, scoped by
client and run, and is never extracted into source control.

## Summary Table

| Boundary | Enforcement Mechanism | NOT Enforced By |
|----------|----------------------|-----------------|
| Data isolation | IAM + prefix scoping | Prompts |
| Code immutability | Deployment pipeline | Agent self-restraint |
| Execution sandbox | Container/runtime isolation | Instructions |
| Credential access | IAM roles + session scoping | Configuration files |
| Public repo safety | .gitignore + CI checks + review | Developer memory |
