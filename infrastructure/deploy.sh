#!/usr/bin/env bash
#
# Deploy all Intelligence Engine infrastructure.
#
# Every durable AWS resource this project uses is defined in the
# CloudFormation templates deployed here. Nothing is created by hand.
#
# Phases:
#   1. storage / state / observability   pay-per-use, no standing cost
#   2. ecr + build                       registry and CodeBuild project
#   3. build + push console image        local Docker, or CodeBuild if absent
#   4. app                               App Runner + Cognito  <-- costs ~$5-10/mo
#   5. callback pass                     re-deploy app with the real service URL
#
# Usage:
#   ./infrastructure/deploy.sh                    # base stacks only
#   ./infrastructure/deploy.sh --with-app         # everything, including hosting
#   ./infrastructure/deploy.sh --with-app --build remote   # force CodeBuild
#   ./infrastructure/deploy.sh --with-workbench          # engineer EC2 via SSM
#   ./infrastructure/deploy.sh --with-app --tag v3
#   ./infrastructure/deploy.sh --dry-run
#
set -euo pipefail

PROFILE="${AWS_PROFILE:-intelligence-dev}"
REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="dev"
DRY_RUN=0
WITH_APP=0
WITH_WORKBENCH=0
TAG=""
BUILD_MODE="auto"   # auto | local | remote
LOG_RETENTION_DAYS="${LOG_RETENTION_DAYS:-30}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)       ENVIRONMENT="$2"; shift 2 ;;
    --profile)   PROFILE="$2";     shift 2 ;;
    --region)    REGION="$2";      shift 2 ;;
    --tag)       TAG="$2";         shift 2 ;;
    --with-app)  WITH_APP=1;       shift ;;
    --with-workbench) WITH_WORKBENCH=1; shift ;;
    --build)     BUILD_MODE="$2";  shift 2 ;;
    --dry-run)   DRY_RUN=1;        shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

case "$BUILD_MODE" in
  auto|local|remote) ;;
  *) echo "--build must be auto, local or remote" >&2; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATES="$HERE/cloudformation"
PREFIX="intelligence-engine-${ENVIRONMENT}"
TAGS="Application=intelligence-engine Environment=${ENVIRONMENT} ManagedBy=cloudformation"

BASE_STACKS=(storage state observability)

echo "Intelligence Engine — infrastructure deploy"
echo "  profile      ${PROFILE}"
echo "  region       ${REGION}"
echo "  environment  ${ENVIRONMENT}"
echo "  hosted app   $([[ $WITH_APP -eq 1 ]] && echo yes || echo 'no (--with-app to include)')"
echo "  workbench    $([[ $WITH_WORKBENCH -eq 1 ]] && echo yes || echo 'no (--with-workbench to include)')"
echo

if [[ $DRY_RUN -eq 0 ]]; then
  echo "Verifying credentials..."
  aws sts get-caller-identity --profile "$PROFILE" --query Arn --output text
  echo
fi

# deploy_stack <stack-suffix> <template> [extra parameter-overrides...]
deploy_stack() {
  local suffix="$1"; shift
  local template="$1"; shift
  local stack="${PREFIX}-${suffix}"

  echo "── ${stack}"

  local cmd=(aws cloudformation deploy
    --profile "$PROFILE"
    --region "$REGION"
    --template-file "${TEMPLATES}/${template}"
    --stack-name "$stack"
    --parameter-overrides "Environment=${ENVIRONMENT}" "$@"
    --capabilities CAPABILITY_NAMED_IAM
    --no-fail-on-empty-changeset
    --tags $TAGS)

  if [[ $DRY_RUN -eq 1 ]]; then
    printf '   would run: %s\n' "${cmd[*]}"
  else
    "${cmd[@]}"
  fi
  echo
}

stack_output() {
  aws cloudformation describe-stacks \
    --profile "$PROFILE" --region "$REGION" --stack-name "${PREFIX}-$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null
}

# ---------- 1. base ----------
for suffix in "${BASE_STACKS[@]}"; do
  deploy_stack "$suffix" "${suffix}.yaml"
done

if [[ $WITH_APP -eq 1 ]]; then
  # ---------- 2. registry + builder ----------
  deploy_stack ecr ecr.yaml
  deploy_stack build build.yaml

  # ---------- 3. image ----------
  # Local Docker if it is running, CodeBuild otherwise. Both produce the same
  # image; CodeBuild builds from committed state and needs nothing installed.
  if [[ "$BUILD_MODE" == "auto" ]]; then
    if docker info >/dev/null 2>&1; then BUILD_MODE=local; else BUILD_MODE=remote; fi
  fi

  echo "── console image (${BUILD_MODE} build)"
  builder="${HERE}/build_and_push.sh"
  [[ "$BUILD_MODE" == "remote" ]] && builder="${HERE}/remote_build.sh"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "   would run: ${builder} --env ${ENVIRONMENT} --profile ${PROFILE}"
    IMAGE_URI="<image-uri>"
  else
    build_args=(--env "$ENVIRONMENT" --profile "$PROFILE" --region "$REGION")
    [[ -n "$TAG" ]] && build_args+=(--tag "$TAG")
    IMAGE_URI="$("$builder" "${build_args[@]}" | tail -n 1)"
    echo "   ${IMAGE_URI}"
  fi
  echo

  # ---------- 4. service ----------
  # First pass creates the service; Cognito still points at the placeholder
  # callback, so login will not work yet.
  deploy_stack app app.yaml "ImageUri=${IMAGE_URI}"

  # ---------- 5. callback pass ----------
  if [[ $DRY_RUN -eq 0 ]]; then
    CALLBACK="$(stack_output app CallbackUrlToSet)"
    LOGOUT="$(stack_output app LogoutUrlToSet)"
    if [[ -n "$CALLBACK" && "$CALLBACK" != "None" ]]; then
      echo "── binding Cognito to the service URL"
      deploy_stack app app.yaml \
        "ImageUri=${IMAGE_URI}" \
        "CallbackURL=${CALLBACK}" \
        "LogoutURL=${LOGOUT}"
    fi
  else
    echo "── would re-deploy app with the real CallbackURL"
    echo
  fi
fi

if [[ $WITH_WORKBENCH -eq 1 ]]; then
  # The engineer workbench. Placed in the default VPC's first public subnet so
  # no networking has to be created and no NAT gateway is involved.
  echo "── engineer workbench"
  VPC_ID="$(aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" \
              --filters Name=isDefault,Values=true \
              --query 'Vpcs[0].VpcId' --output text 2>/dev/null)"
  if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
    echo "   no default VPC found; deploy workbench.yaml with explicit" >&2
    echo "   VpcId and SubnetId parameters instead" >&2
  else
    SUBNET_ID="$(aws ec2 describe-subnets --profile "$PROFILE" --region "$REGION" \
                   --filters "Name=vpc-id,Values=${VPC_ID}" \
                             Name=map-public-ip-on-launch,Values=true \
                   --query 'Subnets[0].SubnetId' --output text 2>/dev/null)"
    echo "   vpc ${VPC_ID}  subnet ${SUBNET_ID}"
    deploy_stack workbench workbench.yaml "VpcId=${VPC_ID}" "SubnetId=${SUBNET_ID}"
  fi
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "Dry run complete — nothing was deployed."
  exit 0
fi

# ---------- log retention ----------
# App Runner and CodeBuild create their own log groups, which default to
# "never expire". CloudFormation cannot set retention on a group it does not
# own, so it is applied here — still reproducible, still in version control.
echo "── log retention (${LOG_RETENTION_DAYS}d)"
for lg in $(aws logs describe-log-groups \
              --profile "$PROFILE" --region "$REGION" \
              --query "logGroups[?contains(logGroupName,'${PREFIX}')].logGroupName" \
              --output text 2>/dev/null); do
  current=$(aws logs describe-log-groups --profile "$PROFILE" --region "$REGION" \
              --log-group-name-pattern "$lg" \
              --query "logGroups[0].retentionInDays" --output text 2>/dev/null)
  if [[ "$current" != "$LOG_RETENTION_DAYS" ]]; then
    aws logs put-retention-policy --profile "$PROFILE" --region "$REGION" \
      --log-group-name "$lg" --retention-in-days "$LOG_RETENTION_DAYS" 2>/dev/null \
      && echo "   set ${lg}"
  fi
done
echo

echo "Stack outputs"
echo "─────────────"
ALL=("${BASE_STACKS[@]}")
[[ $WITH_APP -eq 1 ]] && ALL+=(ecr build app)
[[ $WITH_WORKBENCH -eq 1 ]] && ALL+=(workbench)
for suffix in "${ALL[@]}"; do
  aws cloudformation describe-stacks \
    --profile "$PROFILE" --region "$REGION" --stack-name "${PREFIX}-${suffix}" \
    --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" \
    --output text 2>/dev/null | sed 's/^/  /' || true
done

echo
if [[ $WITH_APP -eq 1 ]]; then
  POOL_ID="$(stack_output app UserPoolId)"
  echo "Create your login (no self-signup is allowed):"
  echo "  aws cognito-idp admin-create-user --profile ${PROFILE} \\"
  echo "    --user-pool-id ${POOL_ID} \\"
  echo "    --username you@example.com \\"
  echo "    --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true"
  echo
  echo "Then open:  $(stack_output app ServiceUrl)"
else
  echo "Next: generate the workforce datasets"
  echo "  python scripts/data_generation/generate_all.py --profile ${PROFILE}"
  echo
  echo "Or host the console:  ./infrastructure/deploy.sh --with-app"
fi
