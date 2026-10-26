"""Benign source and archive trust-boundary controls; runs on Mac and Linux."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import tarfile
import tempfile

from integrity import read_archive, sha256, validate_members


def manifest(members: dict[str, bytes]) -> None:
    members["context.sha256"] = "".join(
        f"{sha256(data)}  {name}\n"
        for name, data in sorted(members.items())
        if name != "context.sha256"
    ).encode()


def expect_refusal(label: str, fn, output: dict[str, bool]) -> None:
    try:
        fn()
    except (ValueError, KeyError, TypeError, AssertionError):
        output[label] = True
    else:
        raise AssertionError(f"{label} was accepted")


def make_tar(path: Path, names: list[tuple[str, bytes, str]]) -> None:
    with tarfile.open(path, "w") as archive:
        for name, data, kind in names:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            item.type = tarfile.REGTYPE if kind == "file" else tarfile.SYMTYPE
            if kind != "file":
                item.linkname = "./requirements.txt"
            archive.addfile(item, io.BytesIO(data) if kind == "file" else None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    for name in ("archive-sha256", "origin", "commit", "requirements-blob", "src-tree"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    members = read_archive(args.archive, args.archive_sha256)

    def validate(value):
        return validate_members(
            value,
            origin=args.origin,
            commit=args.commit,
            requirements_blob=args.requirements_blob,
            src_tree=args.src_tree,
        )

    positive = validate(members)
    output: dict[str, bool] = {"positive": positive["src_files"] > 100}

    def changed(name: str, content: bytes):
        changed_members = dict(members)
        changed_members[name] = content
        manifest(changed_members)
        return validate(changed_members)

    def removed(name: str):
        changed_members = dict(members)
        del changed_members[name]
        manifest(changed_members)
        return validate(changed_members)

    expect_refusal(
        "changed_source_with_rehashed_manifest",
        lambda: changed(
            "src/education/handwritten_math_suitability.py",
            members["src/education/handwritten_math_suitability.py"]
            + b"\n# benign change\n",
        ),
        output,
    )
    for name in (
        "src/education/handwritten_math_suitability.py",
        "Dockerfile",
        "pins.txt",
        "source.json",
    ):
        expect_refusal(
            "missing_" + name.replace("/", "_"), lambda name=name: removed(name), output
        )
    expect_refusal(
        "empty_pins", lambda: changed("pins.txt", b"# no selected pins\n"), output
    )
    expect_refusal(
        "unrelated_member", lambda: changed("unrelated.txt", b"benign extra\n"), output
    )
    expect_refusal(
        "duplicate_normalized_pins",
        lambda: changed("pins.txt", members["pins.txt"] + b"sqlalchemy==2.1.5\n"),
        output,
    )
    expect_refusal(
        "duplicate_public_requirement",
        lambda: changed(
            "requirements.txt", members["requirements.txt"] + b"PILLOW==0.0.0\n"
        ),
        output,
    )
    source = json.loads(members["source.json"])
    source["commit"] = "0" * 40
    expect_refusal(
        "foreign_commit",
        lambda: changed("source.json", json.dumps(source).encode()),
        output,
    )
    source = json.loads(members["source.json"])
    source["src_tree"] = None
    expect_refusal(
        "null_git_tree",
        lambda: changed("source.json", json.dumps(source).encode()),
        output,
    )
    duplicate = (
        members["source.json"].rstrip()[:-1]
        + b',"commit":"'
        + args.commit.encode()
        + b'"}'
    )
    expect_refusal(
        "duplicate_json_key", lambda: changed("source.json", duplicate), output
    )
    expect_refusal(
        "nonstandard_json_constant",
        lambda: changed(
            "source.json", members["source.json"].rstrip()[:-1] + b',"extra":NaN}'
        ),
        output,
    )
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "control.tar"
        for label, names in (
            (
                "canonical_alias",
                [
                    ("./requirements.txt", b"one", "file"),
                    ("requirements.txt", b"two", "file"),
                ],
            ),
            ("symlink_member", [("./requirements.txt", b"", "symlink")]),
            ("traversal_member", [("./../outside", b"x", "file")]),
            ("absolute_member", [("/outside", b"x", "file")]),
        ):
            make_tar(archive, names)
            expect_refusal(
                label,
                lambda: read_archive(archive, sha256(archive.read_bytes())),
                output,
            )
    print(
        json.dumps(
            {
                "passed": all(output.values()),
                "controls": output,
                "source_files": positive["src_files"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
