"""Trusted public-source/archive validation shared by exporter and native bootstrap.

The native operator authenticates this file separately before importing it.
No code from a transferred context is executed during validation.
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

RECIPE = frozenset(
    {
        "Dockerfile",
        "pins.txt",
        "verify.py",
        "verify-context.py",
        "integrity.py",
        "system-packages.txt",
        "source.json",
        "source-inventory.json",
    }
)
REQUIRED_SOURCE = frozenset(
    {
        "src/education/remediation/pdf_remediator.py",
        "src/education/handwritten_math_suitability.py",
    }
)
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PIN = re.compile(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s;]+)\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_hash(kind: str, data: bytes) -> str:
    return hashlib.sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def strict_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("nonstandard JSON constant")

    return json.loads(
        data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject
    )


def pins(data: bytes, *, required: bool = True) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in data.decode("utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN.fullmatch(line)
        if match is None:
            raise ValueError("invalid exact requirement")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in result:
            raise ValueError("duplicate normalized requirement")
        result[name] = match[2]
    if required and not result:
        raise ValueError("empty exact requirements")
    return result


def tree_sha1(inventory: list[dict[str, str]]) -> str:
    root: dict[str, object] = {}
    for item in inventory:
        parts = item["path"].split("/")[1:]
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})  # type: ignore[assignment]
            if not isinstance(node, dict):
                raise ValueError("file/directory collision")
        if parts[-1] in node:
            raise ValueError("duplicate tree path")
        node[parts[-1]] = (item["mode"], item["blob_sha1"])

    def encode(node: dict[str, object]) -> str:
        parts: list[bytes] = []
        for name, value in sorted(
            node.items(),
            key=lambda pair: (
                pair[0] + ("/" if isinstance(pair[1], dict) else "")
            ).encode(),
        ):
            if isinstance(value, dict):
                mode, digest = "40000", encode(value)
            else:
                mode, digest = value
            parts.append(
                mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(digest)
            )
        return git_hash("tree", b"".join(parts))

    return encode(root)


def validate_members(
    members: dict[str, bytes],
    *,
    origin: str,
    commit: str,
    requirements_blob: str,
    src_tree: str,
) -> dict[str, object]:
    if not all(
        HEX40.fullmatch(value) for value in (commit, requirements_blob, src_tree)
    ):
        raise ValueError("invalid trusted Git object identity")
    if (
        not RECIPE <= members.keys()
        or "requirements.txt" not in members
        or "context.sha256" not in members
    ):
        raise ValueError("required context member missing")
    manifest: dict[str, str] = {}
    for line in members["context.sha256"].decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if separator != "  " or not HEX64.fullmatch(digest) or name in manifest:
            raise ValueError("invalid or duplicate manifest entry")
        manifest[name] = digest
    if set(manifest) != set(members) - {"context.sha256"}:
        raise ValueError("manifest inventory mismatch")
    for name, digest in manifest.items():
        if sha256(members[name]) != digest:
            raise ValueError("context member digest mismatch")
    identity = strict_json(members["source.json"])
    if type(identity) is not dict or set(identity) != {
        "origin",
        "commit",
        "requirements_blob",
        "src_tree",
    }:
        raise ValueError("source identity schema")
    expected = {
        "origin": origin,
        "commit": commit,
        "requirements_blob": requirements_blob,
        "src_tree": src_tree,
    }
    if (
        any(type(value) is not str for value in identity.values())
        or identity != expected
    ):
        raise ValueError("source identity does not match external trust anchor")
    if git_hash("blob", members["requirements.txt"]) != requirements_blob:
        raise ValueError("requirements Git blob mismatch")
    inventory = strict_json(members["source-inventory.json"])
    if type(inventory) is not list or not inventory:
        raise ValueError("source inventory schema")
    seen: set[str] = set()
    for item in inventory:
        if type(item) is not dict or set(item) != {
            "path",
            "mode",
            "blob_sha1",
            "sha256",
        }:
            raise ValueError("source inventory item schema")
        path, mode, blob, digest = (
            item[key] for key in ("path", "mode", "blob_sha1", "sha256")
        )
        if (
            type(path) is not str
            or type(mode) is not str
            or type(blob) is not str
            or type(digest) is not str
        ):
            raise ValueError("source inventory item type")
        if (
            not path.startswith("src/")
            or not canonical(path)
            or path in seen
            or mode not in {"100644", "100755"}
        ):
            raise ValueError("source inventory path/mode")
        if not HEX40.fullmatch(blob) or not HEX64.fullmatch(digest):
            raise ValueError("source inventory digest")
        if (
            path not in members
            or sha256(members[path]) != digest
            or git_hash("blob", members[path]) != blob
        ):
            raise ValueError("source inventory bytes mismatch")
        seen.add(path)
    if (
        seen != {name for name in members if name.startswith("src/")}
        or not REQUIRED_SOURCE <= seen
    ):
        raise ValueError("source tree inventory incomplete")
    if set(members) != RECIPE | {"requirements.txt", "context.sha256"} | seen:
        raise ValueError("context contains an unrelated member")
    if tree_sha1(inventory) != src_tree:
        raise ValueError("complete Git source tree mismatch")
    public = pins(members["requirements.txt"])
    selected = pins(members["pins.txt"])
    if len(selected) < 2 or any(
        public.get(name) != version for name, version in selected.items()
    ):
        raise ValueError("selected pins differ from public requirements")
    if (
        public.get("pillow") != "13.0.0"
        or public.get("psycopg2-binary") != "2.9.13"
        or selected.get("sqlalchemy") != "2.1.5"
    ):
        raise ValueError("required baseline pins missing")
    system = members["system-packages.txt"].decode("ascii").splitlines()
    if (
        not system
        or len(system) != len(set(system))
        or any(not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name) for name in system)
    ):
        raise ValueError("invalid system package list")
    return {
        "files": len(members),
        "src_files": len(inventory),
        "selected_pins": len(selected),
        "context_manifest_sha256": sha256(members["context.sha256"]),
        "requirements_blob": requirements_blob,
        "src_tree": src_tree,
    }


def canonical(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith("/")
        and not name.startswith("./")
        and not name.endswith("/")
        and all(part not in ("", ".", "..") for part in name.split("/"))
        and str(path) == name
    )


def read_archive(path: Path, expected_sha256: str) -> dict[str, bytes]:
    if (
        not HEX64.fullmatch(expected_sha256)
        or sha256(path.read_bytes()) != expected_sha256
    ):
        raise ValueError("archive digest differs from external trust anchor")
    members: dict[str, bytes] = {}
    with tarfile.open(path, "r:") as archive:
        for entry in archive:
            if not entry.isfile() or not entry.name.startswith("./"):
                raise ValueError("archive member type or canonical prefix")
            name = entry.name[2:]
            if not canonical(name) or name in members:
                raise ValueError("archive alias, traversal, or duplicate")
            stream = archive.extractfile(entry)
            if stream is None:
                raise ValueError("archive member unreadable")
            members[name] = stream.read()
    return members
