#!/usr/bin/env python3
"""Exercise evidence export against a real completed native cJSON build."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "repo", "scratch"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arch", required=True)
    args = parser.parse_args()
    args.scratch.mkdir(mode=0o700)
    revision = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
    exporter = Path(__file__).resolve().with_name("collect-evidence.py")
    rows = []

    def invoke(name, source, exit_code=0, require_builder=False):
        destination = args.scratch / (name + "-export")
        command = [sys.executable, "-B", str(exporter), "--source", str(source), "--dest", str(destination),
                   "--repo", str(args.repo), "--arch", args.arch, "--revision", revision, "--tree", tree,
                   "--build-exit", str(exit_code)]
        if require_builder:
            command.append("--require-builder")
        result = subprocess.run(command, capture_output=True, timeout=180)
        receipt = json.loads((destination / "component.json").read_text())
        assert "unlisted-private.txt" not in receipt["files"]
        return result.returncode, receipt

    def snapshot(name):
        destination = args.scratch / (name + "-input")
        # Hard links keep large retained signed indexes immutable and avoid
        # duplicating them. A changed fixture unlinks its file before writing.
        def copy_input(source, target):
            try:
                os.link(source, target)
            except PermissionError:
                # Online preparation files may belong to container root.
                shutil.copyfile(source, target)
        shutil.copytree(args.source, destination, copy_function=copy_input, symlinks=True)
        (destination / "unlisted-private.txt").write_text("SYNTHETIC-UNLISTED-DATA\n")
        return destination

    source = snapshot("positive")
    result, receipt = invoke("positive", source)
    assert result == 0 and receipt["status"] == "pass"
    rows.append("completed-native-export")
    result, receipt = invoke("failed-process", source, exit_code=7)
    assert result == 1 and receipt["status"] == "fail" and not receipt["files"]
    rows.append("failed-process-refused")

    for case, relative in (("changed-recipe", "frozen-recipe/build-source.py"),
                           ("missing-native-control", "build-a/native-O2.log"),
                           ("unsafe-source-link", "source-input/cjson_1.7.19-2.dsc")):
        source = snapshot(case)
        path = source / relative
        data = path.read_bytes()
        path.unlink()
        if case == "changed-recipe":
            path.write_bytes(data + b"\n# synthetic changed recipe\n")
        elif case == "unsafe-source-link":
            path.symlink_to(args.source / relative)
        result, receipt = invoke(case, source)
        assert result == 1 and receipt["status"] == "fail"
        rows.append(case + "-refused")

    source = snapshot("missing-builder")
    tar = source / "provenance/builder.tar"
    if tar.exists():
        tar.unlink()
    result, receipt = invoke("missing-builder", source, require_builder=True)
    assert result == 1 and receipt["status"] == "fail"
    rows.append("missing-required-builder-refused")
    link = args.scratch / "source-alias"
    link.symlink_to(args.source, target_is_directory=True)
    result, receipt = invoke("source-alias", link)
    assert result == 1 and receipt["status"] == "fail" and not receipt["files"]
    rows.append("source-directory-alias-refused")
    print(json.dumps({"schema": "aelira-cjson-evidence-controls-v1", "status": "pass",
                      "count": len(rows), "controls": rows}, sort_keys=True))


if __name__ == "__main__":
    main()
