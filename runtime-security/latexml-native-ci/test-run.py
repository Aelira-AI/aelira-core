#!/usr/bin/env python3
"""Portable local controls; synthetic filesystem/Git checks are not producer proof."""

from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import run as common


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scratch", required=True, type=Path)
    p.add_argument("--operator-repo", required=True, type=Path)
    p.add_argument("--operator-ref", required=True)
    p.add_argument("--operator-tree", required=True)
    a = p.parse_args()
    os.umask(0o022)
    a.source_kind = "synthetic"
    a.remote_ref = None
    a.architecture = "arm64"
    common.path_check(a.scratch, new=True)
    a.scratch.mkdir()
    rows = []

    def control(name, fn, expected):
        try:
            fn()
            code = 0
        except (ValueError, OSError, subprocess.TimeoutExpired):
            code = 2
        rows.append({"case": name, "exit": code, "expected_exit": expected})
        common.require(code == expected, "control result differs")

    base = a.scratch / "positive"
    base.mkdir()
    (base / "nested").mkdir()
    (base / "nested/file").write_bytes(b"owned synthetic input\n")
    control(
        "positive-exact-readable-closure",
        lambda: common.inventory(base, {"nested/file"}),
        0,
    )
    empty = a.scratch / "empty"
    empty.mkdir()
    (empty / "unexpected").mkdir()
    control("extra-empty-directory", lambda: common.inventory(empty, set()), 2)
    symlink = a.scratch / "symlink"
    symlink.mkdir()
    (symlink / "file").symlink_to(base / "nested/file")
    control("symlink-member", lambda: common.inventory(symlink, {"file"}), 2)
    hard = a.scratch / "hardlink"
    hard.mkdir()
    os.link(base / "nested/file", hard / "file")
    control("hardlink-member", lambda: common.inventory(hard, {"file"}), 2)
    inaccessible = a.scratch / "unreadable"
    inaccessible.mkdir()
    (inaccessible / "denied").mkdir()
    (inaccessible / "denied").chmod(0)
    control(
        "unreadable-directory",
        lambda: common.inventory(inaccessible, {"denied/file"}),
        2,
    )
    alias = a.scratch / "alias"
    alias.symlink_to(a.scratch / "positive", target_is_directory=True)
    control("aliased-root", lambda: common.inventory(alias, {"nested/file"}), 2)
    unexpected = a.scratch / "extra"
    unexpected.mkdir()
    (unexpected / "file").write_bytes(b"undeclared text")
    control("undeclared-file", lambda: common.inventory(unexpected, set()), 2)
    receipt = a.scratch / "duplicate.json"
    receipt.write_bytes(b'{"field":1,"field":2}\n')
    control("duplicate-JSON", lambda: common.read_json(receipt), 2)
    nan = a.scratch / "nonfinite.json"
    nan.write_bytes(b'{"field":NaN}\n')
    control("nonfinite-JSON", lambda: common.read_json(nan), 2)
    valid = {n: {"state": "executed", "exit": 0} for n in common.STAGES}
    control(
        "typed-original-stage-positive", lambda: common.validate_stages(valid, True), 0
    )
    false = {n: dict(v) for n, v in valid.items()}
    false["native-run"]["exit"] = False
    control("boolean-stage-exit", lambda: common.validate_stages(false), 2)
    failed = {n: dict(v) for n, v in valid.items()}
    failed["native-run"]["exit"] = 7
    control(
        "original-failure-retained-schema", lambda: common.validate_stages(failed), 0
    )
    control(
        "failed-stage-success-export-refusal",
        lambda: common.validate_stages(failed, True),
        2,
    )
    unavailable = {n: dict(v) for n, v in valid.items()}
    unavailable["native-run"] = {"state": "unavailable", "exit": None}
    control(
        "unavailable-without-fabricated-exit",
        lambda: common.validate_stages(unavailable),
        0,
    )
    unavailable["native-run"]["exit"] = 125
    control(
        "unavailable-status-substitution",
        lambda: common.validate_stages(unavailable),
        2,
    )
    control("selected-Git-source-positive", lambda: common.selected(a), 0)
    wrong = argparse.Namespace(**vars(a))
    wrong.operator_tree = "0" * 40
    control("wrong-selected-tree", lambda: common.selected(wrong), 2)
    wrong = argparse.Namespace(**vars(a))
    wrong.remote_ref = "refs/heads/main"
    control("synthetic-public-ref-refusal", lambda: common.selected(wrong), 2)
    control("existing-output-refusal", lambda: common.path_check(base, new=True), 2)
    oversized = a.scratch / "oversize"
    oversized.write_bytes(b"ab")
    control("bounded-file-size", lambda: common.regular(oversized, 1), 2)
    directory_case = a.scratch / "directory-mode"
    directory_case.mkdir()
    (directory_case / "nested").mkdir()
    (directory_case / "nested/file").write_bytes(b"mode fixture\n")
    control(
        "directory-mode-positive",
        lambda: common.directory_modes(directory_case, {"nested/file"}),
        0,
    )
    (directory_case / "nested").chmod(0o750)
    control(
        "declared-parent-mode-refusal",
        lambda: common.inventory(directory_case, {"nested/file"}),
        2,
    )
    (directory_case / "nested").chmod(0o755)
    directory_case.chmod(0o700)
    control(
        "output-root-mode-refusal",
        lambda: common.inventory(directory_case, {"nested/file"}),
        2,
    )
    result = {
        "schema": "aelira-latexml-ci-portable-controls-v1",
        "source_kind": "synthetic",
        "operator_ref": a.operator_ref,
        "operator_tree": a.operator_tree,
        "case_count": len(rows),
        "cases": rows,
    }
    (a.scratch / "controls.json").write_bytes(common.canonical(result))
    print(
        json.dumps(
            {
                "case_count": len(rows),
                "controls_sha256": common.regular(a.scratch / "controls.json")[
                    "sha256"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired):
        print("portable controls refused", file=sys.stderr)
        sys.exit(2)
