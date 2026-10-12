#!/usr/bin/env bash
# Run only in a disposable native API baseline container with network disabled.
set -euo pipefail
arch=$(dpkg --print-architecture)
test "${AELIRA_PILLOW_HOST_ARCH:-}" = "$arch"
export DEBIAN_FRONTEND=noninteractive
mkdir -p /var/lib/apt/lists /var/cache/apt/archives
cp /input/apt-lists/* /var/lib/apt/lists/
cp /input/apt-debs/*.deb /var/cache/apt/archives/
python3 - "$arch" <<'PY'
"""Keep the accepted TIFF link visible to metadata-based image layer diffs."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

arch = sys.argv[1]
accepted = {
    "arm64": (
        "aarch64-linux-gnu",
        "64cbba6dd3595f477b3005618693f392a860e7810f269fd929f69e529a0ca719",
        "63e0add472fc1c99cbcfadcf58df8dc11d8da6516f224ecb57ff12e06029f384",
        592088,
    ),
    "amd64": (
        "x86_64-linux-gnu",
        "222b765e38f7a8db036b3f2fc3c3f14cab5226587c9dbba84f3f66c142661b29",
        "f45a58ee6e6d76dd51da58b0d721b3cf4d49d3a434f224340a7f30646b21d340",
        612592,
    ),
}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


require(arch in accepted, "unsupported architecture")
triplet, deb_sha, elf_sha, elf_size = accepted[arch]
directory = Path("/usr/lib") / triplet
require(directory.is_dir() and directory.resolve() == directory, "unsafe TIFF directory")
link = directory / "libtiff.so.6"
elf = directory / "libtiff.so.6.3.0"
deb = Path(f"/input/tiff/libtiff6_4.7.2-0aelira1_{arch}.deb")
require(deb.is_file() and digest(deb) == deb_sha, "TIFF DEB identity mismatch")
fields = subprocess.check_output(
    ["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"],
    text=True,
).splitlines()
controls = tuple(line.split(": ", 1)[1] for line in fields)
require(controls == ("libtiff6", "4.7.2-0aelira1", arch), "TIFF DEB controls mismatch")

archive = {}
process = subprocess.Popen(
    ["dpkg-deb", "--fsys-tarfile", str(deb)],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
)
require(process.stdout is not None, "TIFF archive unavailable")
try:
    with tarfile.open(fileobj=process.stdout, mode="r|") as members:
        for member in members:
            name = member.name.removeprefix("./")
            if name not in {
                f"usr/lib/{triplet}/libtiff.so.6",
                f"usr/lib/{triplet}/libtiff.so.6.3.0",
            }:
                continue
            require(name not in archive, "duplicate TIFF archive member")
            require(member.uid == member.gid == 0, "TIFF archive owner mismatch")
            if name.endswith("libtiff.so.6"):
                require(
                    member.issym()
                    and member.linkname == "libtiff.so.6.3.0"
                    and member.mode == 0o777,
                    "TIFF archive link mismatch",
                )
                archive[name] = member
            else:
                require(
                    member.isfile()
                    and member.mode == 0o644
                    and member.size == elf_size,
                    "TIFF archive ELF metadata mismatch",
                )
                source = members.extractfile(member)
                require(source is not None, "TIFF archive ELF unavailable")
                h = hashlib.sha256()
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    h.update(block)
                require(h.hexdigest() == elf_sha, "TIFF archive ELF bytes mismatch")
                archive[name] = member
finally:
    process.stdout.close()
require(process.wait() == 0 and len(archive) == 2, "TIFF archive members mismatch")

require(link.is_symlink(), "baseline TIFF link mismatch")
before = link.lstat()
require(
    stat.S_ISLNK(before.st_mode)
    and stat.S_IMODE(before.st_mode) == 0o777
    and before.st_uid == before.st_gid == 0,
    "baseline TIFF link mismatch",
)
subprocess.run(
    [
        "apt-get", "install", "-y", "--no-download", "--no-install-recommends",
        "libavif16", "libraqm0", "libwebpdemux2", "libwebpmux3", "python3-venv",
    ],
    check=True,
)
require(link.is_symlink(), "preinstall TIFF link mismatch")
before_install = link.lstat()
require(
    stat.S_ISLNK(before_install.st_mode)
    and stat.S_IMODE(before_install.st_mode) == 0o777
    and before_install.st_uid == before_install.st_gid == 0,
    "preinstall TIFF link mismatch",
)
subprocess.run(["dpkg", "-i", str(deb)], check=True)


def installed_identity() -> os.stat_result:
    package = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package} ${Version} ${Architecture}", "libtiff6"],
        text=True,
    ).strip()
    require(package == f"libtiff6 4.7.2-0aelira1 {arch}", "installed TIFF package mismatch")
    link_info = link.lstat()
    elf_info = elf.lstat()
    require(
        stat.S_ISLNK(link_info.st_mode)
        and os.readlink(link) == "libtiff.so.6.3.0"
        and link_info.st_size == len("libtiff.so.6.3.0")
        and stat.S_IMODE(link_info.st_mode) == 0o777
        and link_info.st_uid == link_info.st_gid == 0,
        "installed TIFF link mismatch",
    )
    require(
        stat.S_ISREG(elf_info.st_mode)
        and elf_info.st_size == elf_size
        and stat.S_IMODE(elf_info.st_mode) == 0o644
        and elf_info.st_uid == elf_info.st_gid == 0
        and digest(elf) == elf_sha,
        "installed TIFF ELF mismatch",
    )
    verified = subprocess.run(["dpkg", "-V", "libtiff6"], capture_output=True)
    require(
        verified.returncode == 0 and not verified.stdout and not verified.stderr,
        "installed TIFF package files mismatch",
    )
    return link_info


installed = installed_identity()
archive_link = archive[f"usr/lib/{triplet}/libtiff.so.6"]
mtime = int(archive_link.mtime) + 1
while mtime in {
    before.st_mtime_ns // 1_000_000_000,
    before_install.st_mtime_ns // 1_000_000_000,
    installed.st_mtime_ns // 1_000_000_000,
}:
    mtime += 1
os.utime(link, ns=(installed.st_atime_ns, mtime * 1_000_000_000), follow_symlinks=False)
after = installed_identity()
require(
    after.st_mtime_ns == mtime * 1_000_000_000
    and after.st_mtime_ns // 1_000_000_000 != before.st_mtime_ns // 1_000_000_000
    and after.st_size == installed.st_size
    and stat.S_IMODE(after.st_mode) == stat.S_IMODE(installed.st_mode)
    and (after.st_uid, after.st_gid) == (installed.st_uid, installed.st_gid),
    "TIFF link timestamp identity mismatch",
)
print(f"pillow_tiff_install_identity=pass architecture={arch}")
PY
test "$(dpkg-query -W -f='${Version}' libtiff6)" = '4.7.2-0aelira1'
/opt/venv/bin/python -m ensurepip --upgrade
/opt/venv/bin/python -m pip install --no-index --no-deps --force-reinstall \
  /wheel/pillow-12.3.0-*.whl
/opt/venv/bin/python -m pip uninstall --yes pip
test ! -d /opt/venv/lib/python3.14/site-packages/pillow.libs
