#!/usr/bin/env python3
"""Exact allowlist exporter and authenticated input/package replay checker."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import run as common

PENDING = [
    "public_ci",
    "native_amd64",
    "full_application",
    "final_image",
    "all_copy",
    "advisory",
    "R10",
    "#600",
    "#579",
    "#578",
]


def outer(path, a, members):
    row = common.read_json(path)
    common.require(
        type(row) is dict
        and set(row)
        == {
            "schema",
            *common.identity(a),
            "stages",
            "captured_lock_sha256",
            "selected_sources",
            "producer_status_sha256",
            "pending",
        },
        "outer schema",
    )
    common.require(
        row["schema"] == "aelira-latexml-ci-outer-v2"
        and all(row[k] == v for k, v in common.identity(a).items())
        and row["pending"] == PENDING,
        "outer identities",
    )
    common.validate_stages(row["stages"], successful=True)
    expected = {
        n: {"sha256": common.sha(d), "size": len(d), "mode": 420}
        for n, d in members.items()
    }
    common.require(row["selected_sources"] == expected, "outer selected sources")
    common.require(
        row["captured_lock_sha256"] is None
        or (
            type(row["captured_lock_sha256"]) is str
            and common.re.fullmatch("[0-9a-f]{64}", row["captured_lock_sha256"])
        ),
        "captured lock digest",
    )
    common.require(
        type(row["producer_status_sha256"]) is str
        and common.re.fullmatch("[0-9a-f]{64}", row["producer_status_sha256"]),
        "producer status digest",
    )
    return row


def allowed(lock, members):
    common.require(
        type(lock) is dict
        and type(lock.get("files")) is list
        and 1 <= len(lock["files"]) <= 1000,
        "input lock bounds",
    )
    inputs = {"apt/input-lock.json", "apt/ubuntu-archive-keyring.gpg"}
    seen = set()
    for item in lock["files"]:
        common.require(
            type(item) is dict and set(item) == {"name", "sha256", "size"},
            "APT metadata fields",
        )
        name = item["name"]
        common.require(
            type(name) is str
            and common.re.fullmatch(r"(?:lists|debs)/[A-Za-z0-9+._%:~=-]+", name)
            and name.split("/")[1] not in (".", "..")
            and name not in seen,
            "APT relative name",
        )
        common.require(
            type(item["size"]) is int
            and 0 < item["size"] <= common.MAX_FILE
            and type(item["sha256"]) is str
            and common.re.fullmatch("[0-9a-f]{64}", item["sha256"]),
            "APT typed metadata",
        )
        seen.add(name)
        inputs.add("apt/" + name)
    return (
        inputs
        | {"source/" + n for n in members}
        | {
            "package-" + label + "/" + n
            for label in ("a", "b")
            for n in common.PACKAGE_FILES
        }
        | {"caller-export/" + n for n in common.JSON_FILES}
        | {"binding.json", "outer.json"}
    )


def copy_exact(source, dest, files, expected=None):
    before = common.inventory(source, files)
    if expected is not None:
        common.require(before == expected, "copy source manifest differs")
    common.path_check(dest, new=True)
    dest.mkdir()
    for n in sorted(files):
        target = dest / n
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / n).read_bytes())
        target.chmod(before[n]["mode"])
    common.require(
        common.inventory(source, files) == before
        and common.inventory(dest, files) == before,
        "postcopy identity differs",
    )
    return before


def replay(bundle, a, members, verification):
    common.path_check(verification, new=True)
    verification.mkdir()
    files = allowed(common.read_json(bundle / "apt/input-lock.json"), members)
    original = common.inventory(bundle, files)
    for n, data in members.items():
        common.require(
            (bundle / "source" / n).read_bytes() == data, "evidence Git source differs"
        )
    row = outer(bundle / "outer.json", a, members)
    common.require(
        row["producer_status_sha256"]
        == original["caller-export/status.json"]["sha256"],
        "outer status binding",
    )
    lock = common.read_json(bundle / "apt/input-lock.json")
    common.require(
        lock.get("architecture") == a.architecture
        and lock.get("archive_keyring_sha256") == common.KEYRING
        and lock.get("ubuntu_base_manifest") == common.BASE.split("@")[1],
        "native authenticated input identity",
    )
    if a.architecture == "arm64":
        common.require(
            original["apt/input-lock.json"]["sha256"] == common.ARM_LOCK,
            "reviewed ARM lock required",
        )
    for item in lock["files"]:
        meta = original["apt/" + item["name"]]
        common.require(
            meta["sha256"] == item["sha256"] and meta["size"] == item["size"],
            "APT byte metadata",
        )
    common.require(
        original["apt/ubuntu-archive-keyring.gpg"]["sha256"] == common.KEYRING,
        "independent keyring pin",
    )
    apt = verification / "apt"
    aptfiles = {n.removeprefix("apt/") for n in files if n.startswith("apt/")}
    copy_exact(bundle / "apt", apt, aptfiles)
    # Load only exact selected Git bytes after the complete closure/identity checks.
    producer = verification / "producer"
    (producer / "source/recipe").mkdir(parents=True)
    for n in members:
        if n.startswith("runtime-security/latexml/"):
            dest = producer / "source/recipe" / Path(n).name
        elif n in (common.CALLER + "native_acceptance.py", common.CALLER + "README.md"):
            dest = producer / "source" / Path(n).name
        else:
            continue
        dest.write_bytes(members[n])
        dest.chmod(0o644)
    shutil.copyfile(bundle / "binding.json", producer / "source/binding.json")
    caller = common.load_module(
        producer / "source/native_acceptance.py", "bounded_evidence_caller"
    )
    signed = caller.verify_apt(apt, a.architecture)
    common.require(
        common.sha((apt / "input-lock.json").read_bytes())
        == original["apt/input-lock.json"]["sha256"]
        and signed == lock,
        "signed native input replay differs",
    )
    sys.path.insert(0, str(producer / "source/recipe"))
    helper = common.load_module(
        producer / "source/recipe/accept_image.py", "bounded_evidence_package"
    )
    packages = []
    for label in ("a", "b"):
        out = producer / ("package-" + label)
        copy_exact(bundle / ("package-" + label), out, common.PACKAGE_FILES)
        package = helper.read_package(out)
        # Exact safe source AND generated runtime tar inventories; extraction is private.
        helper.extract_verified(
            out / "latexml-source.tar", verification / ("source-" + label), package[2]
        )
        helper.extract_verified(
            out / "latexml-runtime.tar",
            verification / ("runtime-" + label),
            package[2],
            package[5],
        )
        packages.append({n: common.regular(out / n) for n in common.PACKAGE_FILES})
    common.require(packages[0] == packages[1], "independent package pairs differ")
    exported = bundle / "caller-export"
    common.inventory(exported, common.JSON_FILES)
    for n, target in (
        ("status.json", producer / "status.json"),
        ("bounded.json", producer / "bounded.json"),
        ("acceptance.json", producer / "image-acceptance/acceptance.json"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(exported / n, target)
    producer.joinpath("apt").mkdir()
    shutil.copyfile(apt / "input-lock.json", producer / "apt/input-lock.json")
    output = verification / "caller-reexport"
    caller.export(
        producer,
        output,
        a.operator_repo,
        a.operator_ref,
        a.operator_tree,
        a.source_kind,
        a.remote_ref,
        common.regular(bundle / "binding.json")["sha256"],
        common.CALLER_HASHES["native_acceptance.py"],
        a.architecture,
    )
    common.require(
        common.inventory(output, common.JSON_FILES)
        == common.inventory(exported, common.JSON_FILES),
        "seven-JSON replay differs",
    )
    common.require(
        common.inventory(bundle, files) == original, "evidence changed during replay"
    )
    return original


def export(a, members):
    common.path_check(a.scratch)
    common.path_check(a.out, new=True)
    common.require(
        a.out != a.scratch
        and a.out not in a.scratch.parents
        and a.scratch not in a.out.parents,
        "evidence output overlaps scratch",
    )
    p = a.scratch / "producer"
    c = a.scratch / "caller-export"
    outer(a.scratch / "outer.json", a, members)
    common.inventory(a.scratch / "trusted", set(members))
    common.inventory(
        p / "source",
        {
            "recipe/" + Path(n).name
            for n in members
            if n.startswith("runtime-security/latexml/")
        }
        | {"README.md", "native_acceptance.py", "binding.json"},
    )
    for n, data in members.items():
        common.require(
            (a.scratch / "trusted" / n).read_bytes() == data, "trusted source differs"
        )
    lock = common.read_json(p / "apt/input-lock.json")
    files = allowed(lock, members)
    sources = {}
    for n in files:
        if n.startswith("source/"):
            source = a.scratch / "trusted" / n.removeprefix("source/")
        elif n.startswith("caller-export/"):
            source = a.scratch / n
        elif n == "outer.json":
            source = a.scratch / n
        elif n == "binding.json":
            source = p / "source/binding.json"
        else:
            source = p / n
        sources[n] = source
    common.inventory(
        p / "apt", {n.removeprefix("apt/") for n in files if n.startswith("apt/")}
    )
    for label in ("a", "b"):
        # The unchanged producer renames a TemporaryDirectory (literal 0700).
        common.inventory(
            p / ("package-" + label), common.PACKAGE_FILES, root_mode=0o700
        )
    common.inventory(c, common.JSON_FILES)
    before = {n: common.regular(path) for n, path in sources.items()}
    common.require(
        len(before) <= common.MAX_FILES
        and sum(x["size"] for x in before.values()) <= common.MAX_TOTAL
        and all(x["mode"] in (0o644, 0o755) for x in before.values()),
        "export limits or unsafe mode",
    )
    # Use a distinct private staging directory. Partial output remains private on refusal.
    staging = a.scratch / "evidence-staging"
    common.path_check(staging, new=True)
    staging.mkdir()
    for n, path in sorted(sources.items()):
        target = staging / n
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        target.chmod(before[n]["mode"])
    common.require(
        {n: common.regular(path) for n, path in sources.items()} == before
        and common.inventory(staging, files) == before,
        "export postcopy differs",
    )
    replay(staging, a, members, a.scratch / "export-replay")
    manifest = {
        "schema": "aelira-latexml-complete-evidence-v2",
        **common.identity(a),
        "files": before,
        "file_count": len(before),
        "total_size": sum(x["size"] for x in before.values()),
        "directories": common.directory_modes(staging, files),
    }
    (staging / "manifest.json").write_bytes(common.canonical(manifest))
    (staging / "manifest.json").chmod(0o644)
    copy_exact(staging, a.out, files | {"manifest.json"})
    print(
        json.dumps(
            {
                "file_count": len(files),
                "manifest_sha256": common.regular(a.out / "manifest.json")["sha256"],
            },
            sort_keys=True,
        )
    )


def check(a, members):
    common.path_check(a.evidence)
    common.require(
        type(a.manifest_sha256) is str
        and common.re.fullmatch("[0-9a-f]{64}", a.manifest_sha256),
        "independent manifest hash required",
    )
    common.require(
        common.regular(a.evidence / "manifest.json")["sha256"] == a.manifest_sha256,
        "external manifest mismatch",
    )
    m = common.read_json(a.evidence / "manifest.json", 500_000)
    common.require(
        type(m) is dict
        and set(m)
        == {
            "schema",
            *common.identity(a),
            "files",
            "file_count",
            "total_size",
            "directories",
        }
        and m["schema"] == "aelira-latexml-complete-evidence-v2"
        and all(m[k] == v for k, v in common.identity(a).items()),
        "evidence manifest schema",
    )
    files = allowed(common.read_json(a.evidence / "apt/input-lock.json"), members)
    common.require(
        type(m["file_count"]) is int
        and m["file_count"] == len(files)
        and type(m["total_size"]) is int
        and type(m["directories"]) is dict
        and set(m["directories"]) == {"."} | common.directories(files)
        and type(m["files"]) is dict
        and set(m["files"]) == files,
        "manifest inventory",
    )
    for meta in m["directories"].values():
        common.require(
            type(meta) is dict
            and set(meta) == {"mode"}
            and type(meta["mode"]) is int
            and meta["mode"] == 0o755,
            "manifest typed directory metadata",
        )
    common.require(
        common.directory_modes(a.evidence, files) == m["directories"],
        "manifest actual directory modes differ",
    )
    for meta in m["files"].values():
        common.require(
            type(meta) is dict
            and set(meta) == {"sha256", "size", "mode"}
            and type(meta["size"]) is int
            and 0 < meta["size"] <= common.MAX_FILE
            and type(meta["mode"]) is int
            and meta["mode"] in (0o644, 0o755)
            and type(meta["sha256"]) is str
            and common.re.fullmatch("[0-9a-f]{64}", meta["sha256"]),
            "manifest typed metadata",
        )
    observed = common.inventory(a.evidence, files | {"manifest.json"})
    common.require(
        {n: observed[n] for n in files} == m["files"]
        and sum(x["size"] for x in m["files"].values()) == m["total_size"],
        "manifest bytes",
    )
    # Replay receives only the exact selected members, with no arbitrary diagnostics.
    private = a.verification_scratch
    common.path_check(private, new=True)
    private.mkdir()
    bundle = private / "selected"
    common.require(
        a.evidence != private
        and a.evidence not in private.parents
        and private not in a.evidence.parents,
        "verification output overlaps evidence",
    )
    bundle.mkdir()
    for n in files:
        dest = bundle / n
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((a.evidence / n).read_bytes())
        dest.chmod(observed[n]["mode"])
    common.require(common.inventory(bundle, files) == m["files"], "check selected copy")
    replay(bundle, a, members, private / "replay")
    common.require(
        common.inventory(a.evidence, files | {"manifest.json"}) == observed,
        "check changed evidence",
    )
    print(
        json.dumps(
            {
                "schema": "aelira-latexml-evidence-replay-v1",
                "file_count": len(files),
                "signed_release_count": 4,
                "package_pairs": 2,
                "manifest_sha256": a.manifest_sha256,
            },
            sort_keys=True,
        )
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("export", "check"))
    p.add_argument("--operator-repo", type=Path, required=True)
    p.add_argument("--operator-ref", required=True)
    p.add_argument("--operator-tree", required=True)
    p.add_argument("--source-kind", choices=("synthetic", "public"), required=True)
    p.add_argument("--remote-ref")
    p.add_argument("--architecture", choices=("arm64", "amd64"), required=True)
    p.add_argument("--scratch", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--evidence", type=Path)
    p.add_argument("--verification-scratch", type=Path)
    p.add_argument("--manifest-sha256")
    a = p.parse_args()
    common.os.umask(0o022)
    members = common.selected(a)
    if a.action == "export":
        common.require(
            a.scratch is not None
            and a.out is not None
            and a.evidence is None
            and a.verification_scratch is None
            and a.manifest_sha256 is None,
            "export arguments",
        )
        export(a, members)
    else:
        common.require(
            a.evidence is not None
            and a.verification_scratch is not None
            and a.manifest_sha256 is not None
            and a.scratch is None
            and a.out is None,
            "check arguments",
        )
        check(a, members)


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ):
        print("bounded evidence refused", file=sys.stderr)
        sys.exit(2)
