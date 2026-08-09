#!/usr/bin/env bash
#
# Apply the Databricks account-level Terraform from the engineer workbench.
#
# Credentials come from SSM Parameter Store - never from files, never from
# arguments, never echoed. The workbench instance role has read access to
# exactly the /intelligence-engine/<env>/databricks/* path.
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-dev}"
REGION="${AWS_REGION:-us-east-1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAM="/intelligence-engine/${ENVIRONMENT}/databricks"

param() {
  aws ssm get-parameter --region "$REGION" --name "${PARAM}/$1" \
    ${2:-} --query Parameter.Value --output text
}

export TF_VAR_databricks_account_id="$(param account_id)"
export TF_VAR_client_id="$(param client_id)"
export TF_VAR_client_secret="$(param client_secret --with-decryption)"
export TF_VAR_environment="$ENVIRONMENT"

# State bucket resolved at run time so the account-id-bearing bucket name
# stays out of committed code.
BUCKET="$(aws cloudformation describe-stacks --region "$REGION" \
  --stack-name "intelligence-engine-${ENVIRONMENT}-storage" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)"

cd "$HERE"
terraform init -input=false \
  -backend-config="bucket=${BUCKET}" \
  -backend-config="key=terraform/databricks.tfstate" \
  -backend-config="region=${REGION}"

terraform apply -input=false -auto-approve
terraform output
