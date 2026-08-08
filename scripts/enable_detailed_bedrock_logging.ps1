# Enable Detailed Bedrock Model Invocation Logging
#
# WARNING: DETAILED INVOCATION LOGGING MAY CAPTURE CLAUDE CODE PROMPTS AND MODEL RESPONSES.
#
# This enables CloudWatch Logs-based invocation logging for Amazon Bedrock.
# Logs will contain request/response content for all Bedrock API calls.
#
# Prerequisites:
# - AWS CLI configured with intelligence-dev profile
# - The Bedrock service role must exist (created by observability-logging.yaml stack)
#
# Usage:
#   .\scripts\enable_detailed_bedrock_logging.ps1

$ErrorActionPreference = "Stop"
$Profile = "intelligence-dev"
$Region = "us-east-1"
$LogGroupName = "/aws/bedrock/invocation-logs"
$StackName = "intelligence-engine-dev-observability-logging"

Write-Host ""
Write-Host "WARNING: This will enable detailed Bedrock invocation logging." -ForegroundColor Yellow
Write-Host "Logs may contain prompts and model responses." -ForegroundColor Yellow
Write-Host ""

# Deploy the logging infrastructure stack
Write-Host "Deploying logging infrastructure..."
aws cloudformation deploy `
  --profile $Profile `
  --region $Region `
  --template-file infrastructure/cloudformation/observability-logging.yaml `
  --stack-name $StackName `
  --capabilities CAPABILITY_IAM `
  --parameter-overrides Environment=dev `
  --tags Application=intelligence-engine Environment=dev ManagedBy=cloudformation

# Get the role ARN from the stack
$RoleArn = aws cloudformation describe-stacks `
  --profile $Profile `
  --region $Region `
  --stack-name $StackName `
  --query "Stacks[0].Outputs[?OutputKey=='BedrockLoggingRoleArn'].OutputValue" `
  --output text

Write-Host "Using logging role: $RoleArn"

# Enable logging
$LoggingConfig = @{
    loggingConfig = @{
        cloudWatchConfig = @{
            logGroupName = $LogGroupName
            roleArn = $RoleArn
            largeDataDelivery = @{
                s3Config = $null
            }
        }
        textDataDeliveryEnabled = $true
        imageDataDeliveryEnabled = $false
        embeddingDataDeliveryEnabled = $false
        videoDataDeliveryEnabled = $false
    }
} | ConvertTo-Json -Depth 10

$TempFile = [System.IO.Path]::GetTempFileName()
$LoggingConfig | Out-File -FilePath $TempFile -Encoding utf8

aws bedrock put-model-invocation-logging-configuration `
  --profile $Profile `
  --region $Region `
  --cli-input-json "file://$TempFile"

Remove-Item $TempFile

Write-Host ""
Write-Host "Detailed invocation logging ENABLED." -ForegroundColor Green
Write-Host "Log group: $LogGroupName"
Write-Host "Retention: 7 days"
Write-Host ""
Write-Host "To disable: .\scripts\disable_detailed_bedrock_logging.ps1"
