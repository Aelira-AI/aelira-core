"""Export an exact public source context from an explicitly verified repository/ref."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

from integrity import RECIPE, git_hash, sha256, validate_members


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-ref", required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.absolute()
    output = args.out.absolute()
    if (
        not args.repo.is_absolute()
        or not args.out.is_absolute()
        or not repo.is_dir()
        or repo.is_symlink()
        or repo.resolve() != repo
    ):
        raise ValueError("source path invalid")
    parent = output.parent.resolve(strict=True)
    archive_path = output.with_suffix(".tar")
    if (
        archive_path == output
        or parent != output.parent
        or not parent.is_dir()
        or parent.stat().st_uid != os.getuid()
        or output.resolve() != output
        or output.exists()
        or output.is_symlink()
        or archive_path.exists()
        or archive_path.is_symlink()
    ):
        raise ValueError("source or output path invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_ref):
        raise ValueError("expected ref must be an exact commit")
    if git(repo, "rev-parse", "--show-toplevel").decode().strip() != str(repo):
        raise ValueError("not repository root")
    if (
        git(repo, "remote", "get-url", "origin").decode().strip()
        != args.expected_origin
    ):
        raise ValueError("public origin mismatch")
    commit = (
        git(repo, "rev-parse", "--verify", args.expected_ref + "^{commit}")
        .decode()
        .strip()
    )
    if (
        commit != args.expected_ref
        or git(repo, "rev-parse", "HEAD").decode().strip() != commit
    ):
        raise ValueError("HEAD differs from expected public commit")
    if subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repo),
            "diff",
            "--quiet",
            commit,
            "--",
            "requirements.txt",
            "src",
        ]
    ).returncode:
        raise ValueError("public source differs from expected commit")
    requirements_blob = (
        git(repo, "rev-parse", commit + ":requirements.txt").decode().strip()
    )
    src_tree = git(repo, "rev-parse", commit + ":src").decode().strip()
    members: dict[str, bytes] = {}
    with tarfile.open(
        fileobj=io.BytesIO(
            git(repo, "archive", "--format=tar", commit, "requirements.txt", "src")
        )
    ) as archive:
        for entry in archive:
            if entry.isdir():
                continue
            if not entry.isfile() or entry.name in members:
                raise ValueError("unsupported public source member")
            stream = archive.extractfile(entry)
            assert stream is not None
            members[entry.name] = stream.read()
    inventory = []
    for line in git(repo, "ls-tree", "-r", "-z", commit, "--", "src").split(b"\0"):
        if not line:
            continue
        prefix, path_bytes = line.split(b"\t", 1)
        mode, kind, blob = prefix.decode().split(" ")
        path = path_bytes.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"} or path not in members:
            raise ValueError("unsupported Git source object")
        data = members[path]
        if git_hash("blob", data) != blob:
            raise ValueError("source Git blob differs from archive")
        inventory.append(
            {"path": path, "mode": mode, "blob_sha1": blob, "sha256": sha256(data)}
        )
    inventory.sort(key=lambda item: item["path"])
    recipe = Path(__file__).resolve().parent
    for name in sorted(RECIPE - {"source.json", "source-inventory.json"}):
        members[name] = (recipe / name).read_bytes()
    members["source.json"] = (
        json.dumps(
            {
                "origin": args.expected_origin,
                "commit": commit,
                "requirements_blob": requirements_blob,
                "src_tree": src_tree,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    members["source-inventory.json"] = (
        json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    members["context.sha256"] = "".join(
        f"{sha256(data)}  {name}\n" for name, data in sorted(members.items())
    ).encode()
    result = validate_members(
        members,
        origin=args.expected_origin,
        commit=commit,
        requirements_blob=requirements_blob,
        src_tree=src_tree,
    )
    output.mkdir(mode=0o700)
    for name, data in members.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    with tarfile.open(archive_path, "w", format=tarfile.GNU_FORMAT) as archive:
        for name, data in sorted(members.items()):
            entry = tarfile.TarInfo("./" + name)
            entry.size = len(data)
            entry.mode = 0o644
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(data))
    print(
        json.dumps(
            {
                **result,
                "archive_sha256": sha256(archive_path.read_bytes()),
                "commit": commit,
                "origin": args.expected_origin,
                "native_bootstrap_sha256": sha256((recipe / "native.py").read_bytes()),
                "external_integrity_sha256": sha256(
                    (recipe / "integrity.py").read_bytes()
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
