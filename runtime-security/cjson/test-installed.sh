#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C TZ=UTC
arch=$(dpkg --print-architecture)
test "$arch" = "${AELIRA_CJSON_HOST_ARCH:?}"
dpkg -i /packages/*.deb > /tmp/package-install-private.log 2>&1
test "$(dpkg-query -W -f='${Version}' libcjson1)" = '1.7.19-2+aelira3'
gcc -std=c99 -Wall -Wextra -Wswitch-default -Werror -O2 \
  -I /usr/include/cjson /recipe/test-native.c -lcjson_utils -lcjson -lm -pthread \
  -o /tmp/cjson-installed-control
/tmp/cjson-installed-control
python3 -B - <<'PY'
import hashlib, json, re, subprocess
from pathlib import Path
multiarch = subprocess.check_output(['gcc', '-print-multiarch'], text=True).strip()
arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
loaded = subprocess.check_output(['ldd', '/tmp/cjson-installed-control'], text=True)
libraries = {}
def symbols(path):
    output = subprocess.check_output(['nm', '-D', '--defined-only', '--format=posix', str(path)], text=True)
    return sorted((line.split()[0], line.split()[1]) for line in output.splitlines())
for soname, baseline in [('libcjson.so.1', 'baseline-core.so'), ('libcjson_utils.so.1', 'baseline-utils.so')]:
    path = (Path('/usr/lib') / multiarch / soname).resolve(strict=True)
    match = re.search(r'^\s*' + re.escape(soname) + r' => (\S+) ', loaded, re.MULTILINE)
    assert match and Path(match.group(1)).resolve(strict=True) == path
    actual_symbols = symbols(path)
    baseline_path = Path('/baseline') / baseline
    assert actual_symbols == symbols(baseline_path)
    dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
    assert re.search(r'\(SONAME\).*\[' + re.escape(soname) + r'\]', dynamic)
    libraries[soname] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'baseline_sha256': hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                         'public_symbols': actual_symbols, 'loader_path': str(path)}
print('installed_cjson_receipt=' + json.dumps({'schema': 'aelira-cjson-installed-v1',
      'status': 'pass', 'architecture': arch, 'version': '1.7.19-2+aelira3', 'libraries': libraries}, sort_keys=True))
PY
printf 'installed_cjson_acceptance=pass architecture=%s version=1.7.19-2+aelira3\n' "$arch"
