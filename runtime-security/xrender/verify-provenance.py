#!/usr/bin/env python3
"""Replay signed Ubuntu InRelease -> Sources/Packages -> exact input bytes."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path("/provenance")
LISTS = ROOT / "apt-lists"
SOURCE = Path("/source-input")
authenticated_files = {}
source_records = []
package_records = {}
toolchain_records = {}
REQUIRED = {
    "libxrender_0.9.12.orig.tar.gz",
    "libxrender_0.9.12.orig.tar.gz.asc",
    "libxrender_0.9.12-1build1.diff.gz",
    "libxrender_0.9.12-1build1.dsc",
}


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
    m = re.fullmatch(r"(.+_dists_[^_]+)_([^_]+)_(source_Sources|binary-(?:amd64|arm64|all)_Packages)(?:\..+)?", index.name)
    if not m:
        raise RuntimeError(f"Unexpected index filename: {index.name}")
    release = LISTS / (m[1] + "_InRelease")
    verified = subprocess.run(["gpgv", "--keyring", str(ROOT / "ubuntu-archive-keyring.gpg"),
                               "--output", "-", str(release)], check=True, capture_output=True, timeout=30)
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
    content = subprocess.check_output(["/usr/lib/apt/apt-helper", "cat-file", str(index)])
    if (sha(content), len(content)) != (digest, size):
        raise RuntimeError(f"Signed index hash mismatch: {index.name}")
    authenticated_files[release.name] = {"sha256": sha(release.read_bytes()), "size": release.stat().st_size}
    authenticated_files[index.name] = {"sha256": sha(index.read_bytes()), "size": index.stat().st_size}
    return content.decode()


keyring = ROOT / "ubuntu-archive-keyring.gpg"
if keyring.is_symlink() or not keyring.is_file() or keyring.stat().st_size > 1024 * 1024:
    raise RuntimeError("Invalid Ubuntu archive keyring")
if sha(keyring.read_bytes()) != "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31":
    raise RuntimeError("Archive keyring differs from pinned Ubuntu trust input")

subprocess.run(["sha256sum", "-c", str(ROOT / "apt-index-sha256.txt")],
               cwd=LISTS, check=True, stdout=subprocess.DEVNULL)
subprocess.run(["sha256sum", "-c", str(ROOT / "toolchain-debs.sha256")],
               cwd=ROOT / "toolchain-debs", check=True, stdout=subprocess.DEVNULL)
subprocess.run(["sha256sum", "-c", "/recipe/inputs.sha256"],
               cwd=SOURCE, check=True, stdout=subprocess.DEVNULL)

for index in sorted(LISTS.iterdir()):
    if "_source_Sources" not in index.name and "_Packages" not in index.name:
        continue
    contents = signed_index(index)
    for stanza in contents.split("\n\n"):
        item = fields(stanza)
        if "_source_Sources" in index.name:
            if item.get("Package") != "libxrender" or item.get("Version") != "1:0.9.12-1build1":
                continue
            found = {}
            for line in item["Checksums-Sha256"].splitlines():
                parts = line.split()
                if len(parts) != 3:
                    continue
                digest, size, name = parts
                if name in REQUIRED:
                    data = (SOURCE / name).read_bytes()
                    if (sha(data), len(data)) != (digest, int(size)):
                        raise RuntimeError(f"Source archive differs from signed Sources: {name}")
                    found[name] = (digest, int(size))
            if set(found) != REQUIRED:
                raise RuntimeError("Incomplete signed source stanza")
            source_records.append(found)
        else:
            key = (item.get("Package"), item.get("Version"), item.get("Architecture"))
            if all(key) and item.get("SHA256"):
                package_records.setdefault(key, set()).add(item["SHA256"])
if not source_records or any(record != source_records[0] for record in source_records):
    raise RuntimeError("Missing or conflicting signed Ubuntu source records")

expected = {}
for line in (ROOT / "toolchain-delta.tsv").read_text().splitlines():
    package, version, architecture = line.split("\t")
    expected[(package.split(":")[0], version, architecture)] = False
debs = sorted((ROOT / "toolchain-debs").glob("*.deb"))
if len(debs) != len(expected):
    raise RuntimeError("Toolchain archive count differs from installed delta")
for deb in debs:
    name, version, arch = subprocess.check_output(
        ["dpkg-deb", "--show", "--showformat=${Package}\t${Version}\t${Architecture}\n", str(deb)],
        text=True, timeout=30
    ).strip().split("\t")
    key = (name, version, arch)
    if key not in expected or expected[key]:
        raise RuntimeError(f"Unexpected or duplicate toolchain archive: {deb.name}")
    digest = sha(deb.read_bytes())
    if digest not in package_records.get(key, set()):
        raise RuntimeError(f"Toolchain archive absent from signed Packages: {deb.name}")
    toolchain_records[deb.name] = {"package": name, "version": version, "architecture": arch, "sha256": digest, "size": deb.stat().st_size}
    expected[key] = True
if not all(expected.values()):
    raise RuntimeError("Incomplete toolchain archive delta")
print(json.dumps({"schema": "aelira-xrender-ubuntu-inputs-v1", "status": "pass",
                  "source_files": {name: {"sha256": value[0], "size": value[1]} for name, value in source_records[0].items()},
                  "source_stanzas": len(source_records), "toolchain_files": toolchain_records,
                  "authenticated_files": authenticated_files, "keyring_sha256": sha(keyring.read_bytes())}, sort_keys=True))
