#!/usr/bin/env bash
#
# Build the console image and push it to ECR.
#
# Requires Docker running locally. App Runner needs linux/amd64, which is
# forced explicitly so this works from an ARM machine too.
#
# Usage:
#   ./infrastructure/build_and_push.sh                 # tag from git sha
#   ./infrastructure/build_and_push.sh --tag v2
#
# Prints the full image URI on the last line so callers can capture it.
set -euo pipefail

PROFILE="${AWS_PROFILE:-intelligence-dev}"
REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="dev"
TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)     ENVIRONMENT="$2"; shift 2 ;;
    --profile) PROFILE="$2";     shift 2 ;;
    --region)  REGION="$2";      shift 2 ;;
    --tag)     TAG="$2";         shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
STACK="intelligence-engine-${ENVIRONMENT}-ecr"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop and retry." >&2
  exit 1
fi

if [[ -z "$TAG" ]]; then
  TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo latest)"
fi

REPO_URI="$(aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" \
  --output text)"

if [[ -z "$REPO_URI" || "$REPO_URI" == "None" ]]; then
  echo "Could not read RepositoryUri from ${STACK}. Deploy the ecr stack first." >&2
  exit 1
fi

REGISTRY="${REPO_URI%%/*}"

echo "Logging in to ${REGISTRY}" >&2
aws ecr get-login-password --profile "$PROFILE" --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY" >&2

echo "Building ${REPO_URI}:${TAG}" >&2
docker build --platform linux/amd64 -t "${REPO_URI}:${TAG}" "$ROOT" >&2

echo "Pushing" >&2
docker push "${REPO_URI}:${TAG}" >&2

# Last line is the image URI, for capture by deploy.sh.
echo "${REPO_URI}:${TAG}"
