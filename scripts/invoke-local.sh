#!/usr/bin/env bash
set -euo pipefail
aws lambda invoke \
  --function-name aws-finops-cost-anomaly-detector \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
