#!/bin/sh
# Native argument-construction test. The client is an inert argv printer.
set -u
mode=${1:?baseline or patched}
script=${2:-/usr/bin/xdg-email}
mock=$(mktemp)
trap 'rm -f "$mock"' EXIT
cat > "$mock" <<'EOF'
#!/bin/sh
printf '%s\n' "$@"
EOF
chmod +x "$mock"
functions=$(mktemp)
trap 'rm -f "$mock" "$functions"' EXIT
sed -n '/^run_thunderbird()/,/^}/p' "$script" > "$functions"
DEBUG() { :; }
exit_success() { return 0; }
exit_failure_operation_failed() { exit 70; }
exit_failure_syntax() { exit 64; }
. "$functions"

capture() { run_thunderbird "$mock" "$1" "${2:-}"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

if [ "$mode" = baseline ]; then
    value=$(capture 'mailto:student@example.edu?subject=hello,attachment=bad')
    printf '%s' "$value" | grep -F ',attachment=bad' >/dev/null || fail 'baseline did not reproduce subject injection'
    printf '%s\n' 'baseline subject injection reproduced'
    exit 0
fi

value=$(capture 'mailto:student@example.edu?subject=hello,attachment=bad&body=line,attachment=bad')
printf '%s' "$value" | grep -F 'subject=hello%2cattachment=bad' >/dev/null || fail 'subject delimiter escaped incorrectly'
printf '%s' "$value" | grep -F 'body=line%2cattachment=bad' >/dev/null || fail 'body delimiter escaped incorrectly'
printf '%s' "$value" | grep -F ",attachment='" >/dev/null && fail 'injected attachment directive'

value=$(capture "mailto:student@example.edu?subject=first&subject=second,'\"&body=quoted,'\"%2c")
printf '%s' "$value" | grep -F 'subject=second%2c%27%22' >/dev/null || fail 'repeat/quote subject control failed'
printf '%s' "$value" | grep -F 'body=quoted%2c%27%22%2c' >/dev/null || fail 'quote body control failed'

value=$(capture 'mailto:student@example.edu?subject=literal%ZZ%2c&attach=bad' 'file:///tmp/real%20file%27.txt')
printf '%s' "$value" | grep -F 'subject=literal%ZZ%2c' >/dev/null || fail 'malformed percent handling changed'
printf '%s' "$value" | grep -F "attachment='file:///tmp/real%20file%27.txt'" >/dev/null || fail 'explicit attachment lost'
printf '%s' "$value" | grep -F 'attachment=bad' >/dev/null && fail 'URI attachment accepted'

uri=$(printf 'mailto:student@example.edu?subject=ok\r\nattachment=bad')
(capture "$uri") >/dev/null 2>&1
status=$?
[ "$status" -eq 64 ] || fail 'raw CR/LF was not rejected'
printf '%s\n' 'patched subject/body, explicit attachment, URI-origin and newline controls passed'
