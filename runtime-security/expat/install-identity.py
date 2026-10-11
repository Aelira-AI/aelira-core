"""Verify an Expat DEB install and make its two ELF replacements visible to image diffs."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

VERSION = "2.7.4-1ubuntu0.2+aelira1"
FILES = ("libexpat.so.1.11.2", "libexpatw.so.1.11.2")
TRIPLETS = {"amd64": "x86_64-linux-gnu", "arm64": "aarch64-linux-gnu"}


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def details(path: Path) -> dict[str, int | str]:
    info = path.stat()
    assert stat.S_ISREG(info.st_mode), f"not a regular file: {path}"
    return {
        "sha256": sha256(path),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def archive_files(deb: Path, triplet: str) -> dict[str, dict[str, int | str]]:
    wanted = {f"usr/lib/{triplet}/{name}": name for name in FILES}
    found: dict[str, dict[str, int | str]] = {}
    process = subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(deb)], stdout=subprocess.PIPE
    )
    assert process.stdout is not None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                name = wanted.get(member.name.removeprefix("./"))
                if name is None:
                    continue
                assert (
                    name not in found and member.isfile()
                ), f"invalid archive member: {name}"
                source = archive.extractfile(member)
                assert source is not None
                digest = hashlib.sha256()
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                found[name] = {
                    "sha256": digest.hexdigest(),
                    "size": member.size,
                    "mtime": member.mtime,
                    "mode": oct(member.mode),
                    "uid": member.uid,
                    "gid": member.gid,
                }
    finally:
        process.stdout.close()
    assert process.wait() == 0, "unable to read candidate DEB archive"
    assert set(found) == set(
        FILES
    ), f"candidate archive missing Expat libraries: {sorted(found)}"
    return found


def main() -> None:
    deb = Path(sys.argv[1])
    arch = sys.argv[2]
    expected = dict(zip(FILES, sys.argv[3:], strict=True))
    assert arch in TRIPLETS, f"unsupported architecture: {arch}"
    assert command("dpkg", "--print-architecture") == arch
    package = command("dpkg-deb", "-f", str(deb), "Package")
    version = command("dpkg-deb", "-f", str(deb), "Version")
    package_arch = command("dpkg-deb", "-f", str(deb), "Architecture")
    assert (package, version, package_arch) == ("libexpat1", VERSION, arch)

    archive = archive_files(deb, TRIPLETS[arch])
    paths = {name: Path(f"/usr/lib/{TRIPLETS[arch]}/{name}") for name in FILES}
    before = {name: details(path) for name, path in paths.items()}
    for name in FILES:
        assert (
            archive[name]["sha256"] == expected[name]
        ), f"candidate hash mismatch: {name}"
        assert (
            before[name]["sha256"] != expected[name]
        ), f"candidate does not replace baseline: {name}"

    install = subprocess.run(["dpkg", "-i", str(deb)], capture_output=True, text=True)
    print(f"dpkg_install_exit={install.returncode}", flush=True)
    if install.returncode:
        print((install.stdout + install.stderr)[-4000:], file=sys.stderr, flush=True)
        raise RuntimeError("candidate DEB installation failed")
    installed = command(
        "dpkg-query",
        "-W",
        "-f=${Package} ${Version} ${Architecture}",
        f"libexpat1:{arch}",
    )
    assert installed == f"libexpat1 {VERSION} {arch}", installed

    receipt = {
        "package": package,
        "version": version,
        "architecture": arch,
        "files": {},
    }
    for name, path in paths.items():
        archive_file = archive[name]
        after_install = details(path)
        for field in ("sha256", "size", "mode", "uid", "gid"):
            assert (
                after_install[field] == archive_file[field]
            ), f"installed {name} {field} mismatch"
        assert (
            after_install["sha256"] == expected[name]
        ), f"installed {name} hash mismatch"

        # dpkg preserves the archive mtime, which can equal the parent layer's
        # mtime and size. A deterministic install-only mtime makes the changed
        # bytes visible even to a metadata-based Docker image differ.
        mtime = int(archive_file["mtime"]) + 1
        while mtime == int(int(before[name]["mtime_ns"]) // 1_000_000_000):
            mtime += 1
        current = path.stat()
        os.utime(path, ns=(current.st_atime_ns, mtime * 1_000_000_000))
        after = details(path)
        for field in ("sha256", "size", "mode", "uid", "gid"):
            assert (
                after[field] == after_install[field]
            ), f"mtime adjustment changed {name} {field}"
        assert after["mtime_ns"] == mtime * 1_000_000_000
        assert after["mtime_ns"] != before[name]["mtime_ns"]
        receipt["files"][name] = {
            "before": before[name],
            "archive": archive_file,
            "installed_before_mtime_adjustment": after_install,
            "after": after,
        }
    print("installation_identity=" + json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
