#!/usr/bin/env bash
set -euo pipefail

owner=${1:?candidate owner is required}
if [[ ! "$owner" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo 'Invalid candidate owner' >&2
  exit 2
fi

failed=0
for kind in container network; do
  if [ "$kind" = container ]; then
    list=(docker container ls --all --quiet --filter "label=aelira.candidate.owner=$owner")
    inspect=(docker container inspect --format '{{index .Config.Labels "aelira.candidate.owner"}}')
    remove=(docker container rm --force --volumes)
  else
    list=(docker network ls --quiet --filter "label=aelira.candidate.owner=$owner")
    inspect=(docker network inspect --format '{{index .Labels "aelira.candidate.owner"}}')
    remove=(docker network rm)
  fi

  if resources=$("${list[@]}"); then
    while IFS= read -r resource; do
      [ -n "$resource" ] || continue
      if label=$("${inspect[@]}" "$resource"); then
        if [ "$label" = "$owner" ]; then
          if ! "${remove[@]}" "$resource"; then
            echo "Failed to remove candidate $kind $resource" >&2
            failed=1
          fi
        else
          echo "Candidate $kind $resource has an unexpected owner" >&2
          failed=1
        fi
      else
        echo "Failed to inspect candidate $kind $resource" >&2
        failed=1
      fi
    done <<< "$resources"
  else
    echo "Failed to list candidate ${kind}s" >&2
    failed=1
  fi

  if remaining=$("${list[@]}"); then
    if [ -n "$remaining" ]; then
      echo "Candidate ${kind}s remain after cleanup: $remaining" >&2
      failed=1
    fi
  else
    echo "Failed to verify candidate ${kind}s were removed" >&2
    failed=1
  fi
done
exit "$failed"
