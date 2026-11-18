#!/usr/bin/env bash
# Proves the role-preparation retry wrap in build.sh with a PATH-shadowed docker
# stub (same shim pattern as scripts/smoke_psycopg2_tls.test.ts). Scenarios:
#   1. first attempt succeeds on both roles and passes through untouched;
#   2. source attempt 1 killed with 137 then attempt 2 succeeds -> overall pass,
#      per-attempt receipts show the success came from attempt 2;
#   3. source attempts 1 and 2 both die with 137 -> failure with both receipts
#      and a build-status.json naming the attempt that died;
#   4. a genuine build error (exit 1) fails fast with a single receipt;
#   5. a docker daemon error (exit 125) is retried and a success follows.
set -u
here=$(cd "$(dirname "$0")" && pwd)
build="$here/build.sh"
test -f "$build"
root=$(mktemp -d "${TMPDIR:-/tmp}/aelira-xdg-retry-XXXXXX")
trap 'rm -rf "$root"' EXIT
shim="$root/shim"
mkdir -p "$shim"
cat > "$shim/docker" <<'SHIM'
#!/usr/bin/env bash
set -u
sub="${1:-}"
shift 2>/dev/null || true
case "$sub" in
  info) echo aarch64; exit 0 ;;
  pull) exit 0 ;;
  image) echo arm64; exit 0 ;;
  commit) printf 'sha256:%064d\n' 0; exit 0 ;;
  rm) exit 0 ;;
  save)
    out=""
    while test $# -gt 0; do
      if test "$1" = -o; then out="${2:-}"; fi
      shift
    done
    if test -n "$out"; then : > "$out"; fi
    exit 0 ;;
  run) ;;
  *) exit 0 ;;
esac
joined=" $*"
prep_role=""
case "$joined" in
  *prepare-native.sh*) prep_role=source ;;
  *prepare-client.sh*) prep_role=client ;;
esac
if test -n "$prep_role"; then
  if test "$prep_role" = source; then list="${TEST_SHIM_SOURCE_EXITS:-0}"
  else list="${TEST_SHIM_CLIENT_EXITS:-0}"; fi
  counter="$TEST_SHIM_STATE/$prep_role.count"
  n=0
  test -f "$counter" && n=$(cat "$counter")
  printf '%s\n' "$((n + 1))" > "$counter"
  IFS=',' read -ra codes <<< "$list"
  exit "${codes[$n]:-0}"
fi
case "$joined" in
  *verify-client-provenance.py*) echo '{"shim": true}'; exit 0 ;;
  *build-in-container.sh*)
    work=""
    while test $# -gt 0; do
      case "$1" in
        *:/work:rw*) work="${1%%:/work:rw*}" ;;
      esac
      shift
    done
    mkdir -p "$work/packages"
    for pkg in xdg-utils_1.2.1.orig.tar.gz xdg-utils_1.2.1-2ubuntu2+aelira2.dsc \
      xdg-utils_1.2.1-2ubuntu2+aelira2.debian.tar.xz xdg-utils_1.2.1-2ubuntu2+aelira2_all.deb; do
      printf 'shim %s\n' "$pkg" > "$work/packages/$pkg"
    done
    exit 0 ;;
  *run-installed-client.sh*) exit 0 ;;
  *dpkg*) echo '0000000000000000000000000000000000000000000000000000000000000000  /usr/bin/xdg-email'; exit 0 ;;
  */etc/passwd) echo 'root:x:0:0:root:/root:/bin/sh'; exit 0 ;;
  */etc/group) echo 'root:x:0:'; exit 0 ;;
esac
exit 0
SHIM
cat > "$shim/uname" <<'UNAME'
#!/usr/bin/env bash
case "${1:-}" in
  -s) echo Linux ;;
  -m) echo aarch64 ;;
  *) echo shim ;;
esac
UNAME
chmod 700 "$shim/docker" "$shim/uname"
if ! command -v sha256sum > /dev/null 2>&1; then
  printf '#!/usr/bin/env bash\nexec shasum -a 256 "$@"\n' > "$shim/sha256sum"
  chmod 700 "$shim/sha256sum"
fi
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
receipt() { cat "$1" 2> /dev/null || printf 'missing\n'; }
run_case() {
  local name="$1" src="$2" cli="$3"
  mkdir -p "$root/state-$name"
  TEST_SHIM_STATE="$root/state-$name" TEST_SHIM_SOURCE_EXITS="$src" \
    TEST_SHIM_CLIENT_EXITS="$cli" PATH="$shim:$PATH" \
    bash "$build" "$root/out-$name" > "$root/stdout-$name" 2> "$root/stderr-$name"
}
status_json() { cat "$1/build-status.json" 2> /dev/null || printf 'missing\n'; }

# Scenario 1: first-attempt success passes through.
run_case ok 0 0 || fail 'scenario 1 expected exit 0'
out="$root/out-ok"
test "$(status_json "$out")" = '{"schema":"aelira-xdg-build-status-v1","phase":"complete","exit":0}' \
  || fail "scenario 1 status: $(status_json "$out")"
test "$(receipt "$out/source-provenance/prepare-1-exit.txt")" = 0 || fail 'scenario 1 source receipt'
test "$(receipt "$out/client-provenance/prepare-1-exit.txt")" = 0 || fail 'scenario 1 client receipt'
test ! -e "$out/source-provenance/prepare-2-exit.txt" || fail 'scenario 1 ran an attempt 2'
test ! -e "$out/client-provenance/prepare-2-exit.txt" || fail 'scenario 1 client ran an attempt 2'

# Scenario 2: 137 then success yields success and receipts show attempt 2.
run_case oom-recover 137,0 0 || fail 'scenario 2 expected exit 0'
out="$root/out-oom-recover"
test "$(status_json "$out")" = '{"schema":"aelira-xdg-build-status-v1","phase":"complete","exit":0}' \
  || fail "scenario 2 status: $(status_json "$out")"
test "$(receipt "$out/source-provenance/prepare-1-exit.txt")" = 137 || fail 'scenario 2 attempt 1 receipt'
test "$(receipt "$out/source-provenance/prepare-2-exit.txt")" = 0 || fail 'scenario 2 attempt 2 receipt'
test ! -e "$root/state-oom-recover/source.count" -o "$(cat "$root/state-oom-recover/source.count")" = 2 \
  || fail 'scenario 2 made more than two attempts'

# Scenario 3: two 137 deaths fail, both receipts present, status names attempt 2.
run_case oom-exhaust 137,137 0
if test $? != 137; then fail 'scenario 3 expected exit 137'; fi
out="$root/out-oom-exhaust"
test "$(receipt "$out/source-provenance/prepare-1-exit.txt")" = 137 || fail 'scenario 3 attempt 1 receipt'
test "$(receipt "$out/source-provenance/prepare-2-exit.txt")" = 137 || fail 'scenario 3 attempt 2 receipt'
test "$(status_json "$out")" = '{"schema":"aelira-xdg-build-status-v1","phase":"source-preparation-2","exit":137}' \
  || fail "scenario 3 status: $(status_json "$out")"
test ! -e "$out/client-provenance/prepare-1-exit.txt" || fail 'scenario 3 reached client role'

# Scenario 4: a genuine build error fails fast without a second attempt.
run_case real-error 1 0
if test $? != 1; then fail 'scenario 4 expected exit 1'; fi
out="$root/out-real-error"
test "$(receipt "$out/source-provenance/prepare-1-exit.txt")" = 1 || fail 'scenario 4 attempt 1 receipt'
test ! -e "$out/source-provenance/prepare-2-exit.txt" || fail 'scenario 4 retried a genuine error'
test "$(status_json "$out")" = '{"schema":"aelira-xdg-build-status-v1","phase":"source-preparation-1","exit":1}' \
  || fail "scenario 4 status: $(status_json "$out")"

# Scenario 5: a docker daemon error (125) is transient and retried.
run_case daemon-recover 125,0 0 || fail 'scenario 5 expected exit 0'
out="$root/out-daemon-recover"
test "$(receipt "$out/source-provenance/prepare-1-exit.txt")" = 125 || fail 'scenario 5 attempt 1 receipt'
test "$(receipt "$out/source-provenance/prepare-2-exit.txt")" = 0 || fail 'scenario 5 attempt 2 receipt'

printf 'native_xdg_retry_test=pass scenarios=5\n'
