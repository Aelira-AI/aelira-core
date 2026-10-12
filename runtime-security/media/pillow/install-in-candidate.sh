#!/usr/bin/env bash
# Run only in a disposable native API baseline container with network disabled.
set -euo pipefail
arch=$(dpkg --print-architecture)
test "${AELIRA_PILLOW_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive
mkdir -p /var/lib/apt/lists /var/cache/apt/archives
cp /input/apt-lists/* /var/lib/apt/lists/
cp /input/apt-debs/*.deb /var/cache/apt/archives/
apt-get install -y --no-download --no-install-recommends \
  libavif16 libraqm0 libwebpdemux2 libwebpmux3 python3-venv
dpkg -i /input/tiff/libtiff6_4.7.2-0aelira1_${arch}.deb
test "$(dpkg-query -W -f='${Version}' libtiff6)" = '4.7.2-0aelira1'
/opt/venv/bin/python -m ensurepip --upgrade
/opt/venv/bin/python -m pip install --no-index --no-deps --force-reinstall \
  /wheel/pillow-12.3.0-*.whl
/opt/venv/bin/python -m pip uninstall --yes pip
test ! -d /opt/venv/lib/python3.14/site-packages/pillow.libs
