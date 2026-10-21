#!/usr/bin/env bash
# Offline build from immutable, authenticated input and a frozen native builder.
set -euo pipefail
arch=$(dpkg --print-architecture)
case "$(uname -m):$arch" in aarch64:arm64|x86_64:amd64) ;; *) exit 1 ;; esac
test "${AELIRA_PILLOW_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive PYTHONHASHSEED=0 SOURCE_DATE_EPOCH=0
export PIP_NO_INDEX=1 PIP_FIND_LINKS=/input/python PIP_DISABLE_PIP_VERSION_CHECK=1
umask 022
python3 /recipe/verify-frozen-inputs.py /input /provenance "$arch" > /work/input-chain.log
python3 -m venv /work/build-venv
/work/build-venv/bin/python -m pip install --no-index --no-deps \
  pip==26.2.1 setuptools==84.0.0 pybind11==3.1.0 \
  > /work/python-install.log
mkdir /work/source /work/wheels
tar -xf /input/python/pillow-13.0.0.tar.gz -C /work/source
test -f /work/source/pillow-13.0.0/pyproject.toml
cat > /work/build-constraints.txt <<'EOF'
pybind11==3.1.0
setuptools==84.0.0
EOF
/work/build-venv/bin/python -m pip wheel --no-deps --no-cache-dir \
  --build-constraint /work/build-constraints.txt \
  --wheel-dir /work/wheels /work/source/pillow-13.0.0 \
  > /work/wheel-build.log
/work/build-venv/bin/python /recipe/verify-wheel.py /work/wheels/pillow-13.0.0-*.whl "$arch" \
  > /work/wheel-verification.log
dpkg-query -W -f='${Package}\t${Version}\t${Architecture}\n' \
  > /work/build-packages.tsv
cmp /work/build-packages.tsv /provenance/toolchain-packages.tsv
/work/build-venv/bin/python -m pip freeze > /work/build-python-packages.txt
sha256sum /work/wheels/pillow-13.0.0-*.whl > /work/wheel.sha256
