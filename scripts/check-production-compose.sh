#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

for command_name in docker jq; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is unavailable: $command_name" >&2
    exit 69
  fi
done

required_section=$(sed -n \
  '/^# REQUIRED FOR PRODUCTION COMPOSE$/,/^# Deployment identity$/p' \
  "$repo_root/.env.example")

if [ -z "$required_section" ]; then
  echo ".env.example has no REQUIRED FOR PRODUCTION COMPOSE section" >&2
  exit 1
fi

for variable_name in \
  POSTGRES_USER \
  POSTGRES_PASSWORD \
  POSTGRES_DB \
  JWT_SECRET \
  SESSION_REPLAY_ENCRYPTION_KEY; do
  if ! grep -q "^${variable_name}=" <<<"$required_section"; then
    echo "Production Compose requirement is outside its named section: $variable_name" >&2
    exit 1
  fi
done

if grep -Eq '^(DATABASE_URL|REDIS_URL|ENV)=' "$repo_root/.env.example"; then
  echo ".env.example activates a host-development value that overrides Compose" >&2
  exit 1
fi

workdir=$(mktemp -d "${TMPDIR:-/tmp}/aelira-compose-contract.XXXXXX")
trap 'rm -rf -- "$workdir"' EXIT

cp "$repo_root/.env.example" "$workdir/.env"
cp "$repo_root/docker-compose.prod.yml" "$workdir/docker-compose.prod.yml"

fernet_key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
database_password=compose-contract-password
default_database_url="postgresql://aelira:${database_password}@postgres:5432/aelira"
override_database_url=postgresql://external:contract@database.example.invalid:5432/aelira

(
  cd "$workdir"
  POSTGRES_PASSWORD="$database_password" \
    SESSION_REPLAY_ENCRYPTION_KEY="$fernet_key" \
    docker compose -f docker-compose.prod.yml config --format json \
    >production.json

  DATABASE_URL="$override_database_url" \
    ENV=test \
    POSTGRES_PASSWORD="$database_password" \
    SESSION_REPLAY_ENCRYPTION_KEY="$fernet_key" \
    docker compose -f docker-compose.prod.yml config --format json \
    >overrides.json
)

assert_json_value() {
  local file=$1
  local query=$2
  local expected=$3
  local actual

  if ! actual=$(jq -er "$query" "$file"); then
    echo "Compose output has no value at $query" >&2
    exit 1
  fi
  if [ "$actual" != "$expected" ]; then
    echo "Compose value at $query was '$actual'; expected '$expected'" >&2
    exit 1
  fi
}

for service_name in api worker; do
  assert_json_value \
    "$workdir/production.json" \
    ".services.${service_name}.environment.DATABASE_URL" \
    "$default_database_url"
  assert_json_value \
    "$workdir/production.json" \
    ".services.${service_name}.environment.REDIS_URL" \
    redis://redis:6379/0
  assert_json_value \
    "$workdir/production.json" \
    ".services.${service_name}.environment.ENV" \
    production
  assert_json_value \
    "$workdir/overrides.json" \
    ".services.${service_name}.environment.DATABASE_URL" \
    "$override_database_url"
  assert_json_value \
    "$workdir/overrides.json" \
    ".services.${service_name}.environment.ENV" \
    test
done

docker compose \
  --env-file /dev/null \
  -f "$repo_root/docker-compose.quickstart.yml" \
  config --format json >"$workdir/quickstart.json"

for service_name in api worker; do
  assert_json_value \
    "$workdir/quickstart.json" \
    ".services.${service_name}.environment.DATABASE_URL" \
    postgresql://aelira:aelira@postgres:5432/aelira
  assert_json_value \
    "$workdir/quickstart.json" \
    ".services.${service_name}.environment.REDIS_URL" \
    redis://redis:6379/0
done

echo "Production and quickstart Compose environment contracts verified"
