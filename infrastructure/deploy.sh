#!/usr/bin/env bash
#
# Deploy all Intelligence Engine infrastructure.
#
# Every durable AWS resource this project uses is defined in the
# CloudFormation templates deployed here. Nothing is created by hand.
#
# Usage:
#   ./infrastructure/deploy.sh                    # deploy dev
#   ./infrastructure/deploy.sh --env staging      # deploy another environment
#   ./infrastructure/deploy.sh --dry-run          # show what would run
#
set -euo pipefail

PROFILE="${AWS_PROFILE:-intelligence-dev}"
REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="dev"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)      ENVIRONMENT="$2"; shift 2 ;;
    --profile)  PROFILE="$2";     shift 2 ;;
    --region)   REGION="$2";      shift 2 ;;
    --dry-run)  DRY_RUN=1;        shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$HERE/cloudformation"
PREFIX="intelligence-engine-${ENVIRONMENT}"

TAGS="Application=intelligence-engine Environment=${ENVIRONMENT} ManagedBy=cloudformation"

# stack-suffix : template : extra-capabilities
STACKS=(
  "storage:storage.yaml:"
  "state:state.yaml:"
  "observability:observability.yaml:"
)

echo "Intelligence Engine — infrastructure deploy"
echo "  profile      ${PROFILE}"
echo "  region       ${REGION}"
echo "  environment  ${ENVIRONMENT}"
echo

if [[ $DRY_RUN -eq 0 ]]; then
  echo "Verifying credentials..."
  aws sts get-caller-identity --profile "$PROFILE" --query Arn --output text
  echo
fi

for entry in "${STACKS[@]}"; do
  IFS=':' read -r suffix template caps <<< "$entry"
  stack="${PREFIX}-${suffix}"

  echo "── ${stack}"

  cmd=(aws cloudformation deploy
       --profile "$PROFILE"
       --region "$REGION"
       --template-file "${TEMPLATES}/${template}"
       --stack-name "$stack"
       --parameter-overrides "Environment=${ENVIRONMENT}"
       --no-fail-on-empty-changeset
       --tags $TAGS)

  if [[ -n "$caps" ]]; then
    cmd+=(--capabilities "$caps")
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    printf '   would run: %s\n' "${cmd[*]}"
  else
    "${cmd[@]}"
  fi
  echo
done

if [[ $DRY_RUN -eq 1 ]]; then
  echo "Dry run complete — nothing was deployed."
  exit 0
fi

echo "Stack outputs"
echo "─────────────"
for entry in "${STACKS[@]}"; do
  IFS=':' read -r suffix _ _ <<< "$entry"
  stack="${PREFIX}-${suffix}"
  aws cloudformation describe-stacks \
    --profile "$PROFILE" --region "$REGION" --stack-name "$stack" \
    --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" \
    --output text 2>/dev/null | sed 's/^/  /' || true
done

echo
echo "Next: generate the workforce datasets"
echo "  python scripts/data_generation/generate_all.py --profile ${PROFILE}"
