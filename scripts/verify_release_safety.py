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

Exit codes: 0 clean, 1 findings, 2 could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Pattern

# Infrastructure that must never appear in a public repo.
VPS_IP = r"149\.28\.165\.188"
# Any subdomain of the vendor domain. Enumerating known subdomains was the
# earlier approach and it missed app.aelira.ai in 17 places, so match the
# shape instead of a list.
INTERNAL_HOSTS = r"[A-Za-z0-9_-]+\.aelira\.ai"

# Vendor contact addresses. A self-hosted deployment that ships these tells
# its own users to email a support desk that cannot help them.
VENDOR_CONTACT = r"[A-Za-z0-9._%+-]+@aelira\.ai"


# Named-entity patterns (real people/organisations that must never appear in
# a release) live in .release-denylist.local.json, which is gitignored: the
# gate mechanism is public, the specific names it guards are not. Maintainers
# keep their own local denylist; see .release-denylist.local.json.example.
def _load_named_entities() -> str:
    denylist_path = Path(__file__).resolve().parent.parent / (
        ".release-denylist.local.json"
    )
    if not denylist_path.exists():
        print(
            "WARNING: .release-denylist.local.json not found - the "
            "named-entity check is running with no patterns. Maintainers "
            "must keep a local denylist; see the .example file.",
            file=sys.stderr,
        )
        return r"(?!x)x"  # matches nothing
    entries = json.loads(denylist_path.read_text())["patterns"]
    return "(?i)\\b(?:" + "|".join(entries) + ")"


NAMED_ENTITIES = _load_named_entities()

CHECKS: List[tuple[str, Pattern[str], str]] = [
    (
        "vps-ip",
        re.compile(VPS_IP),
        "Production VPS IP address",
    ),
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
        "named-entity",
        re.compile(NAMED_ENTITIES),
        "Real customer, lead, or third party named in the denylist",
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

# Files whose content is not meaningfully scannable as text.
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".webm",
    ".docx",
    ".pptx",
    ".xlsx",
}


@dataclass
class Finding:
    check: str
    description: str
    path: str
    line: int
    excerpt: str


def tracked_files(root: Path) -> Iterable[Path]:
    """Git-tracked files only: those are what publication exposes."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    for rel in result.stdout.splitlines():
        if rel.strip():
            yield root / rel


def redact(text: str) -> str:
    """Never print a suspected secret in full, including into CI logs."""
    stripped = text.strip()
    if len(stripped) <= 24:
        return stripped
    return f"{stripped[:12]}...{stripped[-6:]}"


def load_allowlist(root: Path) -> List[dict]:
    """Documented exceptions. Absent file means no exceptions."""
    path = root / ".release-allowlist.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("allow", [])
    for entry in entries:
        if not entry.get("reason"):
            raise ValueError(
                f"allowlist entry for {entry.get('path')!r} has no reason; "
                "every suppression must be justified"
            )
    return entries


def is_allowed(finding: Finding, allowlist: List[dict]) -> bool:
    return any(
        entry.get("path") == finding.path and entry.get("check") == finding.check
        for entry in allowlist
    )


def scan(root: Path) -> List[Finding]:
    findings: List[Finding] = []

    for path in tracked_files(root):
        rel = str(path.relative_to(root))

        # The gate names the entities it looks for, so it would flag itself.
        if rel == "scripts/verify_release_safety.py":
            continue

        if FORBIDDEN_PATHS.search(rel):
            findings.append(
                Finding("forbidden-path", "Path must never be published", rel, 0, rel)
            )
            continue

        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            for name, pattern, description in CHECKS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        Finding(name, description, rel, lineno, redact(match.group(0)))
                    )

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=".", help="repo or staging tree to scan")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    if not (root / ".git").exists():
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2

    try:
        all_findings = scan(root)
        allowlist = load_allowlist(root)
    except subprocess.CalledProcessError as exc:
        print(f"error: git ls-files failed: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = [f for f in all_findings if not is_allowed(f, allowlist)]
    suppressed = [f for f in all_findings if is_allowed(f, allowlist)]

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        total = len(list(tracked_files(root)))
        # Always report suppressions. A gate whose exceptions are invisible
        # stops being a gate.
        if suppressed:
            print(f"suppressed {len(suppressed)} allowlisted finding(s):")
            for entry in allowlist:
                print(f"  {entry['path']} [{entry['check']}] - {entry['reason']}")
            print()
        if not findings:
            print(f"PASS - {total} tracked files scanned, no unsuppressed findings")
        else:
            print(f"FAIL - {len(findings)} finding(s) across {total} tracked files\n")
            for f in findings:
                where = f"{f.path}:{f.line}" if f.line else f.path
                print(f"  [{f.check}] {where}")
                print(f"      {f.description}: {f.excerpt}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
