#!/usr/bin/env bash
set -euo pipefail
test -s /out/preinstall-authenticated.txt
cd /inputs
sha256sum -c frozen.sha256 > /out/frozen-hash-check.log
(cd /inputs/python && sha256sum -c /inputs/recipe/inputs.sha256) > /out/python-input-check.log
cp -a /inputs/apt-lists/. /var/lib/apt/lists/
cp /inputs/debs/*.deb /var/cache/apt/archives/
apt-get install -y --no-download --no-install-recommends \
  build-essential python3-dev python3-venv libxml2-dev libxslt1-dev \
  libxml2-16 libxslt1.1 libxml2-utils binutils ca-certificates curl > /out/apt-install.log
python3 /inputs/recipe/verify-frozen.py /inputs > /out/authenticated-packages.json
test "$(dpkg-query -W -f='${Version}' libxml2-16)" = '2.15.2+dfsg-0.1ubuntu0.3'
test "$(dpkg-query -W -f='${Version}' libxslt1.1)" = '1.1.45-0.1'
SOURCE_DATE_EPOCH=$(python3 - <<'PY'
import math,tarfile
with tarfile.open('/inputs/python/lxml-6.1.3.tar.gz') as archive:
    epoch=math.ceil(max(member.mtime for member in archive))
assert any(member.name.endswith('/src/lxml/etree.c') for member in archive)
print(epoch)
PY
)
export SOURCE_DATE_EPOCH PYTHONHASHSEED=0
printf '%s\n' "$SOURCE_DATE_EPOCH" > /out/source-date-epoch.txt
python3 -m venv /work/venv
/work/venv/bin/python -m pip install --no-index --no-deps \
  /inputs/python/pip-26.2.1-py3-none-any.whl \
  /inputs/python/setuptools-84.2.1-py3-none-any.whl > /out/python-install.log
mkdir -p /work/source /work/wheels
tar -xf /inputs/python/lxml-6.1.3.tar.gz -C /work/source
test ! -e /work/venv/lib/python3.14/site-packages/Cython
STATIC_DEPS=false /work/venv/bin/python -m pip wheel --no-index --no-deps --no-build-isolation \
  --no-cache-dir --wheel-dir /work/wheels /work/source/lxml-6.1.3 > /out/wheel-build.log
cp /work/wheels/*.whl /out/
sha256sum /out/*.whl > /out/wheel.sha256
/work/venv/bin/python -m pip list --format=json > /out/python-build-tools.json
dpkg-query -W > /out/installed-build-packages.txt
tar -C /work -cf /out/installer-venv.tar venv
