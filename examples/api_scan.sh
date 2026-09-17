#!/bin/sh
# Scan a PDF through the running API and fetch the results.
#
# Prerequisites:
#   - A running Aelira Core instance (quickstart from the repository root:
#     docker compose -f docker-compose.quickstart.yml up -d)
#   - curl and Python 3
#   - An API key: dashboard → Settings → API Keys → Create
#     (on a fresh install, the first magic-link login bootstraps the admin)
#
# Usage:
#   AELIRA_URL=http://localhost:8000 AELIRA_API_KEY=aelira_live_... \
#       sh examples/api_scan.sh path/to/document.pdf
# Optional limits (positive integer seconds): AELIRA_REQUEST_TIMEOUT=30,
# AELIRA_POLL_TIMEOUT=300. Timing out stops this client, not the server scan.

set -eu

URL="${AELIRA_URL:-http://localhost:8000}"
KEY="${AELIRA_API_KEY:?Set AELIRA_API_KEY (dashboard → Settings → API Keys)}"
FILE="${1:?Usage: api_scan.sh path/to/document.pdf}"
REQUEST_TIMEOUT="${AELIRA_REQUEST_TIMEOUT-30}"
POLL_TIMEOUT="${AELIRA_POLL_TIMEOUT-300}"
python3 -c '
import sys
for name, value in zip(("AELIRA_REQUEST_TIMEOUT", "AELIRA_POLL_TIMEOUT"), sys.argv[1:]):
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        sys.exit(f"{name} must be a positive integer number of seconds")
' "$REQUEST_TIMEOUT" "$POLL_TIMEOUT"

request() {
  curl --fail --silent --show-error --connect-timeout 10 \
    --max-time "$REQUEST_LIMIT" -H "Authorization: Bearer $KEY" "$@"
}

# Parse separately from curl: POSIX sh pipelines otherwise hide curl failures.
read_json() {
  python3 -c '
import json, sys
from urllib.parse import quote
try:
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    field = sys.argv[1]
    if field == "results":
        print(json.dumps(data, indent=2))
    else:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing or invalid {field}")
        print(quote(value, safe="") if field == "scan_id" else value.upper())
except (ValueError, TypeError) as error:
    sys.exit(f"Invalid API response: {error}")
' "$1"
}

# A monotonic deadline includes time spent in requests and between polls.
# Cap each request and sleep to the remaining budget, including the results fetch.
budget() {
  python3 -c '
import sys, time
remaining = float(sys.argv[1]) - time.monotonic()
if remaining <= 0:
    sys.exit("Polling timed out; the server scan may still be running.")
print(min(float(sys.argv[2]), remaining))
' "$DEADLINE" "$1"
}

printf 'Submitting %s ...\n' "$FILE"
REQUEST_LIMIT="$REQUEST_TIMEOUT"
RESPONSE=$(request -X POST "$URL/education/pdf/scan" -F "file=@$FILE")
SCAN_ID=$(printf '%s' "$RESPONSE" | read_json scan_id)
printf 'scan_id: %s\n' "$SCAN_ID"
DEADLINE=$(python3 -c 'import sys,time; print(time.monotonic() + int(sys.argv[1]))' "$POLL_TIMEOUT")

# The API returns PENDING, PROCESSING, COMPLETED, or FAILED.
while :; do
  REQUEST_LIMIT=$(budget "$REQUEST_TIMEOUT")
  RESPONSE=$(request "$URL/education/scans/$SCAN_ID/progress")
  STATUS=$(printf '%s' "$RESPONSE" | read_json status)
  budget "$REQUEST_TIMEOUT" >/dev/null
  printf '  status: %s\n' "$STATUS"
  case "$STATUS" in
    COMPLETED) break ;;
    FAILED) echo 'Scan failed.' >&2; exit 1 ;;
    PENDING|PROCESSING) ;;
    *) printf 'Unknown scan status: %s\n' "$STATUS" >&2; exit 1 ;;
  esac
  WAIT=$(budget 2)
  sleep "$WAIT"
done

REQUEST_LIMIT=$(budget "$REQUEST_TIMEOUT")
RESPONSE=$(request "$URL/education/scans/$SCAN_ID")
echo 'Results:'
printf '%s' "$RESPONSE" | read_json results
