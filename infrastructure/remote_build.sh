#!/usr/bin/env bash
#
# Build the console image in AWS via CodeBuild. No local Docker required.
#
# The source is packaged with `git archive`, so the image is built from
# committed state — the repository stays the source of truth, and an image
# always corresponds to a commit you can check out.
#
# Usage:
#   ./infrastructure/remote_build.sh
#   ./infrastructure/remote_build.sh --tag v2
#
# Prints the full image URI on the last line.
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
PREFIX="intelligence-engine-${ENVIRONMENT}"

if [[ -z "$TAG" ]]; then
  TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo latest)"
fi

if [[ -n "$(git -C "$ROOT" status --porcelain 2>/dev/null)" ]]; then
  echo "Warning: uncommitted changes will NOT be in the image (built from HEAD)." >&2
fi

out() { aws cloudformation describe-stacks --profile "$PROFILE" --region "$REGION" \
  --stack-name "${PREFIX}-$1" \
  --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null; }

BUCKET="$(out storage BucketName)"
PROJECT="$(out build ProjectName)"
REPO_URI="$(out build RepositoryUri)"

for v in BUCKET PROJECT REPO_URI; do
  if [[ -z "${!v}" || "${!v}" == "None" ]]; then
    echo "Missing ${v}. Deploy the storage, ecr and build stacks first." >&2
    exit 1
  fi
done

TMP_ZIP="$(mktemp -t ie-source-XXXXXX).zip"
trap 'rm -f "$TMP_ZIP"' EXIT

echo "Packaging HEAD ($(git -C "$ROOT" rev-parse --short HEAD))" >&2
git -C "$ROOT" archive --format=zip -o "$TMP_ZIP" HEAD

echo "Uploading to s3://${BUCKET}/build/source.zip" >&2
aws s3 cp "$TMP_ZIP" "s3://${BUCKET}/build/source.zip" \
  --profile "$PROFILE" --region "$REGION" --only-show-errors

echo "Starting CodeBuild ${PROJECT} (tag ${TAG})" >&2
BUILD_ID="$(aws codebuild start-build \
  --profile "$PROFILE" --region "$REGION" \
  --project-name "$PROJECT" \
  --environment-variables-override "name=IMAGE_TAG,value=${TAG},type=PLAINTEXT" \
  --query 'build.id' --output text)"

echo "  build ${BUILD_ID}" >&2
while true; do
  STATUS="$(aws codebuild batch-get-builds --ids "$BUILD_ID" \
    --profile "$PROFILE" --region "$REGION" \
    --query 'builds[0].buildStatus' --output text)"
  case "$STATUS" in
    SUCCEEDED) echo "  succeeded" >&2; break ;;
    IN_PROGRESS) sleep 10 ;;
    *)
      echo "  build ${STATUS}. Logs:" >&2
      aws codebuild batch-get-builds --ids "$BUILD_ID" \
        --profile "$PROFILE" --region "$REGION" \
        --query 'builds[0].logs.deepLink' --output text >&2
      exit 1
      ;;
  esac
done

echo "${REPO_URI}:${TAG}"
