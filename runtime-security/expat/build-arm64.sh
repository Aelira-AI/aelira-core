#!/bin/sh
set -eu
test "$(dpkg --print-architecture)" = arm64
: "${AELIRA_EXPAT_HOST_ARCH:=arm64}"
export AELIRA_EXPAT_HOST_ARCH
exec /bin/sh /recipe/build-native.sh
