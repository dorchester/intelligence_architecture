# Infrastructure

All AWS infrastructure is managed via CloudFormation templates in the
`cloudformation/` directory.

## Stacks

| Stack | Template | Resources |
|-------|----------|-----------|
| intelligence-engine-dev-storage | cloudformation/storage.yaml | S3 bucket for run artifacts |
| intelligence-engine-dev-state | cloudformation/state.yaml | DynamoDB table for run state |

## Deployment

```bash
# Deploy storage (S3 bucket)
aws cloudformation deploy \
  --profile intelligence-dev \
  --region us-east-1 \
  --template-file infrastructure/cloudformation/storage.yaml \
  --stack-name intelligence-engine-dev-storage \
  --parameter-overrides Environment=dev \
  --tags Application=intelligence-engine Environment=dev ManagedBy=cloudformation

# Deploy state (DynamoDB)
aws cloudformation deploy \
  --profile intelligence-dev \
  --region us-east-1 \
  --template-file infrastructure/cloudformation/state.yaml \
  --stack-name intelligence-engine-dev-state \
  --parameter-overrides Environment=dev \
  --tags Application=intelligence-engine Environment=dev ManagedBy=cloudformation
```

## Tagging Convention

All resources are tagged with:
- `Application`: `intelligence-engine`
- `Environment`: `dev` | `staging` | `prod`
- `ManagedBy`: `cloudformation`

## Cost Profile

Both resources are pay-per-use with near-zero idle cost:
- **S3**: standard storage pricing, Intelligent Tiering after 30 days
- **DynamoDB**: on-demand (PAY_PER_REQUEST), no provisioned capacity

## Future Components

- **AgentCore Runtime**: production agent execution environment
- **AgentCore Code Interpreter**: sandboxed dynamic code execution
- **CloudWatch**: integrated observability (logs, metrics, traces)
- **IAM Execution Roles**: least-privilege roles for agent runtime
