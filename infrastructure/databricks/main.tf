# Databricks account-level resources.
#
# Terraform rather than CloudFormation because Databricks account objects
# (workspaces, storage credentials) are not AWS resources. The AWS half of
# the integration - the Unity Catalog IAM role and the credential parameters -
# lives in infrastructure/cloudformation/databricks-access.yaml.
#
# A SERVERLESS workspace deliberately: no VPC, no NAT gateway, no cross-account
# compute role, no root S3 bucket, and compute that stops billing when it
# stops running. Classic compute would add a ~$32/month NAT gateway to run
# queries a laptop finishes instantly.
#
# Run from the engineer workbench (terraform and the AWS CLI are installed
# there, and its role can read the credential parameters):
#
#   ./infrastructure/databricks/apply.sh
#
# State lives in the project S3 bucket via -backend-config, so the bucket
# name (which embeds the AWS account id) never appears in this file.

terraform {
  required_version = ">= 1.5"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.60"
    }
  }

  backend "s3" {}
}

variable "databricks_account_id" {
  type        = string
  description = "Databricks account ID (from SSM /intelligence-engine/<env>/databricks/account_id)"
}

variable "client_id" {
  type        = string
  description = "Account-level service principal OAuth client ID"
}

variable "client_secret" {
  type        = string
  sensitive   = true
  description = "Service principal OAuth secret (from SSM, never from a file in this repo)"
}

variable "environment" {
  type    = string
  default = "dev"
}

provider "databricks" {
  host          = "https://accounts.cloud.databricks.com"
  account_id    = var.databricks_account_id
  client_id     = var.client_id
  client_secret = var.client_secret
}

resource "databricks_mws_workspaces" "engine" {
  account_id     = var.databricks_account_id
  workspace_name = "intelligence-engine-${var.environment}"
  aws_region     = "us-east-1"

  # Serverless workspaces must not set credentials_id or
  # storage_configuration_id - Databricks hosts the compute plane.
  compute_mode = "SERVERLESS"
}

output "workspace_url" {
  value = databricks_mws_workspaces.engine.workspace_url
}

output "workspace_id" {
  value = databricks_mws_workspaces.engine.workspace_id
}
