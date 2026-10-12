"""Benign source exporter path/ref controls against an explicit public checkout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    script = Path(__file__).with_name("prepare.py")

    def run(out: Path, commit: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(args.repo),
                "--expected-ref",
                commit,
                "--expected-origin",
                args.origin,
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    with tempfile.TemporaryDirectory(
        dir=Path(tempfile.gettempdir()).resolve()
    ) as temporary:
        root = Path(temporary)
        real = root / "real"
        real.mkdir()
        alias = root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        unsafe = run(alias / "export", args.commit)
        assert unsafe.returncode != 0 and not (real / "export").exists()
        foreign = run(root / "foreign", "0" * 40)
        assert foreign.returncode != 0 and not (root / "foreign").exists()
        occupied = root / "occupied"
        occupied.mkdir()
        existing = run(occupied, args.commit)
        assert existing.returncode != 0 and not occupied.with_suffix(".tar").exists()
        alias_name = root / "same.tar"
        archive_alias = run(alias_name, args.commit)
        assert archive_alias.returncode != 0 and not alias_name.exists()
        positive = run(root / "positive", args.commit)
        assert positive.returncode == 0
        result = json.loads(positive.stdout)
        assert result["commit"] == args.commit and (root / "positive.tar").is_file()
    print(
        json.dumps(
            {
                "passed": True,
                "controls": {
                    "positive": True,
                    "foreign_ref_before_output": True,
                    "symlink_parent_before_output": True,
                    "existing_output_refused": True,
                    "archive_name_alias_before_output": True,
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
