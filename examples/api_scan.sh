#!/bin/sh
# Scan a PDF through the running API and fetch the results.
#
# Prerequisites:
#   - A running Aelira Core instance (quickstart: docker compose up)
#   - An API key: dashboard → Settings → API Keys → Create
#     (on a fresh install, the first magic-link login bootstraps the admin)
#
# Usage:
#   AELIRA_URL=http://localhost:8000 AELIRA_API_KEY=aelira_live_... \
#       sh examples/api_scan.sh path/to/document.pdf

set -eu

URL="${AELIRA_URL:-http://localhost:8000}"
KEY="${AELIRA_API_KEY:?Set AELIRA_API_KEY (dashboard → Settings → API Keys)}"
FILE="${1:?Usage: api_scan.sh path/to/document.pdf}"

echo "Submitting $FILE ..."
SCAN_ID=$(curl -sf -X POST "$URL/education/pdf/scan" \
  -H "Authorization: Bearer $KEY" \
  -F "file=@$FILE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["scan_id"])')
echo "scan_id: $SCAN_ID"

# The scan runs asynchronously; poll progress until it completes.
while :; do
  STATUS=$(curl -sf "$URL/education/scans/$SCAN_ID/progress" \
    -H "Authorization: Bearer $KEY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')
  echo "  status: $STATUS"
  case "$STATUS" in
    completed|failed) break ;;
  esac
  sleep 2
done

echo "Results:"
curl -sf "$URL/education/scans/$SCAN_ID" -H "Authorization: Bearer $KEY" | python3 -m json.tool
