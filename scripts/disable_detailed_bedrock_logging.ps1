# Disable Detailed Bedrock Model Invocation Logging
#
# Usage:
#   .\scripts\disable_detailed_bedrock_logging.ps1

$ErrorActionPreference = "Stop"
$Profile = "intelligence-dev"
$Region = "us-east-1"

Write-Host "Disabling detailed Bedrock invocation logging..."

aws bedrock delete-model-invocation-logging-configuration `
  --profile $Profile `
  --region $Region

Write-Host ""
Write-Host "Detailed invocation logging DISABLED." -ForegroundColor Green
Write-Host "Standard CloudWatch Bedrock metrics (tokens, latency) are NOT affected."
Write-Host ""
