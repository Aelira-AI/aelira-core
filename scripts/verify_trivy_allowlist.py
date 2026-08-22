#!/usr/bin/env python3
"""Fail closed unless every Trivy exemption has complete, current governance."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
METADATA = re.compile(r"#\s*(owner|justification|expires)\s*:\s*(.*?)\s*$", re.I)
REQUIRED = {"owner", "justification", "expires"}


def validate(path: Path, *, today: date | None = None) -> list[str]:
    errors: list[str] = []
    pending: dict[str, tuple[str, int]] = {}
    seen_cves: set[str] = set()
    current_date = today or date.today()

    def reject_pending() -> None:
        if pending:
            first_line = min(line_number for _, line_number in pending.values())
            errors.append(
                f"line {first_line}: metadata is not followed by a CVE exemption"
            )
            pending.clear()

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line:
            reject_pending()
            continue
        if line.startswith("#"):
            match = METADATA.fullmatch(line)
            if not match:
                reject_pending()
                continue
            key, value = match.group(1).lower(), match.group(2).strip()
            if key in pending:
                errors.append(f"line {line_number}: duplicate {key} metadata")
            pending[key] = (value, line_number)
            continue

        if not CVE_ID.fullmatch(line):
            errors.append(f"line {line_number}: malformed exemption; expected a CVE ID")
            pending.clear()
            continue

        if line in seen_cves:
            errors.append(f"line {line_number}: duplicate CVE exemption: {line}")
        seen_cves.add(line)

        missing = REQUIRED - pending.keys()
        if missing:
            errors.append(
                f"line {line_number}: {line} missing metadata: {', '.join(sorted(missing))}"
            )
        for key in ("owner", "justification"):
            if key in pending and not pending[key][0]:
                errors.append(f"line {pending[key][1]}: {key} must not be empty")

        if "expires" in pending:
            value, metadata_line = pending["expires"]
            try:
                expiry = date.fromisoformat(value)
                if expiry.isoformat() != value:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"line {metadata_line}: malformed expiry; expected ISO YYYY-MM-DD"
                )
            else:
                if expiry <= current_date:
                    errors.append(
                        f"line {metadata_line}: exemption {line} expired on {expiry}"
                    )
        pending.clear()

    reject_pending()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("allowlist", nargs="?", default=".trivyignore", type=Path)
    args = parser.parse_args()
    try:
        errors = validate(args.allowlist)
    except OSError as exc:
        print(f"Unable to read Trivy allowlist: {exc}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Trivy allowlist governance valid: {args.allowlist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
