#!/usr/bin/env bash
set -euo pipefail
test -s /out/preinstall-authenticated.txt
cd /inputs
sha256sum -c frozen.sha256 > /out/frozen-hash-check.log
(cd /inputs/python && sha256sum -c /inputs/recipe/inputs.sha256) > /out/python-input-check.log
cp -a /inputs/apt-lists/. /var/lib/apt/lists/
cp /inputs/debs/*.deb /var/cache/apt/archives/
apt-get install -y --no-download --no-install-recommends \
  python3-venv libxml2-16 libxslt1.1 libxml2-utils binutils > /out/apt-install.log
python3 /inputs/recipe/verify-consumer-wheels.py /inputs/consumer-wheels > /out/consumer-wheel-check.json
python3 /inputs/recipe/verify-frozen.py /inputs > /out/authenticated-packages.json
python3 -m venv /opt/venv
/opt/venv/bin/python -m pip install --no-index --no-deps /inputs/python/pip-26.2.1-py3-none-any.whl > /out/pip-install.log
mkdir -p /out/baseline
cp /inputs/consumer-wheels/*.whl /out/baseline/
# The exact public wheel is installed first to establish supported replacement.
baseline=(/inputs/consumer-wheels/lxml-6.1.3-*.whl)
test "${#baseline[@]}" = 1
/opt/venv/bin/python -m pip install --no-index --no-deps "${baseline[0]}" > /out/baseline-install.log
/opt/venv/bin/python -m pip show lxml > /out/baseline-identity.txt
/opt/venv/bin/python /inputs/recipe/compatibility.py > /out/baseline-compatibility.json
/opt/venv/bin/python -m pip uninstall -y lxml > /out/uninstall.log
test ! -d /opt/venv/lib/python3.14/site-packages/lxml
wheel=(/wheels/lxml-6.1.3-*.whl)
test "${#wheel[@]}" = 1
/opt/venv/bin/python -m pip install --no-index --no-deps "${wheel[0]}" > /out/install.log
/opt/venv/bin/python /inputs/recipe/compatibility.py > /out/source-compatibility.json
for name in python_docx python_pptx openpyxl pillow xlsxwriter typing_extensions et_xmlfile; do
  files=(/inputs/consumer-wheels/${name}-*.whl)
  test "${#files[@]}" = 1
  /opt/venv/bin/python -m pip install --no-index --no-deps "${files[0]}" >> /out/fixture-install.log
done
/opt/venv/bin/python -m pip list --format=json > /out/installed-python.json
dpkg-query -W > /out/installed-packages.txt
mkdir -p /out/results
chmod 777 /out/results
su -s /bin/sh nobody -c '/opt/venv/bin/python /inputs/recipe/verify-installed.py' > /out/results/installed.json
su -s /bin/sh nobody -c '/opt/venv/bin/python /inputs/recipe/consumer.py' > /out/results/consumer.json
