# Corporate Deployment Architecture

## Context

This document describes how to deploy the Intelligence Engine within a
corporate consulting environment where:

- DevOps/Platform Engineering owns infrastructure and IaC
- AI/ML engineers iterate on agent behavior, prompts, tools, and methodology
- Consultants (operators) use the tool but don't see infrastructure
- AWS Console access is restricted or disabled by default
- Network egress is controlled
- All changes must be auditable

## Personas and Their Boundaries

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CORPORATE ENVIRONMENT                             │
│                                                                          │
│  ┌──────────────┐   ┌──────────────────────┐   ┌────────────────────┐  │
│  │  Consultant  │   │    AI Engineer        │   │   DevOps / Infra   │  │
│  │  (Operator)  │   │                       │   │                    │  │
│  │              │   │  - Claude Code (local) │   │  - Owns IaC        │  │
│  │  - Web UI    │   │  - GitHub (PR-based)  │   │  - Deploys stacks  │  │
│  │  - Approves  │   │  - AgentCore console  │   │  - Manages IAM     │  │
│  │  - No infra  │   │  - Limited AWS access │   │  - Network/VPN     │  │
│  │              │   │  - Bedrock access      │   │  - Audit/logging   │  │
│  └──────┬───────┘   └──────────┬────────────┘   └─────────┬──────────┘  │
│         │                      │                           │             │
└─────────┼──────────────────────┼───────────────────────────┼─────────────┘
          │                      │                           │
          ▼                      ▼                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            AWS ACCOUNT                                    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  VPC (Private Subnets)                                           │    │
│  │                                                                  │    │
│  │  ┌──────────────┐  ┌───────────────┐  ┌─────────────────────┐  │    │
│  │  │  App Runner   │  │  AgentCore    │  │  Claude Code         │  │    │
│  │  │  or ECS       │  │  Runtime      │  │  Development Env     │  │    │
│  │  │               │  │               │  │  (EC2 or CodeCatalyst)│  │    │
│  │  │  Webapp for   │  │  Agent exec   │  │                      │  │    │
│  │  │  consultants  │  │  (production) │  │  SSH/SSM access      │  │    │
│  │  └───────┬───────┘  └───────┬───────┘  └──────────┬───────────┘  │    │
│  │          │                  │                      │              │    │
│  │          ▼                  ▼                      ▼              │    │
│  │  ┌─────────────────────────────────────────────────────────────┐ │    │
│  │  │                    Shared Services                           │ │    │
│  │  │                                                             │ │    │
│  │  │  S3 (artifacts)  │  DynamoDB (state)  │  Bedrock (LLM)     │ │    │
│  │  │  CloudWatch      │  Secrets Manager   │  CodeCommit/GitHub  │ │    │
│  │  └─────────────────────────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Design

### 1. Consultant Web UI (App Runner)

**What**: The Flask webapp that consultants use to start briefings and approve checkpoints.

**Deployment**: AWS App Runner (container from ECR)
- Scales to zero when no one is using it
- No VPC configuration needed (talks to Bedrock/S3/DynamoDB via IAM)
- Behind corporate SSO (SAML/OIDC via App Runner auth or ALB)
- No public internet exposure — internal only via PrivateLink or corporate VPN

**IAM Role** (minimal):
```yaml
- bedrock:InvokeModel (specific model ARNs only)
- s3:PutObject, s3:GetObject (scoped to runs/ prefix)
- dynamodb:PutItem, dynamodb:GetItem, dynamodb:Query (specific table)
- logs:CreateLogStream, logs:PutLogEvents
```

**DevOps owns**: Container build pipeline, ECR, App Runner service, IAM role, network config.

**AI Engineer owns**: Application code deployed via Git → CI/CD.

---

### 2. AgentCore Runtime (Production Agent Execution)

**What**: Where the agent actually reasons and invokes tools in production.
This is the managed serverless runtime for long-running agent sessions.

**Why AgentCore**: 
- Managed session lifecycle (supports 30-60 minute runs with checkpoints)
- Built-in tool invocation framework
- Code Interpreter for sandboxed dynamic Python
- Session suspend/resume (human-in-the-loop without burning tokens)
- CloudWatch integration for observability
- No infrastructure to manage

**What the AI Engineer does here**:
- Defines tool schemas (maps to our `tools/*.py`)
- Configures agent instructions (maps to `methodology/*.md` + `prompts/*.md`)
- Tests agent behavior via the AgentCore console or API
- Iterates on tool implementations via code deployment

**What DevOps provisions**:
- AgentCore agent definition
- IAM execution role for the agent
- Tool Lambda functions or inline tool handlers
- Code Interpreter sandbox configuration
- Network policies (agent can't reach arbitrary internet)

---

### 3. AI Engineer Development Environment

This is the critical piece. The forward-deployed engineer (FDE) needs to:
- Run Claude Code against the codebase
- Test Bedrock calls interactively
- Modify prompts, tools, methodology
- Deploy changes to staging
- Debug failed runs

**Option A: EC2 Development Instance (Recommended for V0)**

```
Engineer's laptop
    │
    │  SSH / SSM Session Manager (no inbound ports needed)
    ▼
┌─────────────────────────────────────┐
│  EC2 (t4g.medium, ARM, ~$0.03/hr)  │
│                                     │
│  - Claude Code CLI installed        │
│  - Git (connected to GitHub/CC)     │
│  - Python environment               │
│  - AWS credentials via IAM role     │
│  - Bedrock access (for testing)     │
│  - Can stop when not in use         │
│                                     │
│  IAM Instance Role:                 │
│  - bedrock:InvokeModel              │
│  - s3:* on project bucket           │
│  - dynamodb:* on project table      │
│  - ssm:* (for Session Manager)      │
│  - codecommit:* or github access    │
│  - NO IAM admin                     │
│  - NO console access                │
│  - NO organization changes          │
└─────────────────────────────────────┘
```

**How Claude Code works here**:
- Engineer SSMs into the EC2 instance
- Runs `claude` CLI directly
- Claude Code calls Bedrock for its own inference
- Claude Code reads/writes the project codebase
- Changes go through Git → PR → CI/CD → deploy

**Benefits**:
- No local credential management (IAM role attached to instance)
- Network-controlled (VPC, security groups, no surprise egress)
- Audit trail via CloudTrail + SSM session logs
- Stop instance when not engineering ($0 when stopped)
- Corporate can image/patch the instance via standard AMI pipeline

**Option B: Amazon CodeCatalyst Dev Environment**

AWS's managed cloud IDE/dev environment:
- Pre-built environments from devfile
- Automatically connects to CodeCatalyst repos
- IAM role attached to the environment
- Starts/stops on demand
- Claude Code could be installed as a CLI tool in the devfile

**Benefits**: Fully managed, no EC2 to patch. 
**Drawback**: Less flexible, newer service, may not support all Claude Code features.

**Option C: GitHub Codespaces + OIDC Federation**

If the firm uses GitHub Enterprise:
- Codespace configured with project devcontainer
- OIDC federation to assume an AWS role
- Claude Code runs in the codespace
- All work flows through GitHub PRs

**Benefits**: Familiar Git-native workflow.
**Drawback**: Data leaves AWS (GitHub's infra), may violate data residency requirements.

---

### 4. Deployment Pipeline

```
┌──────────────┐     ┌──────────────┐     ┌─────────────┐     ┌───────────┐
│  AI Engineer │────▶│   Git Push   │────▶│   CI/CD     │────▶│  Deploy   │
│  (Claude Code)│    │  (PR review) │     │  (validate  │     │  (staging │
│              │     │              │     │   test, scan)│     │   → prod) │
└──────────────┘     └──────────────┘     └─────────────┘     └───────────┘

CI/CD validates:
- pytest passes
- No credentials in code
- No hardcoded account IDs
- Prompt/methodology changes reviewed
- CloudFormation lint passes
- Container builds successfully
```

**AI Engineer can**: push code, create PRs, trigger staging deploys.
**AI Engineer cannot**: deploy directly to production, modify IAM, change VPC rules.
**DevOps approves**: infrastructure changes, IAM changes, production promotions.

---

## Security Model

### Network

```
Corporate Network
    │
    ├── VPN / DirectConnect ──▶ VPC Private Subnets
    │                              ├── App Runner (consultant UI)
    │                              ├── EC2 Dev (engineer)
    │                              └── AgentCore Runtime
    │
    └── Internet ──▶ BLOCKED (no direct internet for workloads)
                     Exception: Bedrock endpoints (VPC endpoint)
                     Exception: GitHub/CodeCommit (VPC endpoint or NAT)
```

### IAM Roles (Least Privilege)

| Role | Assigned To | Permissions |
|------|------------|-------------|
| `intelligence-engine-app` | App Runner task | Bedrock invoke, S3 read/write (scoped prefix), DynamoDB CRUD (scoped table) |
| `intelligence-engine-agent` | AgentCore runtime | Bedrock invoke, S3 read/write (run-scoped), Code Interpreter |
| `intelligence-engine-dev` | Engineer EC2 | Bedrock invoke, S3 full on project bucket, DynamoDB full on project table, SSM, CloudWatch read |
| `intelligence-engine-deploy` | CI/CD pipeline | ECR push, App Runner deploy, CloudFormation deploy (scoped stacks) |

### Audit

- **CloudTrail**: All API calls logged (who did what, when)
- **SSM Session Manager**: All engineer terminal sessions recorded
- **Git history**: All code changes attributed to individuals
- **DynamoDB**: All run state changes timestamped
- **CloudWatch**: All Bedrock invocations metered

---

## What DevOps Needs to Provision (IaC)

```yaml
# DevOps provisions these stacks:
1. intelligence-engine-network       # VPC, subnets, VPC endpoints, security groups
2. intelligence-engine-storage       # S3 bucket (already exists)
3. intelligence-engine-state         # DynamoDB table (already exists)  
4. intelligence-engine-observability # CloudWatch dashboard (already exists)
5. intelligence-engine-compute       # App Runner service, ECR repo
6. intelligence-engine-dev           # EC2 dev instance, instance profile
7. intelligence-engine-cicd          # CodePipeline/GitHub Actions role
8. intelligence-engine-agentcore     # AgentCore agent definition (when ready)
```

The FDE provides the application code and CloudFormation templates.
DevOps reviews and deploys infrastructure changes.

---

## Engineer's Day-to-Day Workflow

```
1. SSH/SSM into dev instance
   $ aws ssm start-session --target i-xxxx --profile corporate

2. Open Claude Code
   $ claude

3. Work on the project
   - Modify prompts/methodology
   - Add/change analytical tools
   - Adjust agent behavior
   - Run local tests (pytest)
   - Test Bedrock calls interactively

4. Push changes
   $ git push origin feature/improve-narrative

5. CI validates, runs tests, scans for secrets

6. PR reviewed (by another engineer or lead)

7. Merge → auto-deploy to staging

8. Validate in staging (run a test engagement)

9. Promote to production (DevOps approves)
```

---

## Migration Path from Current Sandbox

| Current (Sandbox) | Target (Corporate) | Change Required |
|---|---|---|
| Local Flask | App Runner behind corporate auth | Container + deploy pipeline |
| `intelligence-dev` SSO profile | IAM role on EC2/App Runner | DevOps provisions roles |
| AWS Console access | No console (SSM + CLI only) | Engineer uses Claude Code + AWS CLI |
| Manual CFN deploys | CI/CD pipeline deploys | DevOps sets up pipeline |
| Public GitHub repo | Private repo (GitHub Enterprise or CodeCommit) | Move or fork repo |
| Bedrock on-demand | Bedrock with guardrails + VPC endpoint | DevOps adds VPC endpoint + guardrail |
| No network controls | VPC private subnets + security groups | DevOps provisions network stack |

---

## What This Repository Already Provides for Corporate

- [x] CloudFormation templates for all data resources
- [x] No hardcoded credentials or account IDs
- [x] Storage abstraction (swaps local↔S3 without code changes)
- [x] Run isolation by construction (client/run prefix)
- [x] Structured logging with run_id correlation
- [x] Methodology as version-controlled Markdown
- [x] Deterministic tools separated from agent reasoning
- [x] Model configurable at runtime (not hardcoded)
- [x] Synthetic-only test data
- [ ] Containerized webapp (Dockerfile needed)
- [ ] CI/CD pipeline definition
- [ ] VPC endpoint CloudFormation
- [ ] Guardrail configuration
- [ ] AgentCore agent definition
- [ ] Production IAM role definitions
