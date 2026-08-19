#!/usr/bin/env python3
"""Block a public release that would leak something.

This gate exists because publication is irreversible. A GitHub repository that
goes public is forked, cached, and archived within minutes, so "we noticed and
took it down" is not a remedy. The gate therefore fails closed: anything it
cannot rule out is a finding, and findings block.

It scans only *git-tracked* files, because those are what publication exposes.
Untracked working-tree noise (venv/, node_modules/) is deliberately ignored.

Usage:
    python scripts/verify_release_safety.py              # scan the repo
    python scripts/verify_release_safety.py --json       # machine-readable
    python scripts/verify_release_safety.py --path DIR   # scan a staging tree
    python scripts/verify_release_safety.py --strict-policy --denylist PATH

Exit codes: 0 clean, 1 findings, 2 could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Pattern

# Any subdomain of the vendor domain — matched by shape, not an enumerated
# list, so a new internal subdomain can't slip through. Specific internal
# infrastructure values (IPs, hostnames) live in the gitignored local
# denylist alongside named entities, never in this tracked file.
INTERNAL_HOSTS = r"[A-Za-z0-9_-]+\.aelira\.ai"

# Vendor contact addresses. A self-hosted deployment that ships these tells
# its own users to email a support desk that cannot help them.
VENDOR_CONTACT = r"[A-Za-z0-9._%+-]+@aelira\.ai"


POLICY_ERROR = "error: disclosure policy unavailable or invalid"
ALLOWLIST_ERROR = "error: invalid .release-allowlist.json configuration"


class PolicyError(ValueError):
    """The protected named-entity policy cannot be used safely."""


class AllowlistError(ValueError):
    """The tracked release allowlist cannot be used safely."""


BASE_CHECKS: List[tuple[str, Pattern[str], str]] = [
    (
        "internal-host",
        re.compile(INTERNAL_HOSTS),
        "Internal service hostname",
    ),
    (
        "vendor-contact",
        re.compile(VENDOR_CONTACT),
        "Vendor contact address; a self-hoster's users would be sent to it",
    ),
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "Private key material",
    ),
    (
        "aws-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access key id",
    ),
    (
        "google-key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        "Google/Gemini API key",
    ),
    (
        "slack-token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}"),
        "Slack token",
    ),
    (
        "github-token",
        re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}"),
        "GitHub token",
    ),
    (
        "apr1-hash",
        re.compile(r"\$apr1\$"),
        "htpasswd apr1 hash",
    ),
    (
        "generic-secret",
        # Deliberately narrow: an assignment whose value looks like real entropy,
        # not a placeholder. Broad patterns here train people to ignore the gate.
        re.compile(
            r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*"
            r"['\"](?!your[_-]|xxx|placeholder|changeme|example|<)"
            r"[A-Za-z0-9_\-]{24,}['\"]"
        ),
        "Assignment that looks like a real credential",
    ),
]


def load_named_entity_policy(
    denylist: Path | None, strict: bool
) -> Pattern[str] | None:
    """Load and compile protected patterns without exposing policy contents."""
    if denylist is None:
        if strict:
            raise PolicyError
        denylist = Path(__file__).resolve().parent.parent / (
            ".release-denylist.local.json"
        )
        if not denylist.exists():
            print(
                "WARNING: .release-denylist.local.json not found - the "
                "named-entity check is running with no patterns. Maintainers "
                "must keep a local denylist; see the .example file.",
                file=sys.stderr,
            )
            return None

    try:
        data = json.loads(denylist.read_text(encoding="utf-8"))
        patterns = data["patterns"] if isinstance(data, dict) else None
        if (
            not isinstance(patterns, list)
            or not patterns
            or any(not isinstance(item, str) or not item for item in patterns)
        ):
            raise PolicyError
        return re.compile(r"(?i)\b(?:" + "|".join(patterns) + ")")
    except (
        OSError,
        UnicodeError,
        KeyError,
        json.JSONDecodeError,
        re.error,
        PolicyError,
        TypeError,
    ) as exc:
        raise PolicyError from exc


def checks_for_policy(
    named_entities: Pattern[str] | None,
) -> List[tuple[str, Pattern[str], str]]:
    checks = list(BASE_CHECKS)
    if named_entities is not None:
        checks.insert(
            2,
            (
                "named-entity",
                named_entities,
                "Real customer, lead, or third party named in the denylist",
            ),
        )
    return checks


# Filenames that must never be published regardless of content.
FORBIDDEN_PATHS = re.compile(
    r"(?i)(?:^|/)(?:"
    r"\.env(?!\.example)"
    r"|keys?/"
    r"|MASTER_TODO\.md"
    r"|SECURITY_AUDIT\.md"
    r"|server_commands\.txt"
    r"|incidents?/"
    r"|outreach/"
    r"|docs/(?:sales|planning|marketing|outreach|demo|audits)/"
    r")"
)


@dataclass
class Finding:
    check: str
    description: str
    path: str
    line: int
    excerpt: str


def tracked_files(root: Path) -> Iterable[tuple[str, str, str]]:
    """Yield tracked paths, index modes, and blob IDs, safely NUL-delimited."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"],
        capture_output=True,
        check=True,
    )
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3 or fields[2] != b"0":
            raise ValueError("git index contains an invalid or unmerged entry")
        mode = fields[0].decode("ascii")
        if mode not in {"100644", "100755", "120000"}:
            raise ValueError("git index contains an unsupported entry mode")
        oid = fields[1].decode("ascii")
        rel = raw_path.decode("utf-8", errors="surrogateescape")
        yield rel, mode, oid


def tracked_blob(root: Path, oid: str) -> bytes:
    """Read an exact blob from the index object database."""
    return subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", oid],
        capture_output=True,
        check=True,
    ).stdout


def redact(text: str) -> str:
    """Never print a suspected secret in full, including into CI logs."""
    stripped = text.strip()
    if len(stripped) <= 24:
        return stripped
    return f"{stripped[:12]}...{stripped[-6:]}"


def redacted_path(path: str) -> str:
    """Return a stable identifier without disclosing a protected path."""
    digest = hashlib.sha256(path.encode("utf-8", errors="surrogateescape")).hexdigest()
    return f"<redacted-path:{digest[:16]}>"


def human_string(value: object) -> str:
    """Quote untrusted text so it cannot inject terminal or CI log controls."""
    return json.dumps(str(value), ensure_ascii=True)


def load_allowlist(root: Path, entries: Iterable[tuple[str, str, str]]) -> List[dict]:
    """Load documented exceptions from the exact tracked index blob."""
    entry = next(
        (item for item in entries if item[0] == ".release-allowlist.json"), None
    )
    if entry is None:
        return []
    try:
        data = json.loads(tracked_blob(root, entry[2]).decode("utf-8"))
        if not isinstance(data, dict):
            raise AllowlistError
        allow_entries = data.get("allow", [])
        if not isinstance(allow_entries, list):
            raise AllowlistError
        for entry in allow_entries:
            if not isinstance(entry, dict) or any(
                not isinstance(entry.get(field), str) or not entry[field]
                for field in ("path", "check", "reason")
            ):
                raise AllowlistError
        return allow_entries
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        AllowlistError,
        TypeError,
    ) as exc:
        raise AllowlistError from exc


def is_allowed(finding: Finding, allowlist: List[dict]) -> bool:
    if finding.check == "named-entity":
        return False
    return any(
        entry.get("path") == finding.path and entry.get("check") == finding.check
        for entry in allowlist
    )


def scan(
    root: Path,
    checks: List[tuple[str, Pattern[str], str]],
    entries: Iterable[tuple[str, str, str]],
) -> tuple[List[Finding], int]:
    findings: List[Finding] = []
    total = 0

    for rel, _mode, oid in entries:
        total += 1

        path_matches = [
            (name, match, description)
            for name, pattern, description in checks
            if (match := pattern.search(rel)) is not None
        ]
        protected_path = any(name == "named-entity" for name, _, _ in path_matches)
        finding_path = redacted_path(rel) if protected_path else rel

        for name, match, description in path_matches:
            findings.append(
                Finding(
                    name,
                    description,
                    finding_path,
                    0,
                    (
                        "[REDACTED]"
                        if protected_path or name == "named-entity"
                        else redact(match.group(0))
                    ),
                )
            )

        if FORBIDDEN_PATHS.search(rel):
            findings.append(
                Finding(
                    "forbidden-path",
                    "Path must never be published",
                    finding_path,
                    0,
                    "[REDACTED]" if protected_path else rel,
                )
            )

        blob = tracked_blob(root, oid)
        content = blob.decode("utf-8", errors="ignore")

        for lineno, line in enumerate(content.splitlines(), start=1):
            line_matches = [
                (name, match, description)
                for name, pattern, description in checks
                if (match := pattern.search(line)) is not None
            ]
            protected_line = any(name == "named-entity" for name, _, _ in line_matches)
            for name, match, description in line_matches:
                findings.append(
                    Finding(
                        name,
                        description,
                        finding_path,
                        lineno,
                        "[REDACTED]" if protected_line else redact(match.group(0)),
                    )
                )

    return findings, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=".", help="repo or staging tree to scan")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--strict-policy",
        action="store_true",
        help="fail closed unless an explicit valid denylist is available",
    )
    parser.add_argument("--denylist", help="protected named-entity policy JSON")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    try:
        policy = load_named_entity_policy(
            Path(args.denylist).resolve() if args.denylist else None,
            args.strict_policy,
        )
    except PolicyError:
        print(POLICY_ERROR, file=sys.stderr)
        return 2

    if not (root / ".git").exists():
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2

    try:
        entries = list(tracked_files(root))
        all_findings, total = scan(root, checks_for_policy(policy), entries)
        allowlist = load_allowlist(root, entries)
    except subprocess.CalledProcessError:
        print("error: unable to read tracked git data", file=sys.stderr)
        return 2
    except AllowlistError:
        print(ALLOWLIST_ERROR, file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = [f for f in all_findings if not is_allowed(f, allowlist)]
    suppressed = [f for f in all_findings if is_allowed(f, allowlist)]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        # Always report suppressions. A gate whose exceptions are invisible
        # stops being a gate.
        if suppressed:
            print(
                f"suppressed {len(suppressed)} allowlisted finding(s); "
                "see tracked .release-allowlist.json"
            )
            print()
        if not findings:
            print(f"PASS - {total} tracked files scanned, no unsuppressed findings")
        else:
            print(f"FAIL - {len(findings)} finding(s) across {total} tracked files\n")
            for f in findings:
                path = human_string(f.path)
                where = f"{path}:{f.line}" if f.line else path
                print(f"  [{f.check}] {where}")
                print(f"      {f.description}: {human_string(f.excerpt)}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
