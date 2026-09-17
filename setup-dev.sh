#!/bin/bash
# Start the local development stack without modifying user configuration.
set -Eeuo pipefail

usage() {
    cat <<'HELP'
Usage: setup-dev.sh [--compose-override FILE]... [--no-build] [--wait-timeout SECONDS]

Requires Docker with the Compose plugin and jq. Defaults to building current
sources and waiting up to 180 seconds per startup phase for container health.
Override files are resolved from the caller's directory and applied in order.
--no-build uses explicitly prepared development images.

AI is disabled by default. To opt in (without changing .env):
  LLM_PROVIDER=ollama ./setup-dev.sh
  LLM_PROVIDER=ollama EMBEDDING_PROVIDER=ollama ./setup-dev.sh
Existing .env/provider choices and COMPOSE_PROJECT_NAME are respected.
HELP
}

fail() { printf 'Setup failed: %s\n' "$1" >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
compose=(docker compose --project-directory "$repo_dir" -f "$repo_dir/docker-compose.dev.yml")
build=true
wait_timeout=180
while (($#)); do
    case "$1" in
        --compose-override)
            (($# >= 2)) || fail '--compose-override requires a file.'
            [[ -f "$2" ]] || fail 'Compose override file does not exist.'
            override=$(cd -- "$(dirname -- "$2")" && pwd)/$(basename -- "$2")
            compose+=(-f "$override")
            shift 2 ;;
        --no-build) build=false; shift ;;
        --wait-timeout)
            (($# >= 2)) || fail '--wait-timeout requires positive integer seconds.'
            [[ "$2" =~ ^[1-9][0-9]*$ ]] || fail '--wait-timeout requires positive integer seconds.'
            wait_timeout=$2
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail 'Unknown argument; use --help.' ;;
    esac
done

command -v docker >/dev/null 2>&1 || fail 'Install Docker and the Docker Compose plugin (docker compose).'
command -v jq >/dev/null 2>&1 || fail 'Install jq (for example: brew install jq or apt-get install jq).'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose plugin required; legacy docker-compose is unsupported.'
docker info >/dev/null 2>&1 || fail 'Docker daemon is unavailable; start Docker and retry.'
# Compose loads the repository .env, never a caller-directory .env or shell code.
cd -- "$repo_dir"
stage='resolving Compose configuration'
trap 'printf "Setup failed while %s; no completion was reported.\n" "$stage" >&2' ERR
# Keep resolved credentials in memory; never print or write the configuration.
config=$("${compose[@]}" --profile ollama config --format json 2>/dev/null) || fail 'Cannot resolve Compose configuration; check your .env and override files.'

# A copied production template contains a placeholder, not a usable Fernet key.
# Empty is allowed in development. Existing keys are never generated or rotated.
if ! jq -e '
    [.services.api.environment, .services.worker.environment] |
    all(.[]; (.TOKEN_ENCRYPTION_KEY // "") |
        . == "" or test("\\A[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]=\\z"))
' <<< "$config" >/dev/null; then
    fail 'TOKEN_ENCRYPTION_KEY must be empty for basic local development or a valid Fernet key. Replace the .env.example placeholder in your own .env; preserve any existing encryption key.'
fi

# Match provider normalization and canonical/legacy model resolution in the app.
# Read both services so an override cannot leave the worker missing its models.
models=$(jq -er '
    def normalized: (. // "none" | ascii_downcase | gsub("^\\s+|\\s+$"; ""));
    def model($key; $legacy; $default):
        (.[$key] // "" | gsub("^\\s+|\\s+$"; "")) as $value |
        if $value != "" then $value else
            (.[$legacy] // "" | gsub("^\\s+|\\s+$"; "")) as $old |
            if $old != "" then $old else $default end
        end;
    [(.services.api.environment, .services.worker.environment) |
        (if ((.LLM_PROVIDER | normalized) == "ollama" or
             (.LLM_FALLBACK_PROVIDER | normalized) == "ollama") then
            model("OLLAMA_TEXT_MODEL"; "OLLAMA_FALLBACK_TEXT"; "gemma3:4b"),
            model("OLLAMA_CODE_MODEL"; "OLLAMA_FALLBACK_CODE"; "qwen2.5-coder:7b"),
            model("OLLAMA_VISION_MODEL"; "OLLAMA_FALLBACK_VISION"; "qwen2.5vl:3b")
         else empty end),
        (if (.EMBEDDING_PROVIDER | normalized) == "ollama" then
            .OLLAMA_EMBEDDING_MODEL // "nomic-embed-text:latest"
         else empty end)
    ] | unique |
    if all(.[]; type == "string" and test("\\A[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\\z"))
    then join("\n") else error("Invalid Ollama model identifier") end
' <<< "$config" 2>/dev/null) || fail 'Invalid Ollama model identifier in resolved Compose configuration.'
if [[ -n "$models" ]] && ! jq -e '
    def normalized: (. // "none" | ascii_downcase | gsub("^\\s+|\\s+$"; ""));
    [.services.api.environment, .services.worker.environment] |
    all(.[];
        if ((.LLM_PROVIDER | normalized) == "ollama" or
            (.LLM_FALLBACK_PROVIDER | normalized) == "ollama" or
            (.EMBEDDING_PROVIDER | normalized) == "ollama") then
            (.OLLAMA_HOST == "http://ollama:11434" or .OLLAMA_HOST == "http://ollama:11434/")
        else true end)
' <<< "$config" >/dev/null; then
    fail 'This setup manages the bundled Ollama service only. Use OLLAMA_HOST=http://ollama:11434 in Compose, or provision external Ollama models and start Compose manually.'
fi
unset config

dependencies=(postgres redis)
if [[ -n "$models" ]]; then
    compose+=(--profile ollama)
    dependencies+=(ollama)
fi

if $build; then
    stage='building development images'
    printf 'Building development images from current sources...\n'
    "${compose[@]}" build api worker
fi
stage='waiting for dependencies'
printf 'Starting dependencies and waiting for health...\n'
"${compose[@]}" up -d --no-deps --wait --wait-timeout "$wait_timeout" "${dependencies[@]}"
if [[ -n "$models" ]]; then
    stage='pulling selected Ollama models'
    printf 'Pulling the selected Ollama models...\n'
    while IFS= read -r model; do
        "${compose[@]}" exec -T ollama ollama pull "$model"
    done <<< "$models"
fi

stage='stopping existing API and worker before migrations'
"${compose[@]}" stop api worker
stage='applying database migrations'
printf 'Applying database migrations...\n'
"${compose[@]}" run --rm --no-deps -T api alembic upgrade head
stage='waiting for API and worker readiness'
printf 'Starting API and worker and waiting for readiness...\n'
"${compose[@]}" up -d --no-build --wait --wait-timeout "$wait_timeout" api worker

printf '\nSetup complete! Database migrated; dependencies, API and worker are healthy.\n'
printf 'Default API docs: http://localhost:8000/docs (override ports may differ).\n'
printf 'The dashboard runs separately. See the onboarding guide for authenticated AI smoke tests.\n'
printf 'See docs/development/onboarding.md for configuration, tests and shutdown commands.\n'
