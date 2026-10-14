#!/usr/bin/env python3
"""Replay signed Debian InRelease -> Packages -> frozen native client archives."""

import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path("/provenance")
LISTS = ROOT / "apt-lists"
authenticated_files = {}
package_records = {}
toolchain_records = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fields(stanza):
    result = {}
    key = None
    for line in stanza.splitlines():
        if line.startswith(" ") and key:
            result[key] += "\n" + line
        elif ": " in line:
            key, value = line.split(": ", 1)
            result[key] = value
        elif line.endswith(":"):
            key = line[:-1]
            result[key] = ""
    return result


def signed_index(index):
    m = re.fullmatch(
        r"(.+_dists_[^_]+)_([^_]+)_(source_Sources|binary-(?:amd64|arm64|all)_Packages)(?:\..+)?",
        index.name,
    )
    if not m:
        raise RuntimeError(f"Unexpected index filename: {index.name}")
    release = LISTS / (m[1] + "_InRelease")
    verified = subprocess.run(
        [
            "gpgv",
            "--keyring",
            str(ROOT / "debian-archive-keyring.gpg"),
            "--output",
            "-",
            str(release),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    signed = verified.stdout.decode("utf-8")
    section = signed.split("\nSHA256:\n", 1)[1]
    records = {}
    for line in section.splitlines():
        parts = line.split()
        if len(parts) != 3:
            break
        records[parts[2]] = (parts[0], int(parts[1]))
    key = m[2] + "/" + m[3].replace("_", "/")
    digest, size = records[key]
    content = subprocess.check_output(
        ["/usr/lib/apt/apt-helper", "cat-file", str(index)]
    )
    if (sha(content), len(content)) != (digest, size):
        raise RuntimeError(f"Signed index hash mismatch: {index.name}")
    authenticated_files[release.name] = {
        "sha256": sha(release.read_bytes()),
        "size": release.stat().st_size,
    }
    authenticated_files[index.name] = {
        "sha256": sha(index.read_bytes()),
        "size": index.stat().st_size,
    }
    return content.decode()


keyring = ROOT / "debian-archive-keyring.gpg"
if (
    keyring.is_symlink()
    or not keyring.is_file()
    or keyring.stat().st_size > 1024 * 1024
):
    raise RuntimeError("Invalid Debian archive keyring")
if (
    sha(keyring.read_bytes())
    != "506b815cbb32d9b6066b4a2aa524071e071761e7e7f68c3ac74f3061ba852017"
):
    raise RuntimeError("Archive keyring differs from pinned Debian trust input")

subprocess.run(
    ["sha256sum", "-c", str(ROOT / "apt-index-sha256.txt")],
    cwd=LISTS,
    check=True,
    stdout=subprocess.DEVNULL,
)
subprocess.run(
    ["sha256sum", "-c", str(ROOT / "toolchain-debs.sha256")],
    cwd=ROOT / "toolchain-debs",
    check=True,
    stdout=subprocess.DEVNULL,
)

for index in sorted(LISTS.iterdir()):
    if "_source_Sources" not in index.name and "_Packages" not in index.name:
        continue
    contents = signed_index(index)
    for stanza in contents.split("\n\n"):
        item = fields(stanza)
        key = (item.get("Package"), item.get("Version"), item.get("Architecture"))
        if all(key) and item.get("SHA256"):
            package_records.setdefault(key, set()).add(item["SHA256"])

expected = {}
for line in (ROOT / "toolchain-delta.tsv").read_text().splitlines():
    package, version, architecture = line.split("\t")
    expected[(package.split(":")[0], version, architecture)] = False
debs = sorted((ROOT / "toolchain-debs").glob("*.deb"))
if len(debs) != len(expected):
    raise RuntimeError("Toolchain archive count differs from installed delta")
for deb in debs:
    name, version, arch = (
        subprocess.check_output(
            [
                "dpkg-deb",
                "--show",
                "--showformat=${Package}\t${Version}\t${Architecture}\n",
                str(deb),
            ],
            text=True,
            timeout=30,
        )
        .strip()
        .split("\t")
    )
    key = (name, version, arch)
    if key not in expected or expected[key]:
        raise RuntimeError(f"Unexpected or duplicate toolchain archive: {deb.name}")
    digest = sha(deb.read_bytes())
    if digest not in package_records.get(key, set()):
        raise RuntimeError(f"Toolchain archive absent from signed Packages: {deb.name}")
    toolchain_records[deb.name] = {
        "package": name,
        "version": version,
        "architecture": arch,
        "sha256": digest,
        "size": deb.stat().st_size,
    }
    expected[key] = True
if not all(expected.values()):
    raise RuntimeError("Incomplete toolchain archive delta")
print(
    json.dumps(
        {
            "schema": "aelira-xdg-client-inputs-v1",
            "status": "pass",
            "toolchain_files": toolchain_records,
            "authenticated_files": authenticated_files,
            "keyring_sha256": sha(keyring.read_bytes()),
        },
        sort_keys=True,
    )
)
