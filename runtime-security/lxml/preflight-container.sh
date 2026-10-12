#!/usr/bin/env bash
# Runs in the separately authenticated immutable verifier image, before APT
# sees any candidate index or DEB. No candidate package is installed here.
set -euo pipefail
[[ $# = 1 && "$1" =~ ^[0-9a-f]{64}$ ]] || exit 2
expected_manifest=$1
python3 -I /inputs/recipe/input-gate.py inventory /inputs "$expected_manifest" \
  > /out/preinstall-inventory.json
(cd /inputs/python && sha256sum -c /inputs/recipe/inputs.sha256) \
  > /out/preinstall-python-inputs.log
python3 -I /inputs/recipe/verify-consumer-wheels.py /inputs/consumer-wheels \
  > /out/preinstall-consumer-wheels.json
python3 -I /inputs/recipe/verify-frozen.py /inputs \
  > /out/preinstall-authenticated-packages.json
printf '%s\n' "$expected_manifest" > /out/preinstall-signed-inputs.txt
