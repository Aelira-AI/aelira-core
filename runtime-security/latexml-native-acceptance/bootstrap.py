#!/usr/bin/env python3
"""Validate a bound LaTeXML transport before extracting any executable member."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import subprocess
import tarfile

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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def nonfinite(_: str) -> None:
    raise ValueError("nonfinite JSON number")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--transport", required=True, type=Path)
    p.add_argument("--transport-sha256", required=True)
    p.add_argument("--binding-sha256", required=True)
    p.add_argument("--runner-sha256", required=True)
    p.add_argument("--operator-ref", required=True)
    p.add_argument("--operator-tree", required=True)
    p.add_argument("--source-kind", required=True, choices=("public", "synthetic"))
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--operator-repo", required=True, type=Path)
    p.add_argument("--remote-ref")
    a = p.parse_args()
    for digest in (a.transport_sha256, a.binding_sha256, a.runner_sha256):
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("external digest malformed")
    for oid in (a.operator_ref, a.operator_tree):
        if re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise ValueError("external Git object identifier malformed")
    if not (
        a.transport.is_absolute()
        and a.transport.is_file()
        and not a.transport.is_symlink()
        and a.out.is_absolute()
        and not a.out.exists()
        and a.out.parent.is_dir()
    ):
        raise ValueError("bootstrap path unavailable or aliased")
    if sha(a.transport.read_bytes()) != a.transport_sha256:
        raise ValueError("transport digest mismatch")
    repo = a.operator_repo
    if not (
        repo.is_absolute()
        and repo.is_dir()
        and not repo.is_symlink()
        and repo.resolve() == repo
    ):
        raise ValueError("trusted operator repository unavailable or aliased")

    def git(*args: str) -> bytes:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), *args],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("trusted selected Git lookup failed")
        return result.stdout

    if (
        git("remote", "get-url", "origin").decode().strip()
        != "https://github.com/Aelira-AI/aelira-core.git"
        or git("rev-parse", a.operator_ref + "^{commit}").decode().strip()
        != a.operator_ref
        or git("rev-parse", a.operator_ref + "^{tree}").decode().strip()
        != a.operator_tree
    ):
        raise ValueError("trusted selected Git identity mismatch")
    if a.source_kind == "synthetic":
        if a.remote_ref is not None:
            raise ValueError("synthetic source cannot claim public remote ref")
    elif (
        a.remote_ref is None
        or re.fullmatch(r"refs/(?:heads|tags|pull)/[A-Za-z0-9_./-]+", a.remote_ref)
        is None
        or git("ls-remote", "--exit-code", "origin", a.remote_ref).decode().splitlines()
        != [a.operator_ref + "\t" + a.remote_ref]
    ):
        raise ValueError("trusted public remote ref mismatch")

    def blob(name: str) -> bytes:
        raw = git("ls-tree", "-z", a.operator_ref, "--", name).split(b"\0")
        if len(raw) != 2 or raw[1] != b"":
            raise ValueError("trusted Git member closure mismatch")
        header, sep, path = raw[0].partition(b"\t")
        parts = header.split()
        if (
            sep != b"\t"
            or path.decode() != name
            or len(parts) != 3
            or parts[0] not in (b"100644", b"100755")
            or parts[1] != b"blob"
        ):
            raise ValueError("trusted Git member not regular blob")
        return git("cat-file", "blob", parts[2].decode())

    if (
        blob("runtime-security/latexml-native-acceptance/bootstrap.py")
        != Path(__file__).read_bytes()
        or sha(blob("runtime-security/latexml-native-acceptance/native_acceptance.py"))
        != a.runner_sha256
    ):
        raise ValueError("trusted bootstrap or runner bytes mismatch")
    expected = {"recipe/" + name for name in RECIPE} | {
        "native_acceptance.py",
        "README.md",
        "binding.json",
    }
    members: dict[str, bytes] = {}
    with tarfile.open(a.transport, "r:") as archive:
        for member in archive:
            if (
                not member.isfile()
                or member.name not in expected
                or member.name in members
                or member.mode != 0o644
                or member.uid != 0
                or member.gid != 0
                or member.mtime != 0
                or member.size > 300_000
            ):
                raise ValueError("transport member invalid")
            file = archive.extractfile(member)
            if file is None:
                raise ValueError("transport member unavailable")
            data = file.read()
            if len(data) != member.size:
                raise ValueError("transport member size mismatch")
            members[member.name] = data
    if set(members) != expected or len(members) != 13:
        raise ValueError("transport member closure mismatch")
    if (
        sha(members["binding.json"]) != a.binding_sha256
        or sha(members["native_acceptance.py"]) != a.runner_sha256
    ):
        raise ValueError("external source identity mismatch")
    binding = json.loads(
        members["binding.json"], object_pairs_hook=unique, parse_constant=nonfinite
    )
    if (
        type(binding) is not dict
        or set(binding)
        != {
            "schema",
            "source_kind",
            "operator_origin",
            "operator_ref",
            "operator_tree",
            "remote_ref",
            "upstream_origin",
            "upstream_ref",
            "upstream_tree",
            "recipe_sha256",
            "runner_sha256",
        }
        or binding["schema"] != "aelira-latexml-native-source-binding-v1"
        or binding["source_kind"] != a.source_kind
        or binding["operator_origin"] != "https://github.com/Aelira-AI/aelira-core.git"
        or binding["operator_ref"] != a.operator_ref
        or binding["operator_tree"] != a.operator_tree
        or binding["runner_sha256"] != a.runner_sha256
        or binding["recipe_sha256"] != RECIPE
        or binding["upstream_origin"] != "https://github.com/brucemiller/LaTeXML.git"
        or binding["upstream_ref"] != "0db6f47ea859391f9099e6e25e7405afad8467bf"
        or binding["upstream_tree"] != "2693c0c65c86f6b36370af18496d8283cf4f73ea"
        or (a.source_kind == "synthetic" and binding["remote_ref"] is not None)
        or binding["remote_ref"] != a.remote_ref
    ):
        raise ValueError("source binding mismatch")
    for name, digest in RECIPE.items():
        if sha(members["recipe/" + name]) != digest:
            raise ValueError("accepted recipe member mismatch")
    for name in members:
        if name == "binding.json":
            continue
        selected_name = (
            "runtime-security/latexml/" + name.removeprefix("recipe/")
            if name.startswith("recipe/")
            else "runtime-security/latexml-native-acceptance/" + name
        )
        if blob(selected_name) != members[name]:
            raise ValueError("archive bytes differ from trusted selected Git object")
    a.out.mkdir()
    (a.out / "recipe").mkdir()
    for name, data in members.items():
        target = a.out / name
        target.write_bytes(data)
        target.chmod(0o644)
    if {
        x.relative_to(a.out).as_posix() for x in a.out.rglob("*") if x.is_file()
    } != expected:
        raise ValueError("extracted member closure mismatch")
    print(
        json.dumps(
            {
                "schema": "aelira-latexml-native-bootstrap-v1",
                "member_count": 13,
                "transport_sha256": a.transport_sha256,
                "binding_sha256": a.binding_sha256,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        tarfile.TarError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ):
        print("native bootstrap refused", file=sys.stderr)
        sys.exit(2)
