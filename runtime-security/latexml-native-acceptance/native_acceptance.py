#!/usr/bin/env python3
"""Native source-bound LaTeXML component acceptance for Aelira issue #600."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile

OPERATOR_ORIGIN = "https://github.com/Aelira-AI/aelira-core.git"
UPSTREAM_ORIGIN = "https://github.com/brucemiller/LaTeXML.git"
UPSTREAM_HEAD = "0db6f47ea859391f9099e6e25e7405afad8467bf"
UPSTREAM_TREE = "2693c0c65c86f6b36370af18496d8283cf4f73ea"
MAKEFILE_SHA = "dd354e2809bb4de91f0a28fc22f50edcd5ef2b4653b961b156284cf355002d72"
VERSION_TEMPLATE_SHA = (
    "467b5dad2bdf9d15561d7faf53722b1748a63cc62c53ff21e1cd60227726f103"
)
GRAMMAR_SHA = "4e04d9b04296951dc84031bdc6619a6e4b319eaa63419d28592562ae9fea507b"
UBUNTU_MANIFEST = (
    "sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7"
)
KEYRING_SHA = "80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31"
PINNED_ARM_LOCK = "f63058d835f3d32a9c36819048388a48c48ee2a01a082fbf047288a806cbd85c"
PINNED_ARM_PERL_SHA = "51d562fb5f145f8c12610790b6f67b2868d1aa7c977906b533250f00903681cd"
PINNED_ARM_RECDESCENT_SHA = (
    "093ddd1287581fa4369c1dbc71f3696af3348c7e60da88de60af5b1db15d9680"
)
RECIPE = {
    "Dockerfile.probe": "f54aaefc5eb165fc6d4e12b8f3ee1148ffefc6cc675e13ff19f5f16eda41ac1a",
    "README.md": "a4b0bc04532434dab0f1c8406ed09ceb3f56bf2c62edb3eab23615f1589ca8ce",
    "accept_image.py": "4bedf36f828859ad0a8b27ba357869c2bc313b3e6edf38c1f4f0e6532dccc3f4",
    "package_source.py": "52a70e0bb2d0334ffdeb30d489c342c7a499db7d491f47919e2ddcc72ae617cc",
    "prepare-source.sh": "b4073547e741a222bb519c1661ade015136c46ed7577c17b9158c13555363987",
    "probe_cases.py": "4c7a56b4a30137677d0bcab24a01bd6efcac2d1bbe0019038dbd04c3d7f8b416",
    "probe_integrated.py": "d415a50f08ee7ed7fd480d298f3e274ca81891d62841ec11696da0419b142f30",
    "siunitx-v3-adapter.patch": "8bea4050c667438c49e5a175dbaa52bb75415fd7eb4ec0a6508eda20ddf4795b",
    "siunitx.sty.ltxml": "cbcb285c38233641d1cab2170fad611e0d39b95ce325f5a405aba8c2895968d2",
    "verify_install.py": "120f0a32c1a55be777cf9e8663f26020c8b862758c6f1464db388e8cd31e787b",
}
PACKAGES = (
    "perl",
    "python3",
    "libxml-libxml-perl",
    "libxml-libxslt-perl",
    "libarchive-zip-perl",
    "libimage-size-perl",
    "libfile-which-perl",
    "libjson-xs-perl",
    "libparse-recdescent-perl",
    "libuuid-tiny-perl",
    "libpod-parser-perl",
    "libio-string-perl",
    "libwww-perl",
    "libtext-unidecode-perl",
    "liburi-perl",
    "imagemagick",
    "libimage-magick-perl",
    "texlive-latex-base",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def require(test: bool, message: str) -> None:
    if not test:
        raise ValueError(message)


def command(
    args: list[str],
    *,
    log: Path | None = None,
    timeout: int = 1200,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    p = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if log is not None:
        log.write_bytes(p.stdout + b"\n[stderr]\n" + p.stderr)
    return p


def value(args: list[str]) -> str:
    p = command(args, timeout=30)
    require(p.returncode == 0, "identity command failed")
    return p.stdout.decode().strip()


def exact_file(path: Path, expected: str) -> None:
    require(
        path.is_file() and not path.is_symlink(), "source member unavailable or aliased"
    )
    require(sha(path.read_bytes()) == expected, "source member hash mismatch")


def git_object(repo: Path, ref: str, name: str) -> bytes:
    entry = command(
        ["/usr/bin/git", "-C", str(repo), "ls-tree", "-z", ref, "--", name], timeout=30
    )
    require(entry.returncode == 0, "selected Git member lookup failed")
    fields = entry.stdout.split(b"\0")
    require(
        len(fields) == 2 and fields[1] == b"", "selected Git member closure mismatch"
    )
    header, separator, path = fields[0].partition(b"\t")
    require(
        separator == b"\t" and path.decode() == name,
        "selected Git member path mismatch",
    )
    pieces = header.split()
    require(
        len(pieces) == 3
        and pieces[0] in (b"100644", b"100755")
        and pieces[1] == b"blob"
        and re.fullmatch(rb"[0-9a-f]{40}", pieces[2]),
        "selected Git member is not a regular blob",
    )
    data = command(
        ["/usr/bin/git", "-C", str(repo), "cat-file", "blob", pieces[2].decode()],
        timeout=30,
    )
    require(data.returncode == 0, "selected Git blob unavailable")
    return data.stdout


def selected_repo(repo: Path, ref: str, tree: str) -> None:
    require(
        repo.is_absolute() and repo.is_dir() and not repo.is_symlink(),
        "operator checkout unavailable or aliased",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", ref) and re.fullmatch(r"[0-9a-f]{40}", tree),
        "operator object identifier malformed",
    )
    require(
        value(["/usr/bin/git", "-C", str(repo), "remote", "get-url", "origin"])
        == OPERATOR_ORIGIN,
        "operator origin mismatch",
    )
    require(
        value(["/usr/bin/git", "-C", str(repo), "rev-parse", ref + "^{commit}"]) == ref,
        "operator commit mismatch",
    )
    require(
        value(["/usr/bin/git", "-C", str(repo), "rev-parse", ref + "^{tree}"]) == tree,
        "operator tree mismatch",
    )
    for name, digest in RECIPE.items():
        require(
            sha(git_object(repo, ref, "runtime-security/latexml/" + name)) == digest,
            "selected recipe blob mismatch",
        )


def binding(repo: Path, ref: str, tree: str, kind: str, remote_ref: str | None) -> dict:
    selected_repo(repo, ref, tree)
    require(kind in ("public", "synthetic"), "source kind malformed")
    require(
        kind == "synthetic"
        or (
            remote_ref is not None
            and re.fullmatch(r"refs/(?:heads|tags|pull)/[A-Za-z0-9_./-]+", remote_ref)
        ),
        "public remote ref malformed",
    )
    require(
        kind == "public" or remote_ref is None,
        "synthetic source cannot claim public remote ref",
    )
    require(
        git_object(
            repo, ref, "runtime-security/latexml-native-acceptance/native_acceptance.py"
        )
        == Path(__file__).read_bytes(),
        "selected runner differs from executing source",
    )
    if kind == "public":
        result = command(
            [
                "/usr/bin/git",
                "-C",
                str(repo),
                "ls-remote",
                "--exit-code",
                "origin",
                remote_ref,
            ],
            timeout=60,
        )
        require(
            result.returncode == 0
            and result.stdout.decode().splitlines() == [ref + "\t" + remote_ref],
            "selected public remote ref differs",
        )
    return {
        "schema": "aelira-latexml-native-source-binding-v1",
        "source_kind": kind,
        "operator_origin": OPERATOR_ORIGIN,
        "operator_ref": ref,
        "operator_tree": tree,
        "remote_ref": remote_ref,
        "upstream_origin": UPSTREAM_ORIGIN,
        "upstream_ref": UPSTREAM_HEAD,
        "upstream_tree": UPSTREAM_TREE,
        "recipe_sha256": RECIPE,
        "runner_sha256": sha(Path(__file__).read_bytes()),
    }


def prepare(
    output: Path,
    operator_repo: Path,
    operator_ref: str,
    operator_tree: str,
    source_kind: str,
    remote_ref: str | None,
) -> None:
    expected = binding(
        operator_repo, operator_ref, operator_tree, source_kind, remote_ref
    )
    runner_name = "runtime-security/latexml-native-acceptance/native_acceptance.py"
    readme_name = "runtime-security/latexml-native-acceptance/README.md"
    require(
        git_object(operator_repo, operator_ref, runner_name)
        == Path(__file__).read_bytes(),
        "selected runner differs from executing source",
    )
    require(
        git_object(operator_repo, operator_ref, readme_name)
        == Path(__file__).with_name("README.md").read_bytes(),
        "selected README differs from executing source",
    )
    if source_kind == "public":
        result = command(
            [
                "/usr/bin/git",
                "-C",
                str(operator_repo),
                "ls-remote",
                "--exit-code",
                "origin",
                remote_ref,
            ],
            timeout=60,
        )
        require(
            result.returncode == 0
            and result.stdout.decode().splitlines()
            == [operator_ref + "\t" + remote_ref],
            "selected public remote ref differs",
        )
    require(
        output.is_absolute() and not output.exists() and output.parent.is_dir(),
        "output must be a new absolute directory",
    )
    output.mkdir()
    (output / "binding.json").write_bytes(canonical(expected))
    members = {
        "recipe/"
        + name: git_object(
            operator_repo, operator_ref, "runtime-security/latexml/" + name
        )
        for name in RECIPE
    }
    members["native_acceptance.py"] = git_object(
        operator_repo, operator_ref, runner_name
    )
    members["README.md"] = git_object(operator_repo, operator_ref, readme_name)
    members["binding.json"] = (output / "binding.json").read_bytes()
    with tarfile.open(output / "transport.tar", "w") as tar:
        for name in sorted(members):
            data = members[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    result = {
        "schema": "aelira-latexml-native-transport-v2",
        "transport_sha256": sha((output / "transport.tar").read_bytes()),
        "transport_size": (output / "transport.tar").stat().st_size,
        "binding_sha256": sha((output / "binding.json").read_bytes()),
        "runner_sha256": expected["runner_sha256"],
        "source_kind": source_kind,
        "operator_ref": operator_ref,
        "operator_tree": operator_tree,
    }
    (output / "transport-receipt.json").write_bytes(canonical(result))
    print(json.dumps(result, sort_keys=True))


def exact_closure(
    root: Path, files: set[str], directories: set[str], label: str
) -> None:
    """No-follow walk: unreadable directories raise, including unexpected ones."""
    require(
        root.is_dir() and not root.is_symlink() and root.resolve() == root,
        label + " root unavailable or aliased",
    )
    found_files: set[str] = set()
    found_dirs: set[str] = set()

    def visit(folder: Path) -> None:
        with os.scandir(folder) as entries:
            for entry in entries:
                path = folder / entry.name
                metadata = entry.stat(follow_symlinks=False)
                name = path.relative_to(root).as_posix()
                if stat.S_ISDIR(metadata.st_mode):
                    found_dirs.add(name)
                    visit(path)
                else:
                    require(
                        stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                        label + " alias or special member",
                    )
                    found_files.add(name)

    visit(root)
    require(
        found_files == files and found_dirs == directories,
        label + " member closure mismatch",
    )


def verify_transport(
    source: Path, repo: Path, ref: str, tree: str, kind: str, remote_ref: str | None
) -> dict:
    require(
        source.is_dir() and not source.is_symlink(),
        "transport source unavailable or aliased",
    )
    expected = binding(repo, ref, tree, kind, remote_ref)
    allowed = {"recipe/" + name for name in RECIPE} | {
        "native_acceptance.py",
        "README.md",
        "binding.json",
    }
    exact_closure(source, allowed, {"recipe"}, "transport")
    raw = (source / "binding.json").read_bytes()
    require(
        json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        == expected,
        "transport binding mismatch",
    )
    for name, digest in RECIPE.items():
        exact_file(source / "recipe" / name, digest)
    exact_file(source / "native_acceptance.py", expected["runner_sha256"])
    require(
        (source / "native_acceptance.py").read_bytes() == Path(__file__).read_bytes(),
        "running source differs from selected transport",
    )
    require(
        (source / "README.md").read_bytes()
        == git_object(
            repo, ref, "runtime-security/latexml-native-acceptance/README.md"
        ),
        "transport README differs from selected Git object",
    )
    return expected


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out = {}
    for key, item in pairs:
        require(key not in out, "duplicate JSON key")
        out[key] = item
    return out


def reject_constant(value: str) -> None:
    raise ValueError("nonfinite JSON number")


def clean_files(directory: Path, suffix: str) -> list[Path]:
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and not p.is_symlink() and p.name.endswith(suffix)
    )


def paragraphs(data: bytes) -> list[dict[str, str]]:
    rows = []
    for section in data.decode("utf-8").split("\n\n"):
        row: dict[str, str] = {}
        key = ""
        for line in section.splitlines():
            if line.startswith(" ") and key:
                row[key] += " " + line.strip()
            elif ": " in line:
                key, text = line.split(": ", 1)
                row[key] = text
        if row:
            rows.append(row)
    return rows


def verify_apt(apt: Path, architecture: str) -> dict:
    key = apt / "ubuntu-archive-keyring.gpg"
    exact_file(key, KEYRING_SHA)
    signed = clean_files(apt / "lists", "_InRelease")
    indexes = clean_files(apt / "lists", "_Packages.lz4")
    debs = clean_files(apt / "debs", ".deb")
    require(len(signed) == 4 and indexes and debs, "APT closure incomplete")
    releases: dict[str, dict[str, tuple[str, int]]] = {}
    for file in signed:
        p = command(["/usr/bin/gpgv", "--keyring", str(key), str(file)], timeout=30)
        require(p.returncode == 0, "signed index authentication failed")
        text = file.read_text()
        require(
            "Suite: resolute" in text or "Suite: resolute-" in text,
            "unexpected release suite",
        )
        table: dict[str, tuple[str, int]] = {}
        sha_section = text.split("SHA256:\n", 1)
        require(len(sha_section) == 2, "release missing SHA256")
        for line in sha_section[1].splitlines():
            match = re.fullmatch(r"\s*([0-9a-f]{64})\s+(\d+)\s+(\S+)", line)
            if match:
                table[match[3]] = (match[1], int(match[2]))
            elif line and not line.startswith(" "):
                break
        releases[file.name.removesuffix("_InRelease")] = table
    records: dict[tuple[str, str, str], set[tuple[str, int]]] = {}
    for file in indexes:
        match = re.fullmatch(
            r"(.+_dists_(?:resolute|resolute-updates|resolute-security|resolute-backports))_(main|universe|restricted|multiverse)_binary-(arm64|amd64)_Packages\.lz4",
            file.name,
        )
        require(
            match is not None and match[3] == architecture, "unexpected Packages index"
        )
        release = releases.get(match[1])
        require(release is not None, "Packages index has no signed release")
        data = command(["/usr/lib/apt/apt-helper", "cat-file", str(file)], timeout=60)
        require(data.returncode == 0, "Packages index decompression failed")
        path = f"{match[2]}/binary-{architecture}/Packages"
        require(
            release.get(path) == (sha(data.stdout), len(data.stdout)),
            "Packages index differs from signed release",
        )
        for row in paragraphs(data.stdout):
            if {"Package", "Version", "Architecture", "SHA256", "Size"} <= row.keys():
                identity = (row["Package"], row["Version"], row["Architecture"])
                records.setdefault(identity, set()).add(
                    (row["SHA256"], int(row["Size"]))
                )
    inventory = []
    seen: set[tuple[str, str, str]] = set()
    for file in debs:
        fields = {}
        for field in ("Package", "Version", "Architecture"):
            fields[field] = value(["/usr/bin/dpkg-deb", "--field", str(file), field])
        identity = (fields["Package"], fields["Version"], fields["Architecture"])
        require(
            identity not in seen and identity[2] in (architecture, "all"),
            "duplicate or wrong-architecture DEB",
        )
        seen.add(identity)
        digest = sha(file.read_bytes())
        size = file.stat().st_size
        require(
            records.get(identity) == {(digest, size)},
            "DEB not uniquely bound to signed Packages",
        )
        inventory.append(
            {
                "package": identity[0],
                "version": identity[1],
                "architecture": identity[2],
                "sha256": digest,
                "size": size,
            }
        )
    members = []
    for folder in ("lists", "debs"):
        selected = signed + indexes if folder == "lists" else debs
        for file in sorted(selected):
            require(
                file.is_file() and not file.is_symlink(),
                "APT closure has nonregular member",
            )
            members.append(
                {
                    "name": folder + "/" + file.name,
                    "sha256": sha(file.read_bytes()),
                    "size": file.stat().st_size,
                }
            )
    lock = {
        "schema": "aelira-latexml-apt-input-v1",
        "ubuntu_base_manifest": UBUNTU_MANIFEST,
        "archive_keyring_sha256": KEYRING_SHA,
        "architecture": architecture,
        "signed_releases": len(signed),
        "packages_indexes": len(indexes),
        "debs": inventory,
        "files": members,
    }
    (apt / "input-lock.json").write_bytes(canonical(lock))
    return lock


def docker_run(
    image: str, args: list[str], log: Path, timeout: int = 180
) -> subprocess.CompletedProcess[bytes]:
    return command(
        [
            "/usr/bin/docker",
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "2",
            "--memory",
            "3g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=768m",
            "--entrypoint",
            args[0],
            image,
            *args[1:],
        ],
        log=log,
        timeout=timeout,
    )


def run(
    scratch: Path,
    upstream_repo: Path,
    apt_input: Path,
    apt_lock_sha256: str,
    operator_repo: Path,
    operator_ref: str,
    operator_tree: str,
    source_kind: str,
    remote_ref: str | None,
    architecture: str,
) -> None:
    require(
        scratch.is_dir() and not scratch.is_symlink() and scratch.resolve() == scratch,
        "scratch unavailable or aliased",
    )
    require(
        upstream_repo.is_absolute()
        and upstream_repo.is_dir()
        and not upstream_repo.is_symlink(),
        "upstream checkout unavailable or aliased",
    )
    source = scratch / "source"
    source_binding = verify_transport(
        source, operator_repo, operator_ref, operator_tree, source_kind, remote_ref
    )
    private = scratch / "private"
    private.mkdir(exist_ok=False)
    arch_machine = platform.machine()
    require(arch_machine in ("aarch64", "x86_64"), "unsupported host architecture")
    arch = "arm64" if arch_machine == "aarch64" else "amd64"
    require(architecture == arch, "requested architecture differs from native host")
    daemon = value(["/usr/bin/docker", "info", "--format", "{{.Architecture}}"])
    require(
        daemon == arch_machine or daemon == arch,
        "Docker daemon architecture differs from host",
    )
    base_ref = "ubuntu:26.04@" + UBUNTU_MANIFEST
    base_data = json.loads(value(["/usr/bin/docker", "image", "inspect", base_ref]))[0]
    require(
        base_data["Architecture"] == arch and base_data["Os"] == "linux",
        "base architecture mismatch",
    )
    base_id = base_data["Id"]
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", base_id) is not None, "base ID malformed"
    )
    upstream = ["/usr/bin/git", "-C", str(upstream_repo)]
    require(
        value(upstream + ["remote", "get-url", "origin"]) == UPSTREAM_ORIGIN,
        "upstream origin mismatch",
    )
    require(
        value(upstream + ["rev-parse", "HEAD"]) == UPSTREAM_HEAD,
        "upstream HEAD mismatch",
    )
    require(
        value(upstream + ["rev-parse", "HEAD^{tree}"]) == UPSTREAM_TREE,
        "upstream tree mismatch",
    )
    makefile = command(upstream + ["show", "HEAD:Makefile.PL"], timeout=30)
    require(
        makefile.returncode == 0 and sha(makefile.stdout) == MAKEFILE_SHA,
        "official generated-module rule mismatch",
    )
    require(
        apt_input.is_absolute() and apt_input.is_dir() and not apt_input.is_symlink(),
        "frozen APT input missing or aliased",
    )
    require(
        re.fullmatch(r"[0-9a-f]{64}", apt_lock_sha256) is not None,
        "APT lock digest malformed",
    )
    if arch == "arm64":
        require(
            apt_lock_sha256 == PINNED_ARM_LOCK,
            "native ARM input lock differs from reviewed closure",
        )
    apt = scratch / "apt"
    exact_file(apt_input / "input-lock.json", apt_lock_sha256)
    declared = json.loads(
        (apt_input / "input-lock.json").read_bytes(),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    require(
        type(declared) is dict
        and type(declared.get("files")) is list
        and 1 <= len(declared["files"]) <= 1000,
        "frozen APT lock member count mismatch",
    )
    expected_input_members = {"input-lock.json", "ubuntu-archive-keyring.gpg"}
    for member in declared["files"]:
        require(
            type(member) is dict and set(member) == {"name", "sha256", "size"},
            "frozen APT lock member malformed",
        )
        require(
            type(member["size"]) is int
            and 0 < member["size"] <= 200_000_000
            and type(member["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", member["sha256"]),
            "frozen APT member metadata malformed",
        )
        require(type(member["name"]) is str, "frozen APT member name malformed")
        expected_input_members.add(member["name"])
    exact_closure(
        apt_input, expected_input_members, {"lists", "debs"}, "frozen APT input"
    )
    (apt / "lists").mkdir(parents=True)
    (apt / "debs").mkdir()
    for name in ("input-lock.json", "ubuntu-archive-keyring.gpg"):
        item = apt_input / name
        require(
            item.is_file() and not item.is_symlink(),
            "frozen APT metadata unavailable or aliased",
        )
        shutil.copyfile(item, apt / name)
    seen_inputs = set()
    for member in declared["files"]:
        require(
            type(member) is dict and set(member) == {"name", "sha256", "size"},
            "frozen APT lock member malformed",
        )
        name = member["name"]
        require(
            type(name) is str
            and name not in seen_inputs
            and name.count("/") == 1
            and name.split("/")[0] in ("lists", "debs")
            and all(part not in ("", ".", "..") for part in name.split("/")),
            "frozen APT member path invalid",
        )
        seen_inputs.add(name)
        item = apt_input / name
        require(
            item.is_file()
            and not item.is_symlink()
            and item.stat().st_size == member["size"]
            and sha(item.read_bytes()) == member["sha256"],
            "frozen APT member bytes mismatch",
        )
        shutil.copyfile(item, apt / name)
    lock = verify_apt(apt, arch)
    input_sha = sha((apt / "input-lock.json").read_bytes())
    require(input_sha == apt_lock_sha256, "verified APT lock mismatch")
    exact_closure(
        apt,
        {"input-lock.json", "ubuntu-archive-keyring.gpg"}
        | {x["name"] for x in lock["files"]},
        {"lists", "debs"},
        "APT closure",
    )
    context = scratch / "dependency-context"
    context.mkdir()
    (context / "lists").mkdir()
    (context / "debs").mkdir()
    for member in lock["files"]:
        shutil.copyfile(apt / member["name"], context / member["name"])
    exact_closure(
        context,
        {x["name"] for x in lock["files"]},
        {"lists", "debs"},
        "dependency context",
    )
    for member in lock["files"]:
        copied = context / member["name"]
        require(
            copied.stat().st_size == member["size"]
            and sha(copied.read_bytes()) == member["sha256"],
            "dependency context input mismatch",
        )
    dockerfile = (
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
        "COPY lists/ /var/lib/apt/lists/\n"
        "COPY debs/ /var/cache/apt/archives/\n"
        "RUN apt-get -y --no-install-recommends --no-download install "
        + " ".join(PACKAGES)
        + "\n"
        "USER 1000:1000\n"
    )
    (context / "Dockerfile").write_text(dockerfile)
    iid = scratch / "dependency-image-id.txt"
    build = command(
        [
            "/usr/bin/docker",
            "build",
            "--network",
            "none",
            "--pull=false",
            "--no-cache",
            "--build-arg",
            "BASE_IMAGE=" + base_id,
            "--iidfile",
            str(iid),
            str(context),
        ],
        log=private / "dependency-build.log",
        timeout=1200,
        env={**os.environ, "DOCKER_BUILDKIT": "0"},
    )
    require(build.returncode == 0 and iid.is_file(), "offline dependency build failed")
    dependency_id = iid.read_text().strip()
    dependency_data = json.loads(
        value(["/usr/bin/docker", "image", "inspect", dependency_id])
    )[0]
    require(
        dependency_data["Architecture"] == arch
        and dependency_data["Id"] == dependency_id,
        "dependency image identity mismatch",
    )
    require(
        dependency_data["RootFS"]["Layers"][: len(base_data["RootFS"]["Layers"])]
        == base_data["RootFS"]["Layers"],
        "dependency image base ancestry mismatch",
    )
    native_tools = docker_run(
        dependency_id,
        ["/usr/bin/sha256sum", "/usr/bin/perl", "/usr/share/perl5/Parse/RecDescent.pm"],
        private / "generator-tools.log",
    )
    require(
        native_tools.returncode == 0, "native generator tool identities unavailable"
    )
    tool_hashes = [
        line.split()[0] for line in native_tools.stdout.decode().splitlines()
    ]
    require(
        len(tool_hashes) == 2
        and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in tool_hashes),
        "native generator tool digests malformed",
    )
    if arch == "arm64":
        require(
            tool_hashes == [PINNED_ARM_PERL_SHA, PINNED_ARM_RECDESCENT_SHA],
            "native ARM generator tools differ from reviewed closure",
        )
    dependency_arch = docker_run(
        dependency_id,
        ["/usr/bin/uname", "-m"],
        private / "dependency-container-arch.log",
    )
    require(
        dependency_arch.returncode == 0
        and dependency_arch.stdout.decode().strip() == arch_machine,
        "dependency container is not native",
    )
    source_outputs = []
    for label in ("a", "b"):
        out = scratch / ("package-" + label)
        p = command(
            [
                "/usr/bin/python3",
                str(source / "recipe" / "package_source.py"),
                "--git",
                "/usr/bin/git",
                "--repo",
                str(upstream_repo),
                "--patch",
                str(source / "recipe" / "siunitx-v3-adapter.patch"),
                "--out",
                str(out),
                "--docker",
                "/usr/bin/docker",
                "--generator-image-id",
                dependency_id,
                "--architecture",
                arch,
                "--perl-sha256",
                tool_hashes[0],
                "--recdescent-sha256",
                tool_hashes[1],
            ],
            log=private / ("package-" + label + ".log"),
            timeout=900,
        )
        require(p.returncode == 0, "source-bound generated runtime export failed")
        source_outputs.append(out)
    package_files = (
        "latexml-source.tar",
        "source-manifest.json",
        "latexml-runtime.tar",
        "runtime-manifest.json",
        "build-receipt.json",
    )
    packages = [
        {
            name: {
                "sha256": sha((out / name).read_bytes()),
                "size": (out / name).stat().st_size,
            }
            for name in package_files
        }
        for out in source_outputs
    ]
    require(
        packages[0] == packages[1],
        "independent Git-object and generated-runtime packages differ",
    )
    require(
        packages[0]["latexml-source.tar"]["sha256"]
        == "1330d4e8cc55608eeb49a44fadc89b04699c928c258de954aebdb25904359d76"
        and packages[0]["source-manifest.json"]["sha256"]
        == "4d31f365789d6e66ec02542d34de947b10c14961ad1ba4d7a0ec836d92a47760",
        "original tracked-source provenance mismatch",
    )
    accept_out = scratch / "image-acceptance"
    accepted = command(
        [
            "/usr/bin/python3",
            str(source / "recipe" / "accept_image.py"),
            "--docker",
            "/usr/bin/docker",
            "--package-a",
            str(source_outputs[0]),
            "--package-b",
            str(source_outputs[1]),
            "--base-image-id",
            dependency_id,
            "--architecture",
            arch,
            "--out",
            str(accept_out),
        ],
        log=private / "accept-image.log",
        timeout=900,
    )
    require(accepted.returncode == 0, "source image acceptance failed")
    accepted_data = json.loads((accept_out / "acceptance.json").read_text())
    require(
        accepted_data["generator_tool_sha256"]
        == {"perl_sha256": tool_hashes[0], "recdescent_sha256": tool_hashes[1]},
        "accepted generator tool measurement mismatch",
    )
    image_id = accepted_data["image_id"]
    container_arch = docker_run(
        image_id, ["/usr/bin/uname", "-m"], private / "container-arch.log"
    )
    require(
        container_arch.returncode == 0
        and container_arch.stdout.decode().strip() == arch_machine,
        "container execution is not native",
    )
    helper_check = docker_run(
        image_id,
        [
            "/usr/bin/sha256sum",
            "/usr/local/libexec/latexml/package_source.py",
            "/usr/local/libexec/latexml/verify_install.py",
        ],
        private / "installed-helpers.log",
    )
    require(helper_check.returncode == 0, "installed helper identity unavailable")
    helper_hashes = [
        line.split()[0] for line in helper_check.stdout.decode().splitlines()
    ]
    require(
        helper_hashes == [RECIPE["package_source.py"], RECIPE["verify_install.py"]],
        "installed helper bytes differ",
    )
    generated_check = docker_run(
        image_id,
        [
            "/usr/bin/sha256sum",
            "/opt/latexml-source/lib/LaTeXML/Version.pm",
            "/opt/latexml-source/lib/LaTeXML/MathGrammar.pm",
        ],
        private / "generated-modules.log",
    )
    require(
        generated_check.returncode == 0, "source-bound generated modules unavailable"
    )
    generated_hashes = [
        line.split()[0] for line in generated_check.stdout.decode().splitlines()
    ]
    require(
        generated_hashes
        == [
            "bb1b7ff039b0563eacfd2541a2b8725ec33e46309e508be5491fd8a062304e6e",
            "94018fc222d72df04b2cd1e0bd1bc1f614d733ca6c53a891c98bd77037ccf58e",
        ],
        "generated module identity mismatch",
    )
    loaded_check = docker_run(
        image_id,
        [
            "/usr/bin/perl",
            "-MLaTeXML::Version",
            "-MLaTeXML::MathGrammar",
            "-e",
            "print \"$INC{'LaTeXML/Version.pm'}\n$INC{'LaTeXML/MathGrammar.pm'}\n\"",
        ],
        private / "loaded-modules.log",
    )
    require(
        loaded_check.returncode == 0
        and loaded_check.stdout.decode().splitlines()
        == [
            "/opt/latexml-source/lib/LaTeXML/Version.pm",
            "/opt/latexml-source/lib/LaTeXML/MathGrammar.pm",
        ],
        "loaded modules differ from accepted runtime root",
    )
    parse = docker_run(
        image_id,
        [
            "/bin/sh",
            "-c",
            "set -eu; cd /tmp; printf '%s\n' '\\documentclass{article}' '\\begin{document}Hello world.\\end{document}' > simple.tex; /usr/local/bin/latexml --destination=/tmp/out.xml simple.tex; cat out.xml",
        ],
        private / "positive-parse.log",
        timeout=180,
    )
    require(
        parse.returncode == 0
        and b"Hello world" in parse.stdout
        and b"<" in parse.stdout,
        "source-installed document parse failed",
    )
    math = docker_run(
        image_id,
        ["/usr/local/bin/latexmlmath", "--presentationmathml=-", "x+1"],
        private / "positive-math.log",
        timeout=180,
    )
    require(
        math.returncode == 0 and b"<math" in math.stdout,
        "source-installed math parse failed",
    )
    tex = docker_run(
        image_id,
        ["/usr/local/bin/latexml", "--VERSION"],
        private / "positive-version.log",
    )
    require(tex.returncode == 0, "source-installed command unusable")
    controls = []
    pristine = scratch / "installed-pristine"
    pristine.mkdir()
    source_manifest = json.loads(
        (source_outputs[0] / "source-manifest.json").read_bytes()
    )
    runtime_manifest = json.loads(
        (source_outputs[0] / "runtime-manifest.json").read_bytes()
    )
    inventory = {**source_manifest["files"], **runtime_manifest["generated"]}
    with tarfile.open(source_outputs[0] / "latexml-runtime.tar") as archive:
        seen = set()
        for member in archive:
            require(
                member.isfile()
                and member.name in inventory
                and member.name not in seen
                and not member.name.startswith("/")
                and all(part not in ("", ".", "..") for part in member.name.split("/")),
                "unsafe test-runtime archive",
            )
            data = archive.extractfile(member).read()
            require(
                sha(data) == inventory[member.name]["sha256"]
                and member.mode == inventory[member.name]["mode"],
                "test-runtime archive identity mismatch",
            )
            target = pristine / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(member.mode)
            seen.add(member.name)
        require(seen == set(inventory), "test-runtime archive incomplete")
    for name in ("source-manifest.json", "runtime-manifest.json"):
        shutil.copyfile(source_outputs[0] / name, pristine / name)

    def installed_control(
        case: str,
        root: Path | None = None,
        shadow: bool = False,
        runtime_sha: str | None = None,
        expected_exit: int = 2,
        marker: bytes | None = None,
    ) -> None:
        command_line = [
            "/usr/bin/docker",
            "run",
            "--rm",
            "--user",
            "1000:1000",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "2",
            "--memory",
            "3g",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=768m",
        ]
        if root is not None:
            command_line += ["-v", str(root) + ":/opt/latexml-source:ro"]
        if shadow:
            command_line += ["-e", "PERL5LIB=/tmp"]
        command_line += [
            "--entrypoint",
            "/usr/bin/python3",
            image_id,
            "/usr/local/libexec/latexml/verify_install.py",
            "--root",
            "/opt/latexml-source",
            "--manifest",
            "/opt/latexml-source/source-manifest.json",
            "--manifest-sha256",
            packages[0]["source-manifest.json"]["sha256"],
            "--runtime-manifest",
            "/opt/latexml-source/runtime-manifest.json",
            "--runtime-manifest-sha256",
            runtime_sha or packages[0]["runtime-manifest.json"]["sha256"],
        ]
        result = command(command_line, log=private / (case + ".log"), timeout=180)
        controls.append(
            {
                "case": case,
                "exit": result.returncode,
                "expected_exit": expected_exit,
                "specific_guard_matched": marker is None or marker in result.stderr,
            }
        )
        require(
            result.returncode == expected_exit
            and (marker is None or marker in result.stderr),
            case + " returned unexpected verification result",
        )

    installed_control("pristine_installed_verifier", expected_exit=0)

    def package_control(case: str, mutation: str, marker: bytes) -> None:
        altered = []
        for label, original in (("a", source_outputs[0]), ("b", source_outputs[1])):
            target = scratch / (case + "-" + label)
            shutil.copytree(original, target)
            runtime_path = target / "runtime-manifest.json"
            runtime_data = json.loads(runtime_path.read_bytes())
            if mutation == "false_tools":
                runtime_data["generator"]["perl_sha256"] = "0" * 64
                runtime_data["generator"]["recdescent_sha256"] = "1" * 64
                raw = canonical(runtime_data)
            elif mutation == "float_mode":
                runtime_data["generated"]["lib/LaTeXML/Version.pm"]["mode"] = 420.0
                raw = canonical(runtime_data)
            elif mutation == "duplicate_key":
                raw = runtime_path.read_bytes().replace(
                    b'"schema":', b'"schema":"duplicate","schema":', 1
                )
            elif mutation == "nonfinite_mode":
                raw = runtime_path.read_bytes().replace(b'"mode":420', b'"mode":NaN', 1)
            elif mutation == "extra_schema":
                runtime_data["unexpected"] = "fixture"
                raw = canonical(runtime_data)
            else:
                raise ValueError("unknown package control")
            require(
                raw != runtime_path.read_bytes(),
                "control did not alter runtime manifest",
            )
            runtime_path.write_bytes(raw)
            receipt_path = target / "build-receipt.json"
            receipt_data = json.loads(receipt_path.read_bytes())
            receipt_data["runtime_manifest_sha256"] = sha(raw)
            receipt_path.write_bytes(canonical(receipt_data))
            altered.append(target)
        output = scratch / (case + "-output")
        result = command(
            [
                "/usr/bin/python3",
                str(source / "recipe" / "accept_image.py"),
                "--docker",
                "/usr/bin/docker",
                "--package-a",
                str(altered[0]),
                "--package-b",
                str(altered[1]),
                "--base-image-id",
                dependency_id,
                "--architecture",
                arch,
                "--out",
                str(output),
            ],
            log=private / (case + ".log"),
            timeout=180,
        )
        require(
            result.returncode == 2 and marker in result.stderr and not output.exists(),
            case + " did not refuse before image build",
        )
        controls.append(
            {
                "case": case,
                "exit": result.returncode,
                "expected_exit": 2,
                "specific_guard_matched": True,
                "before_image_build": True,
            }
        )

    package_control(
        "false_generator_tool_provenance",
        "false_tools",
        b"native generator tool bytes mismatch",
    )
    package_control(
        "float_generated_mode_package", "float_mode", b"generated metadata mismatch"
    )
    package_control("duplicate_runtime_key", "duplicate_key", b"duplicate JSON key")
    package_control(
        "nonfinite_generated_mode", "nonfinite_mode", b"generated metadata mismatch"
    )
    package_control(
        "extra_runtime_schema", "extra_schema", b"runtime manifest schema mismatch"
    )
    # Each refusal invokes the unchanged installed/source verifier or package gate.
    bad = scratch / "changed-receipt"
    shutil.copytree(source_outputs[0], bad)
    receipt = json.loads((bad / "build-receipt.json").read_text())
    receipt["file_count"] = 759
    (bad / "build-receipt.json").write_bytes(canonical(receipt))
    refusal = command(
        [
            "/usr/bin/python3",
            str(source / "recipe" / "accept_image.py"),
            "--docker",
            "/usr/bin/docker",
            "--package-a",
            str(bad),
            "--package-b",
            str(source_outputs[1]),
            "--base-image-id",
            dependency_id,
            "--architecture",
            arch,
            "--out",
            str(scratch / "refusal-output"),
        ],
        log=private / "receipt-refusal.log",
        timeout=120,
    )
    controls.append(
        {
            "case": "changed_package_receipt",
            "exit": refusal.returncode,
            "expected_exit": 2,
        }
    )
    require(refusal.returncode == 2, "changed receipt was accepted")
    for field in ("runtime_archive_size", "runtime_file_count"):
        altered = scratch / ("float-" + field)
        shutil.copytree(source_outputs[0], altered)
        altered_receipt = json.loads((altered / "build-receipt.json").read_text())
        altered_receipt[field] = float(altered_receipt[field])
        (altered / "build-receipt.json").write_bytes(canonical(altered_receipt))
        typed_refusal = command(
            [
                "/usr/bin/python3",
                str(source / "recipe" / "accept_image.py"),
                "--docker",
                "/usr/bin/docker",
                "--package-a",
                str(altered),
                "--package-b",
                str(source_outputs[1]),
                "--base-image-id",
                dependency_id,
                "--architecture",
                arch,
                "--out",
                str(scratch / ("float-output-" + field)),
            ],
            log=private / ("float-" + field + ".log"),
            timeout=120,
        )
        controls.append(
            {
                "case": "float_" + field,
                "exit": typed_refusal.returncode,
                "expected_exit": 2,
            }
        )
        require(
            typed_refusal.returncode == 2, "float runtime receipt field was accepted"
        )
    bad_patch = scratch / "changed-adapter.patch"
    bad_patch.write_bytes(
        (source / "recipe" / "siunitx-v3-adapter.patch").read_bytes() + b"\n"
    )
    source_refusal = command(
        [
            "/usr/bin/python3",
            str(source / "recipe" / "package_source.py"),
            "--git",
            "/usr/bin/git",
            "--repo",
            str(upstream_repo),
            "--patch",
            str(bad_patch),
            "--out",
            str(scratch / "source-refusal-output"),
            "--docker",
            "/usr/bin/docker",
            "--generator-image-id",
            dependency_id,
            "--architecture",
            arch,
            "--perl-sha256",
            tool_hashes[0],
            "--recdescent-sha256",
            tool_hashes[1],
        ],
        log=private / "source-refusal.log",
        timeout=120,
    )
    controls.append(
        {
            "case": "changed_adapter_source",
            "exit": source_refusal.returncode,
            "expected_exit": 2,
        }
    )
    require(source_refusal.returncode == 2, "changed adapter was accepted")
    wrong_arch = "amd64" if arch == "arm64" else "arm64"
    architecture_refusal = command(
        [
            "/usr/bin/python3",
            str(source / "recipe" / "accept_image.py"),
            "--docker",
            "/usr/bin/docker",
            "--package-a",
            str(source_outputs[0]),
            "--package-b",
            str(source_outputs[1]),
            "--base-image-id",
            dependency_id,
            "--architecture",
            wrong_arch,
            "--out",
            str(scratch / "arch-refusal-output"),
        ],
        log=private / "architecture-refusal.log",
        timeout=120,
    )
    controls.append(
        {
            "case": "wrong_native_architecture",
            "exit": architecture_refusal.returncode,
            "expected_exit": 2,
        }
    )
    require(
        architecture_refusal.returncode == 2, "wrong native architecture was accepted"
    )
    corrupted = scratch / "changed-apt"
    (corrupted / "lists").mkdir(parents=True)
    (corrupted / "debs").mkdir()
    for member in lock["files"]:
        shutil.copyfile(apt / member["name"], corrupted / member["name"])
    shutil.copyfile(
        apt / "ubuntu-archive-keyring.gpg", corrupted / "ubuntu-archive-keyring.gpg"
    )
    changed_deb = next((corrupted / "debs").glob("*.deb"))
    with changed_deb.open("r+b") as stream:
        first = stream.read(1)
        stream.seek(0)
        stream.write(bytes([first[0] ^ 1]))
    input_refusal_exit = 0
    try:
        verify_apt(corrupted, arch)
    except (ValueError, OSError):
        input_refusal_exit = 2
    controls.append(
        {"case": "changed_apt_deb", "exit": input_refusal_exit, "expected_exit": 2}
    )
    require(input_refusal_exit == 2, "changed APT input was accepted")
    for case, mutation, marker in (
        (
            "float_generated_mode_installed",
            "float_mode",
            b"generated runtime metadata mismatch",
        ),
        (
            "false_generator_tool_provenance_installed",
            "false_tools",
            b"installed generator tool bytes mismatch",
        ),
    ):
        root = scratch / case
        shutil.copytree(pristine, root)
        runtime_path = root / "runtime-manifest.json"
        runtime_data = json.loads(runtime_path.read_bytes())
        if mutation == "float_mode":
            runtime_data["generated"]["lib/LaTeXML/Version.pm"]["mode"] = 420.0
        else:
            runtime_data["generator"]["perl_sha256"] = "0" * 64
            runtime_data["generator"]["recdescent_sha256"] = "1" * 64
        changed_runtime = canonical(runtime_data)
        runtime_path.write_bytes(changed_runtime)
        installed_control(case, root, runtime_sha=sha(changed_runtime), marker=marker)
    for case in (
        "missing_generated",
        "changed_generated",
        "extra_generated",
        "aliased_generated",
    ):
        root = scratch / case
        shutil.copytree(pristine, root)
        module = root / "lib/LaTeXML/Version.pm"
        if case == "missing_generated":
            module.unlink()
        elif case == "changed_generated":
            module.write_bytes(module.read_bytes() + b"\n")
        elif case == "extra_generated":
            (root / "lib/LaTeXML/Unexpected.pm").write_bytes(b"1;\n")
        else:
            module.unlink()
            module.symlink_to("MathGrammar.pm")
        installed_control(case, root)
    installed_control("shadowed_generated", shadow=True)
    bounded = {
        "schema": "aelira-latexml-native-acceptance-v1",
        "source_kind": source_kind,
        "operator_origin": OPERATOR_ORIGIN,
        "operator_ref": operator_ref,
        "operator_tree": operator_tree,
        "remote_ref": remote_ref,
        "source_binding_sha256": sha((source / "binding.json").read_bytes()),
        "runner_sha256": source_binding["runner_sha256"],
        "recipe_sha256": RECIPE,
        "upstream_origin": UPSTREAM_ORIGIN,
        "upstream_commit": UPSTREAM_HEAD,
        "upstream_tree": UPSTREAM_TREE,
        "host_machine": arch_machine,
        "daemon_architecture": daemon,
        "container_machine": arch_machine,
        "ubuntu_base_manifest": UBUNTU_MANIFEST,
        "ubuntu_base_image_id": base_id,
        "ubuntu_archive_keyring_sha256": KEYRING_SHA,
        "apt_input_lock_sha256": input_sha,
        "apt_signed_release_count": lock["signed_releases"],
        "apt_packages_index_count": lock["packages_indexes"],
        "apt_deb_count": len(lock["debs"]),
        "apt_deb_identity": lock["debs"],
        "generated_module_inputs": {
            "Version.in": VERSION_TEMPLATE_SHA,
            "MathGrammar": GRAMMAR_SHA,
        },
        "official_makefile_sha256": MAKEFILE_SHA,
        "runtime_generated_module_sha256": {
            "LaTeXML/Version.pm": generated_hashes[0],
            "LaTeXML/MathGrammar.pm": generated_hashes[1],
        },
        "loaded_generated_module_paths_verified": True,
        "native_generator_tool_sha256": {
            "perl": tool_hashes[0],
            "Parse-RecDescent-module": tool_hashes[1],
        },
        "dependency_image_id": dependency_id,
        "source_image_id": image_id,
        "source_packages": packages,
        "accept_image_exit": accepted.returncode,
        "installed_verify_exit": accepted_data["installed_verify_exit"],
        "installed_helpers_sha256": helper_hashes,
        "source_command_version_exit": tex.returncode,
        "document_parse_exit": parse.returncode,
        "math_parse_exit": math.returncode,
        "refusals": controls,
        "amd64": "not_run" if arch == "arm64" else "executed",
        "full_application": "not_run",
        "final_runtime": "not_run",
        "all_copy_security_advisory": "not_run",
    }
    (scratch / "bounded.json").write_bytes(canonical(bounded))
    print(
        json.dumps(
            {
                "bounded_sha256": sha((scratch / "bounded.json").read_bytes()),
                "architecture": arch,
                "source_image_id": image_id,
            },
            sort_keys=True,
        )
    )


def write_status(scratch: Path, args: argparse.Namespace, run_exit: int) -> None:
    """A failed status is private; status-write failure never replaces the original run."""
    try:
        if scratch.is_dir() and not scratch.is_symlink():
            bounded = scratch / "bounded.json"
            status = {
                "schema": "aelira-latexml-native-status-v1",
                "source_kind": args.source_kind,
                "operator_ref": args.operator_ref,
                "operator_tree": args.operator_tree,
                "architecture": args.architecture,
                "run_exit": run_exit,
                "bounded_sha256": (
                    sha(bounded.read_bytes())
                    if run_exit == 0 and bounded.is_file()
                    else None
                ),
            }
            (scratch / "status.json").write_bytes(canonical(status))
    except OSError:
        pass


def safe_export_tree(item: object) -> None:
    if item is None or type(item) in (bool, int):
        return
    if type(item) is str:
        require(
            len(item) <= 300
            and not any(c in item for c in ("\n", "\r", "\x00"))
            and not any(
                fragment in item
                for fragment in ("/home/", "/Users/", "file://", "token=", "password=")
            ),
            "private or unbounded export text",
        )
        return
    if type(item) is list:
        require(len(item) <= 1000, "unbounded export list")
        for value in item:
            safe_export_tree(value)
        return
    require(
        type(item) is dict and len(item) <= 1000, "export JSON type or mapping bound"
    )
    for key, value in item.items():
        require(type(key) is str, "export JSON key malformed")
        safe_export_tree(key)
        safe_export_tree(value)


CONTROL_CONTRACT = {
    "pristine_installed_verifier": (0, {"specific_guard_matched"}),
    "false_generator_tool_provenance": (
        2,
        {"specific_guard_matched", "before_image_build"},
    ),
    "float_generated_mode_package": (
        2,
        {"specific_guard_matched", "before_image_build"},
    ),
    "duplicate_runtime_key": (2, {"specific_guard_matched", "before_image_build"}),
    "nonfinite_generated_mode": (2, {"specific_guard_matched", "before_image_build"}),
    "extra_runtime_schema": (2, {"specific_guard_matched", "before_image_build"}),
    "changed_package_receipt": (2, set()),
    "float_runtime_archive_size": (2, set()),
    "float_runtime_file_count": (2, set()),
    "changed_adapter_source": (2, set()),
    "wrong_native_architecture": (2, set()),
    "changed_apt_deb": (2, set()),
    "float_generated_mode_installed": (2, {"specific_guard_matched"}),
    "false_generator_tool_provenance_installed": (2, {"specific_guard_matched"}),
    "missing_generated": (2, {"specific_guard_matched"}),
    "changed_generated": (2, {"specific_guard_matched"}),
    "extra_generated": (2, {"specific_guard_matched"}),
    "aliased_generated": (2, {"specific_guard_matched"}),
    "shadowed_generated": (2, {"specific_guard_matched"}),
}
GENERATED_METADATA = {
    "lib/LaTeXML/Version.pm": {
        "mode": 420,
        "size": 2199,
        "sha256": "bb1b7ff039b0563eacfd2541a2b8725ec33e46309e508be5491fd8a062304e6e",
    },
    "lib/LaTeXML/MathGrammar.pm": {
        "mode": 420,
        "size": 2626944,
        "sha256": "94018fc222d72df04b2cd1e0bd1bc1f614d733ca6c53a891c98bd77037ccf58e",
    },
}


def validate_controls(rows: list[dict]) -> None:
    require(
        type(rows) is list and len(rows) == len(CONTROL_CONTRACT),
        "bounded control inventory mismatch",
    )
    seen = set()
    for row in rows:
        require(
            type(row) is dict
            and type(row.get("case")) is str
            and row["case"] in CONTROL_CONTRACT
            and row["case"] not in seen,
            "bounded control inventory mismatch",
        )
        seen.add(row["case"])
        expected, guards = CONTROL_CONTRACT[row["case"]]
        require(
            set(row) == {"case", "exit", "expected_exit"} | guards
            and type(row["exit"]) is int
            and row["exit"] == expected
            and type(row["expected_exit"]) is int
            and row["expected_exit"] == expected
            and all(row[guard] is True for guard in guards),
            "bounded control evidence malformed",
        )
    require(
        seen == set(CONTROL_CONTRACT)
        and rows[0]["case"] == "pristine_installed_verifier",
        "bounded control positive-first inventory mismatch",
    )


def digest_field(value: object, image: bool = False) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}" if image else r"[0-9a-f]{64}", value)
        is not None
    )


def count_field(value: object, expected: int | None = None) -> bool:
    return (
        type(value) is int
        and 0 < value <= 100_000_000
        and (expected is None or value == expected)
    )


def validate_nested(parsed: dict, data: dict, architecture: str) -> None:
    b, a, r, receipt, lock = (
        parsed[n]
        for n in (
            "bounded.json",
            "acceptance.json",
            "runtime-manifest.json",
            "build-receipt.json",
            "input-lock.json",
        )
    )
    require(
        architecture in ("arm64", "amd64")
        and parsed["status.json"]["architecture"] == a["architecture"] == architecture
        and b["amd64"] == ("not_run" if architecture == "arm64" else "executed"),
        "export status architecture mismatch",
    )
    require(
        all(
            digest_field(b[key], True)
            for key in (
                "ubuntu_base_image_id",
                "dependency_image_id",
                "source_image_id",
            )
        )
        and all(
            digest_field(a[key])
            for key in (
                "archive_sha256",
                "manifest_sha256",
                "runtime_archive_sha256",
                "runtime_manifest_sha256",
                "installed_verify_stdout_sha256",
                "installed_verify_stderr_sha256",
            )
        )
        and count_field(a["base_layer_count"])
        and count_field(a["image_layer_count"])
        and a["image_layer_count"] >= a["base_layer_count"],
        "image nested identity malformed",
    )
    tools = b["native_generator_tool_sha256"]
    require(
        type(tools) is dict
        and set(tools) == {"perl", "Parse-RecDescent-module"}
        and all(digest_field(v) for v in tools.values()),
        "native generator tool schema mismatch",
    )
    expected_tools = {
        "perl_sha256": tools["perl"],
        "recdescent_sha256": tools["Parse-RecDescent-module"],
    }
    require(
        type(a["generator_tool_sha256"]) is dict
        and a["generator_tool_sha256"] == expected_tools,
        "acceptance generator tool identity mismatch",
    )
    if architecture == "arm64":
        require(
            tools
            == {
                "perl": PINNED_ARM_PERL_SHA,
                "Parse-RecDescent-module": PINNED_ARM_RECDESCENT_SHA,
            }
            and b["apt_input_lock_sha256"] == PINNED_ARM_LOCK,
            "ARM provenance differs from reviewed inputs",
        )
    require(
        b["installed_helpers_sha256"]
        == [RECIPE["package_source.py"], RECIPE["verify_install.py"]]
        and b["generated_module_inputs"]
        == {"Version.in": VERSION_TEMPLATE_SHA, "MathGrammar": GRAMMAR_SHA}
        and b["runtime_generated_module_sha256"]
        == {
            name.removeprefix("lib/"): meta["sha256"]
            for name, meta in GENERATED_METADATA.items()
        },
        "bounded helper or generated identity mismatch",
    )
    require(
        type(r) is dict
        and set(r)
        == {
            "schema",
            "upstream_origin",
            "upstream_commit",
            "upstream_tree",
            "makefile_sha256",
            "source_manifest_sha256",
            "source_archive_sha256",
            "revision",
            "templates",
            "generator",
            "generated",
            "source_file_count",
            "runtime_file_count",
        }
        and r["schema"] == "aelira-latexml-runtime-v2"
        and r["upstream_origin"] == UPSTREAM_ORIGIN
        and r["upstream_commit"] == UPSTREAM_HEAD
        and r["upstream_tree"] == UPSTREAM_TREE
        and r["makefile_sha256"] == MAKEFILE_SHA
        and r["revision"] == UPSTREAM_HEAD[:7]
        and r["source_manifest_sha256"]
        == sha(data["source-manifest.json"])
        == a["manifest_sha256"]
        and r["source_archive_sha256"] == a["archive_sha256"]
        and r["templates"]
        == {
            "lib/LaTeXML/Version.in": VERSION_TEMPLATE_SHA,
            "lib/LaTeXML/MathGrammar": GRAMMAR_SHA,
        }
        and type(r["generator"]) is dict
        and set(r["generator"])
        == {"image_id", "architecture", "perl_sha256", "recdescent_sha256"}
        and r["generator"]
        == {
            "image_id": b["dependency_image_id"],
            "architecture": architecture,
            **expected_tools,
        }
        and type(r["generated"]) is dict
        and set(r["generated"]) == set(GENERATED_METADATA)
        and count_field(r["source_file_count"], 760)
        and count_field(r["runtime_file_count"], 762),
        "runtime nested schema or identity mismatch",
    )
    for name, expected in GENERATED_METADATA.items():
        meta = r["generated"][name]
        require(
            type(meta) is dict
            and set(meta) == {"mode", "size", "sha256"}
            and type(meta["mode"]) is int
            and count_field(meta["size"])
            and meta == expected,
            "generated metadata export mismatch",
        )
    require(
        receipt["schema"] == "aelira-latexml-package-build-v2"
        and receipt["upstream_commit"] == UPSTREAM_HEAD
        and receipt["upstream_tree"] == UPSTREAM_TREE
        and count_field(receipt["file_count"], 760)
        and count_field(receipt["runtime_file_count"], 762)
        and receipt["archive_sha256"] == a["archive_sha256"]
        and receipt["runtime_archive_sha256"] == a["runtime_archive_sha256"]
        and count_field(
            receipt["archive_size"],
            b["source_packages"][0]["latexml-source.tar"]["size"],
        )
        and count_field(
            receipt["runtime_archive_size"],
            b["source_packages"][0]["latexml-runtime.tar"]["size"],
        ),
        "package receipt nested identity mismatch",
    )
    require(
        set(lock)
        == {
            "schema",
            "ubuntu_base_manifest",
            "archive_keyring_sha256",
            "architecture",
            "signed_releases",
            "packages_indexes",
            "debs",
            "files",
        }
        and lock["schema"] == "aelira-latexml-apt-input-v1"
        and count_field(lock["signed_releases"], 4)
        and count_field(lock["packages_indexes"])
        and count_field(b["apt_signed_release_count"], lock["signed_releases"])
        and count_field(b["apt_packages_index_count"], lock["packages_indexes"])
        and count_field(b["apt_deb_count"], len(lock["debs"]))
        and b["apt_deb_identity"] == lock["debs"],
        "APT nested schema or counts mismatch",
    )
    seen = set()
    for row in lock["debs"]:
        require(
            type(row) is dict
            and set(row) == {"package", "version", "architecture", "sha256", "size"}
            and type(row["package"]) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", row["package"])
            and type(row["version"]) is str
            and re.fullmatch(r"[A-Za-z0-9.+:~_-]+", row["version"])
            and row["architecture"] in (architecture, "all")
            and digest_field(row["sha256"])
            and count_field(row["size"]),
            "APT DEB metadata malformed",
        )
        identity = (row["package"], row["version"], row["architecture"])
        require(identity not in seen, "duplicate APT DEB identity")
        seen.add(identity)
    seen = set()
    for row in lock["files"]:
        require(
            type(row) is dict
            and set(row) == {"name", "sha256", "size"}
            and type(row["name"]) is str
            and re.fullmatch(r"(?:lists|debs)/[A-Za-z0-9+._%:~=-]+", row["name"])
            and row["name"].split("/")[1] not in (".", "..")
            and digest_field(row["sha256"])
            and count_field(row["size"])
            and row["name"] not in seen,
            "APT file metadata malformed",
        )
        seen.add(row["name"])
    require(
        len(lock["files"])
        == lock["signed_releases"] + lock["packages_indexes"] + len(lock["debs"]),
        "APT file inventory count mismatch",
    )


def export(
    scratch: Path,
    output: Path,
    operator_repo: Path,
    operator_ref: str,
    operator_tree: str,
    source_kind: str,
    remote_ref: str | None,
    binding_sha256: str,
    runner_sha256: str,
    architecture: str,
) -> None:
    require(
        scratch.is_absolute() and scratch.is_dir() and scratch.resolve() == scratch,
        "export scratch unavailable or aliased",
    )
    require(
        output.is_absolute() and not output.exists() and output.parent.is_dir(),
        "export output must be new and absolute",
    )
    require(
        type(binding_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", binding_sha256)
        and runner_sha256 == sha(Path(__file__).read_bytes()),
        "external export source digest mismatch",
    )
    source_binding = verify_transport(
        scratch / "source",
        operator_repo,
        operator_ref,
        operator_tree,
        source_kind,
        remote_ref,
    )
    require(
        source_binding["runner_sha256"] == runner_sha256
        and sha((scratch / "source/binding.json").read_bytes()) == binding_sha256,
        "external export binding mismatch",
    )
    if source_kind == "public":
        result = command(
            [
                "/usr/bin/git",
                "-C",
                str(operator_repo),
                "ls-remote",
                "--exit-code",
                "origin",
                remote_ref,
            ],
            timeout=60,
        )
        require(
            result.returncode == 0
            and result.stdout.decode().splitlines()
            == [operator_ref + "\t" + remote_ref],
            "selected public remote ref differs",
        )
    selected = {
        "status.json": scratch / "status.json",
        "bounded.json": scratch / "bounded.json",
        "acceptance.json": scratch / "image-acceptance/acceptance.json",
        "build-receipt.json": scratch / "package-a/build-receipt.json",
        "source-manifest.json": scratch / "package-a/source-manifest.json",
        "runtime-manifest.json": scratch / "package-a/runtime-manifest.json",
        "input-lock.json": scratch / "apt/input-lock.json",
    }
    limits = {
        "bounded.json": 100_000,
        "source-manifest.json": 300_000,
        "input-lock.json": 100_000,
    }
    parsed = {}
    data = {}
    for name, path in selected.items():
        require(
            path.is_file() and not path.is_symlink() and path.resolve() == path,
            "selected export member missing or aliased",
        )
        raw = path.read_bytes()
        require(
            len(raw) <= limits.get(name, 30_000), "selected export member too large"
        )
        item = json.loads(
            raw, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
        require(type(item) is dict, "selected export JSON schema malformed")
        safe_export_tree(item)
        parsed[name] = item
        data[name] = raw
    status = parsed["status.json"]
    bounded = parsed["bounded.json"]
    bounded_keys = {
        "schema",
        "source_kind",
        "operator_origin",
        "operator_ref",
        "operator_tree",
        "remote_ref",
        "source_binding_sha256",
        "runner_sha256",
        "recipe_sha256",
        "upstream_origin",
        "upstream_commit",
        "upstream_tree",
        "host_machine",
        "daemon_architecture",
        "container_machine",
        "ubuntu_base_manifest",
        "ubuntu_base_image_id",
        "ubuntu_archive_keyring_sha256",
        "apt_input_lock_sha256",
        "apt_signed_release_count",
        "apt_packages_index_count",
        "apt_deb_count",
        "apt_deb_identity",
        "generated_module_inputs",
        "official_makefile_sha256",
        "runtime_generated_module_sha256",
        "loaded_generated_module_paths_verified",
        "native_generator_tool_sha256",
        "dependency_image_id",
        "source_image_id",
        "source_packages",
        "accept_image_exit",
        "installed_verify_exit",
        "installed_helpers_sha256",
        "source_command_version_exit",
        "document_parse_exit",
        "math_parse_exit",
        "refusals",
        "amd64",
        "full_application",
        "final_runtime",
        "all_copy_security_advisory",
    }
    require(
        set(bounded) == bounded_keys
        and bounded["recipe_sha256"] == RECIPE
        and bounded["operator_origin"] == OPERATOR_ORIGIN
        and bounded["upstream_origin"] == UPSTREAM_ORIGIN
        and bounded["upstream_commit"] == UPSTREAM_HEAD
        and bounded["upstream_tree"] == UPSTREAM_TREE
        and bounded["ubuntu_base_manifest"] == UBUNTU_MANIFEST
        and bounded["ubuntu_archive_keyring_sha256"] == KEYRING_SHA
        and bounded["official_makefile_sha256"] == MAKEFILE_SHA
        and bounded["source_kind"] in ("public", "synthetic")
        and (
            bounded["remote_ref"] is None
            if bounded["source_kind"] == "synthetic"
            else type(bounded["remote_ref"]) is str
        )
        and all(
            re.fullmatch(r"[0-9a-f]{40}", bounded[k])
            for k in ("operator_ref", "operator_tree")
        )
        and all(
            type(bounded[k]) is int and bounded[k] == 0
            for k in (
                "accept_image_exit",
                "installed_verify_exit",
                "source_command_version_exit",
                "document_parse_exit",
                "math_parse_exit",
            )
        )
        and bounded["loaded_generated_module_paths_verified"] is True
        and bounded["full_application"]
        == bounded["final_runtime"]
        == bounded["all_copy_security_advisory"]
        == "not_run"
        and type(bounded["refusals"]) is list
        and len(bounded["refusals"]) == 19,
        "bounded public evidence schema mismatch",
    )
    validate_controls(bounded["refusals"])
    require(
        all(
            bounded[key] == source_binding[key]
            for key in (
                "source_kind",
                "operator_origin",
                "operator_ref",
                "operator_tree",
                "remote_ref",
                "runner_sha256",
                "recipe_sha256",
            )
        )
        and bounded["source_binding_sha256"] == binding_sha256,
        "export selected source binding mismatch",
    )
    require(
        set(status)
        == {
            "schema",
            "source_kind",
            "operator_ref",
            "operator_tree",
            "architecture",
            "run_exit",
            "bounded_sha256",
        }
        and status["schema"] == "aelira-latexml-native-status-v1"
        and type(status["run_exit"]) is int
        and status["run_exit"] == 0
        and status["bounded_sha256"] == sha(data["bounded.json"]),
        "successful status binding mismatch",
    )
    require(
        bounded.get("schema") == "aelira-latexml-native-acceptance-v1"
        and bounded.get("source_kind") == status["source_kind"]
        and bounded.get("operator_ref") == status["operator_ref"]
        and bounded.get("operator_tree") == status["operator_tree"]
        and bounded.get("source_packages") is not None
        and type(bounded["source_packages"]) is list
        and len(bounded["source_packages"]) == 2
        and bounded["source_packages"][0] == bounded["source_packages"][1],
        "bounded source acceptance mismatch",
    )
    acceptance = parsed["acceptance.json"]
    require(
        set(acceptance)
        == {
            "architecture",
            "archive_sha256",
            "base_image_id",
            "base_layer_count",
            "base_layer_prefix_match",
            "build_exit",
            "generator_tool_sha256",
            "image_id",
            "image_layer_count",
            "installed_verify_exit",
            "installed_verify_stderr_sha256",
            "installed_verify_stdout_sha256",
            "manifest_sha256",
            "runtime_archive_sha256",
            "runtime_manifest_sha256",
            "schema",
            "source_builds_equal",
        }
        and acceptance["schema"] == "aelira-latexml-immutable-image-v2"
        and acceptance["source_builds_equal"] is True
        and acceptance["base_layer_prefix_match"] is True
        and type(acceptance["build_exit"]) is int
        and acceptance["build_exit"] == 0,
        "image acceptance evidence malformed",
    )
    require(
        acceptance.get("image_id") == bounded.get("source_image_id")
        and acceptance.get("base_image_id") == bounded.get("dependency_image_id")
        and acceptance.get("architecture") in ("amd64", "arm64")
        and bounded["host_machine"] == bounded["container_machine"]
        and bounded["host_machine"]
        == ("aarch64" if acceptance["architecture"] == "arm64" else "x86_64")
        and bounded["daemon_architecture"]
        in (bounded["host_machine"], acceptance["architecture"])
        and type(acceptance.get("installed_verify_exit")) is int
        and acceptance["installed_verify_exit"] == 0,
        "export image acceptance mismatch",
    )
    hashes = bounded["source_packages"][0]
    require(
        set(hashes)
        == {
            "latexml-source.tar",
            "source-manifest.json",
            "latexml-runtime.tar",
            "runtime-manifest.json",
            "build-receipt.json",
        }
        and sha(data["source-manifest.json"])
        == "4d31f365789d6e66ec02542d34de947b10c14961ad1ba4d7a0ec836d92a47760",
        "package source provenance mismatch",
    )
    require(
        all(
            type(meta) is dict
            and set(meta) == {"sha256", "size"}
            and type(meta["sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])
            and type(meta["size"]) is int
            and 0 < meta["size"] <= 20_000_000
            for meta in hashes.values()
        )
        and hashes["latexml-source.tar"]["sha256"] == acceptance["archive_sha256"]
        and hashes["latexml-runtime.tar"]["sha256"]
        == acceptance["runtime_archive_sha256"]
        and hashes["runtime-manifest.json"]["sha256"]
        == acceptance["runtime_manifest_sha256"],
        "package archive identity mismatch",
    )
    for name in ("build-receipt.json", "source-manifest.json", "runtime-manifest.json"):
        require(
            type(hashes.get(name)) is dict
            and hashes[name].get("sha256") == sha(data[name])
            and type(hashes[name].get("size")) is int
            and hashes[name]["size"] == len(data[name]),
            "export package evidence mismatch",
        )
    require(
        bounded.get("apt_input_lock_sha256") == sha(data["input-lock.json"])
        and parsed["input-lock.json"].get("architecture") == acceptance["architecture"]
        and parsed["input-lock.json"].get("ubuntu_base_manifest") == UBUNTU_MANIFEST
        and parsed["input-lock.json"].get("archive_keyring_sha256") == KEYRING_SHA
        and type(parsed["input-lock.json"].get("files")) is list
        and type(parsed["input-lock.json"].get("debs")) is list
        and len(parsed["input-lock.json"]["debs"]) == bounded["apt_deb_count"],
        "export input lock mismatch",
    )
    require(
        set(parsed["build-receipt.json"])
        == {
            "archive_sha256",
            "archive_size",
            "file_count",
            "manifest_sha256",
            "runtime_archive_sha256",
            "runtime_archive_size",
            "runtime_file_count",
            "runtime_manifest_sha256",
            "schema",
            "upstream_commit",
            "upstream_tree",
        }
        and parsed["build-receipt.json"]["manifest_sha256"]
        == sha(data["source-manifest.json"])
        and parsed["build-receipt.json"]["runtime_manifest_sha256"]
        == sha(data["runtime-manifest.json"])
        and parsed["build-receipt.json"]["file_count"] == 760
        and parsed["build-receipt.json"]["runtime_file_count"] == 762
        and parsed["runtime-manifest.json"].get("generated") is not None
        and type(parsed["runtime-manifest.json"]["generated"]) is dict
        and set(parsed["runtime-manifest.json"]["generated"])
        == {"lib/LaTeXML/Version.pm", "lib/LaTeXML/MathGrammar.pm"}
        and parsed["runtime-manifest.json"].get("source_file_count") == 760
        and parsed["runtime-manifest.json"].get("runtime_file_count") == 762,
        "runtime membership export mismatch",
    )
    validate_nested(parsed, data, architecture)
    output.mkdir()
    manifest_files = []
    for name in sorted(data):
        (output / name).write_bytes(data[name])
        manifest_files.append(
            {"path": name, "sha256": sha(data[name]), "size": len(data[name])}
        )
    manifest = {
        "schema": "aelira-latexml-native-public-evidence-v1",
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
    (output / "manifest.json").write_bytes(canonical(manifest))
    require(
        {p.name for p in output.iterdir()} == set(data) | {"manifest.json"},
        "export selected member closure mismatch",
    )
    print(
        json.dumps(
            {
                "manifest_sha256": sha((output / "manifest.json").read_bytes()),
                "file_count": len(manifest_files),
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "export"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--upstream-repo", type=Path)
    parser.add_argument("--apt-input", type=Path)
    parser.add_argument("--apt-lock-sha256")
    parser.add_argument("--operator-repo", type=Path)
    parser.add_argument("--operator-ref")
    parser.add_argument("--operator-tree")
    parser.add_argument("--source-kind", choices=("public", "synthetic"))
    parser.add_argument("--remote-ref")
    parser.add_argument("--architecture", choices=("amd64", "arm64"))
    parser.add_argument("--binding-sha256")
    parser.add_argument("--runner-sha256")
    args = parser.parse_args()
    require(args.directory.is_absolute(), "directory must be absolute")
    if args.action == "prepare":
        require(
            args.out is None
            and args.upstream_repo is None
            and args.apt_input is None
            and args.apt_lock_sha256 is None
            and args.architecture is None
            and args.operator_repo is not None
            and args.operator_ref is not None
            and args.operator_tree is not None
            and args.source_kind is not None,
            "prepare requires explicit selected operator Git source",
        )
        prepare(
            args.directory,
            args.operator_repo,
            args.operator_ref,
            args.operator_tree,
            args.source_kind,
            args.remote_ref,
        )
    elif args.action == "run":
        require(
            args.out is None
            and args.upstream_repo is not None
            and args.apt_input is not None
            and args.apt_lock_sha256 is not None
            and args.architecture is not None
            and args.operator_repo is not None
            and args.operator_ref is not None
            and args.operator_tree is not None
            and args.source_kind is not None,
            "run requires explicit native input and selected Git source",
        )
        error = None
        try:
            run(
                args.directory,
                args.upstream_repo,
                args.apt_input,
                args.apt_lock_sha256,
                args.operator_repo,
                args.operator_ref,
                args.operator_tree,
                args.source_kind,
                args.remote_ref,
                args.architecture,
            )
        except (
            ValueError,
            OSError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            error = exc
        finally:
            write_status(args.directory, args, 0 if error is None else 2)
        if error is not None:
            raise error
    else:
        require(
            args.out is not None
            and args.operator_repo is not None
            and args.operator_ref is not None
            and args.operator_tree is not None
            and args.source_kind is not None
            and args.binding_sha256 is not None
            and args.runner_sha256 is not None
            and args.architecture is not None
            and args.upstream_repo is None
            and args.apt_input is None
            and args.apt_lock_sha256 is None,
            "export requires independently trusted selected source and architecture",
        )
        export(
            args.directory,
            args.out,
            args.operator_repo,
            args.operator_ref,
            args.operator_tree,
            args.source_kind,
            args.remote_ref,
            args.binding_sha256,
            args.runner_sha256,
            args.architecture,
        )


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ):
        print("native acceptance refused", file=sys.stderr)
        sys.exit(2)
