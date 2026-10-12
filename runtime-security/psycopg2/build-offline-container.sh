#!/usr/bin/env bash
set -euo pipefail
cd /inputs
sha256sum -c frozen.sha256 > /out/frozen-hash-check.log
cp -a /inputs/apt-lists/. /var/lib/apt/lists/
cp /inputs/debs/*.deb /var/cache/apt/archives/
apt-get install -y --no-download --no-install-recommends \
  build-essential python3-dev python3-venv libpq-dev ca-certificates curl \
  > /out/apt-install.log
python3 /inputs/recipe/verify-frozen.py /inputs > /out/authenticated-packages.json
python3 - <<'PY'
import math
import os
import tarfile

with tarfile.open('/inputs/python/psycopg2_binary-2.9.13.tar.gz') as archive:
    source_epoch = math.ceil(max(member.mtime for member in archive))
assert source_epoch == int(os.environ['SOURCE_DATE_EPOCH'])
PY
python3 -m venv /work/venv
/work/venv/bin/python -m pip install --no-index --no-deps \
  /inputs/python/pip-26.2.1-py3-none-any.whl \
  /inputs/python/setuptools-84.0.0-py3-none-any.whl > /out/python-install.log
mkdir -p /work/source /work/wheels
tar -xf /inputs/python/psycopg2_binary-2.9.13.tar.gz -C /work/source
/work/venv/bin/python -m pip wheel --no-index --no-deps --no-build-isolation \
  --no-cache-dir --wheel-dir /work/wheels \
  /work/source/psycopg2-binary-2.9.13 > /out/wheel-build.log
cp /work/wheels/*.whl /out/
sha256sum /out/*.whl > /out/wheel.sha256
/work/venv/bin/python -m pip list --format=json > /out/python-build-tools.json
dpkg-query -W > /out/installed-build-packages.txt
tar -C /work -cf /out/installer-venv.tar venv
