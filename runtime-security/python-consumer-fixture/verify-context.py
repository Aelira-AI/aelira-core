"""Post-authentication standalone source verifier for a prepared context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from integrity import validate_members


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("context", type=Path)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--requirements-blob", required=True)
    parser.add_argument("--src-tree", required=True)
    args = parser.parse_args()
    root = args.context.resolve(strict=True)
    members = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("context symlink")
        if path.is_file():
            members[str(path.relative_to(root))] = path.read_bytes()
    result = validate_members(
        members,
        origin=args.origin,
        commit=args.commit,
        requirements_blob=args.requirements_blob,
        src_tree=args.src_tree,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
