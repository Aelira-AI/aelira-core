#!/usr/bin/env bash
# Clean source/package build; caller disables network and mounts inputs read-only.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1791849600
test "$(dpkg --print-architecture)" = "${AELIRA_CJSON_HOST_ARCH:?}"
python3 -B /recipe/verify-ubuntu-source.py --lists /provenance/apt-lists \
  --input /source-input > /work/ubuntu-source.json
python3 -B /recipe/test-source.py --input /source-input --scratch /work/source-controls \
  > /work/source-controls.json
source=/work/source-controls/positive/cjson-1.7.19-aelira
for level in 0 2; do
  gcc -std=c99 -Wall -Wextra -Wswitch-default -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer -g -O"$level" \
    -I "$source" "$source/cJSON.c" "$source/cJSON_Utils.c" \
    /recipe/test-native.c -lm -pthread -o "/work/native-O$level"
  ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
    "/work/native-O$level" > "/work/native-O$level.log" 2>&1
  grep -Fx 'cJSON native controls passed' "/work/native-O$level.log"
done
(cd "$source" && dpkg-buildpackage -b -us -uc -j2) > /work/package-build-private.log 2>&1
grep -Fx '100% tests passed, 0 tests failed out of 22' /work/package-build-private.log
# dpkg-source --after-build restores the original C files. Use their exact
# identity to make a separate public-symbol/SONAME compatibility baseline.
python3 -B - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('cjson_source', '/recipe/build-source.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
helper.verify_tree(Path('/work/source-controls/positive/cjson-1.7.19-aelira'), helper.ORIGINAL)
PY
gcc -fPIC -shared -fvisibility=hidden -DCJSON_API_VISIBILITY \
  -I "$source" "$source/cJSON.c" -lm \
  -Wl,-soname,libcjson.so.1 -o /work/baseline-core.so
gcc -fPIC -shared -fvisibility=hidden -DCJSON_API_VISIBILITY \
  -I "$source" "$source/cJSON_Utils.c" /work/baseline-core.so -lm \
  -Wl,-soname,libcjson_utils.so.1 -o /work/baseline-utils.so
mkdir /work/artifacts
cp /work/source-controls/positive/*.deb /work/source-controls/positive/*.dsc \
  /work/source-controls/positive/*.debian.tar.xz \
  /work/source-controls/positive/source-receipt.json /work/artifacts/
(cd /work/artifacts && sha256sum *.deb *.dsc *.debian.tar.xz) > /work/artifact-sha256.txt
printf 'native_cjson_build=pass architecture=%s version=1.7.19-2+aelira3\n' "$AELIRA_CJSON_HOST_ARCH"
