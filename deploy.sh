#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-fin-agent}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
[ -f .mcp_token ] || (openssl rand -hex 24 > .mcp_token && chmod 600 .mcp_token)
./build.sh
.venv/bin/sam deploy --template-file template.yaml --stack-name "$STACK" --region "$REGION" \
  --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset \
  --parameter-overrides "McpAuthToken=$(cat .mcp_token)" "ModelId=$MODEL_ID"
.venv/bin/aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table
