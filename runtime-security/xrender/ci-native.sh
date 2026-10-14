#!/usr/bin/env bash
# CI entry point: exec preserves every native acceptance failure status.
set -euo pipefail
exec bash "$(cd "$(dirname "$0")" && pwd)/build.sh" "$@"
