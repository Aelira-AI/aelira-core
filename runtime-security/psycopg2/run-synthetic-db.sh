#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 ]]; then
  echo "usage: $0 PREPARED_ABSOLUTE_SCRATCH NATIVE_RUNTIME_IMAGE" >&2
  exit 2
fi
scratch=$1
runtime_image=$2
[[ "$scratch" = /* && -f "$scratch/build-a/wheel.sha256" ]] || exit 2
recipe=$(cd "$(dirname "$0")" && pwd -P)
host_uid=$(id -u)
host_gid=$(id -g)
[[ "$host_uid" != 0 ]] || { echo "native host root is refused" >&2; exit 2; }
umask 077
frozen="$scratch/frozen"
[[ -f "$frozen/frozen.sha256" && -d "$frozen/recipe" ]] || { echo "missing prepared frozen recipe" >&2; exit 2; }
(
  cd "$frozen"
  sha256sum -c --quiet frozen.sha256
) || { echo "frozen input hash mismatch" >&2; exit 1; }
for source in "$frozen/recipe"/*; do
  [[ -f "$source" ]] || continue
  name=${source##*/}
  cmp -s "$source" "$recipe/$name" || { echo "live source differs from frozen recipe: $name" >&2; exit 1; }
done
for name in consumer.py verify_installed.py run-synthetic-db.sh; do
  [[ -f "$frozen/recipe/$name" ]] || { echo "missing frozen recipe source: $name" >&2; exit 1; }
done
host_arch=$(uname -m)
case "$host_arch" in x86_64) architecture=amd64;; aarch64) architecture=arm64;; *) exit 2;; esac
[[ "$(docker image inspect "$runtime_image" --format '{{.Architecture}}')" = "$architecture" ]] || { echo "runtime image architecture mismatch" >&2; exit 1; }
wheel=("$scratch"/build-a/*.whl)
[[ ${#wheel[@]} = 1 ]] || exit 1
wheel_name=${wheel[0]##*/}
expected_wheel_hash=$(awk '{print $1}' "$scratch/build-a/wheel.sha256")
[[ "$(sha256sum "${wheel[0]}" | awk '{print $1}')" = "$expected_wheel_hash" ]] || { echo "wheel hash mismatch" >&2; exit 1; }
run_dir="$scratch/synthetic-db"
[[ ! -e "$run_dir" ]] || { echo "synthetic database scratch already exists" >&2; exit 2; }
mkdir -m 700 -p "$run_dir/tls"
{
  printf 'host_uid=%s\nhost_gid=%s\nprobe_uid=%s\nprobe_gid=%s\narchitecture=%s\n' "$host_uid" "$host_gid" "$host_uid" "$host_gid" "$architecture"
  printf 'frozen_manifest_sha256=%s\n' "$(sha256sum "$frozen/frozen.sha256" | awk '{print $1}')"
  printf 'frozen_consumer_sha256=%s\n' "$(sha256sum "$frozen/recipe/consumer.py" | awk '{print $1}')"
  printf 'frozen_verifier_sha256=%s\n' "$(sha256sum "$frozen/recipe/verify_installed.py" | awk '{print $1}')"
  printf 'wheel_sha256=%s\n' "$expected_wheel_hash"
  printf 'runtime_image_id=%s\n' "$(docker image inspect "$runtime_image" --format '{{.Id}}')"
} > "$run_dir/harness-identity.txt"
openssl rand -base64 36 > "$run_dir/pg-password"
chmod 600 "$run_dir/pg-password"
openssl req -x509 -newkey rsa:3072 -nodes -days 2 \
  -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$run_dir/tls/server.key" -out "$run_dir/tls/server.crt" > /dev/null 2>&1
openssl req -x509 -newkey rsa:3072 -nodes -days 2 \
  -subj '/CN=unrelated.invalid' -addext 'subjectAltName=DNS:unrelated.invalid' \
  -keyout "$run_dir/tls/unrelated.key" -out "$run_dir/tls/unrelated.crt" > /dev/null 2>&1
chmod 600 "$run_dir/tls/"*.key
chmod 644 "$run_dir/tls/"*.crt
pg_image='pgvector/pgvector@sha256:33198da2828a14c30348d2ccb4750833d5ed9a44c88d840a0e523d7417120337'
docker pull "$pg_image" > /dev/null
docker image inspect "$pg_image" > /dev/null
[[ "$(docker image inspect "$pg_image" --format '{{.Architecture}}')" = "$architecture" ]] || { echo "PG image architecture mismatch" >&2; exit 1; }
pg_uid=$(docker run --rm --pull=never --network none --entrypoint id "$pg_image" -u postgres)
docker run --rm --pull=never --network none \
  --mount "type=bind,src=$run_dir/tls,dst=/tls" \
  --entrypoint /bin/sh "$pg_image" -c 'chown "$1" /tls/server.key && chmod 600 /tls/server.key' sh "$pg_uid"
run_id=$(date -u +%s)-$$
db=aelira-v0912-psycopg2-db-$run_id
client=aelira-v0912-psycopg2-client-$run_id
data_volume=aelira-v0912-psycopg2-data-$run_id
socket_volume=aelira-v0912-psycopg2-socket-$run_id
printf '%s\n' "$db" "$client" "$data_volume" "$socket_volume" > "$run_dir/resources.txt"
docker volume create "$data_volume" > /dev/null
docker volume create "$socket_volume" > /dev/null
stop_new_containers() {
  status=$?
  docker stop "$client" "$db" > /dev/null 2>&1 || true
  exit "$status"
}
trap stop_new_containers EXIT
docker run -d --pull=never --name "$db" --network none \
  --cpus 2 --memory 3g --pids-limit 256 \
  --mount "type=volume,src=$data_volume,dst=/var/lib/postgresql/data" \
  --mount "type=volume,src=$socket_volume,dst=/var/run/postgresql" \
  --mount "type=bind,src=$run_dir/pg-password,dst=/run/secrets/pg_password,readonly" \
  --mount "type=bind,src=$run_dir/tls/server.crt,dst=/tls/server.crt,readonly" \
  --mount "type=bind,src=$run_dir/tls/server.key,dst=/tls/server.key,readonly" \
  --env POSTGRES_PASSWORD_FILE=/run/secrets/pg_password \
  "$pg_image" postgres -c listen_addresses=127.0.0.1 -c ssl=on \
  -c ssl_cert_file=/tls/server.crt -c ssl_key_file=/tls/server.key \
  -c unix_socket_directories=/var/run/postgresql \
  > "$run_dir/database-container-id.txt"
ready=0
for _ in $(seq 1 40); do
  if docker exec "$db" pg_isready -h 127.0.0.1 -U postgres > /dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" = 1 ]] || { echo "fresh PostgreSQL 16 did not become ready" >&2; docker logs "$db" | tail -30 >&2; exit 1; }
docker run --rm --pull=never --network none \
  --mount "type=bind,src=$scratch/build-a,dst=/out" \
  --entrypoint /bin/tar "$pg_image" -C /out -xf /out/installer-venv.tar
docker run -d --pull=never --name "$client" --network "container:$db" \
  --cpus 2 --memory 3g --pids-limit 256 \
  --mount "type=volume,src=$socket_volume,dst=/var/run/postgresql,readonly" \
  --mount "type=bind,src=$scratch/build-a,dst=/wheels,readonly" \
  --mount "type=bind,src=$scratch/build-a/venv,dst=/work/venv,readonly" \
  --mount "type=bind,src=$run_dir/pg-password,dst=/run/secrets/pg_password,readonly" \
  --mount "type=bind,src=$run_dir/tls/server.crt,dst=/tls/server.crt,readonly" \
  --mount "type=bind,src=$run_dir/tls/unrelated.crt,dst=/tls/unrelated.crt,readonly" \
  --mount "type=bind,src=$frozen/recipe,dst=/probe,readonly" \
  --env HOME=/tmp --user root --entrypoint /bin/sh "$runtime_image" -c 'sleep infinity' \
  > "$run_dir/client-container-id.txt"
docker exec "$client" test -d /opt/venv/lib/python3.14/site-packages/psycopg2_binary.libs
docker exec "$client" /work/venv/bin/python -m pip --python /opt/venv/bin/python uninstall -y psycopg2-binary \
  > "$run_dir/uninstall.log"
docker exec "$client" /work/venv/bin/python -m pip --python /opt/venv/bin/python install \
  --no-index --no-deps "/wheels/$wheel_name" > "$run_dir/install.log"
docker exec --user "$host_uid:$host_gid" "$client" /opt/venv/bin/python /probe/verify_installed.py \
  > "$run_dir/installed.json"
docker exec --user "$host_uid:$host_gid" "$client" /opt/venv/bin/python /probe/consumer.py \
  > "$run_dir/consumers.json"
docker exec "$client" dpkg-query -W libpq5 libssl3t64 \
  > "$run_dir/runtime-packages.txt"
docker inspect "$db" "$client" --format '{{.Id}} {{.Image}} {{.HostConfig.NetworkMode}}' \
  > "$run_dir/container-identities.txt"
echo "fresh-native-$architecture-postgresql16-consumers-passed"
