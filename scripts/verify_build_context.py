#!/usr/bin/env python3
"""Verify Docker ignore rules using harmless fixtures and Docker's own matcher.

Run ``python scripts/verify_build_context.py`` with a running Docker daemon and
BuildKit. No dependencies, base images, network, or repository build are needed.
Only the two .dockerignore files are read from the checkout. Temporary scratch
builds receive generated sentinel files; no working-tree contents are sent.

Exit codes: 0 passed, 1 policy mismatch, 2 verification could not run.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SENTINEL = b"Harmless Docker context verification fixture.\n"

# Repeat every class at the context root and multiple nested depths. A trailing
# /fixture denotes a directory; .git is also checked as a worktree pointer file.
EXCLUDED = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".env.production.local",
    "runtime.env",
    ".venv/fixture",
    "venv/fixture",
    "env/fixture",
    "ENV/fixture",
    "__pycache__/fixture",
    "module.pyc",
    "module.pyo",
    "package.egg-info/fixture",
    "node_modules/fixture",
    ".pytest_cache/fixture",
    ".mypy_cache/fixture",
    ".ruff_cache/fixture",
    ".cache/fixture",
    ".coverage",
    ".coverage.worker",
    "coverage.xml",
    "coverage/fixture",
    "coverage_html/fixture",
    "htmlcov/fixture",
    "test-results/fixture",
    "playwright-report/fixture",
    "dist-ssr/fixture",
    "build/fixture",
    "dist/fixture",
    "logs/fixture",
    "runtime.log",
    "uploads/fixture",
    "tmp/fixture",
    "temp/fixture",
    "runtime.db",
    "runtime.sqlite",
    "runtime.sqlite3",
    "secrets/fixture",
    ".secrets/fixture",
    "credentials/fixture",
    ".credentials/fixture",
    "credentials.json",
    "service-account-local.json",
    "private.key",
    "private.pem",
    "private.crt",
    "private.cert",
    "private.p12",
    "private.pfx",
    "private.jks",
    "private.keystore",
    ".aws/fixture",
    ".ssh/fixture",
    ".docker/fixture",
    ".kube/fixture",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git/fixture",
    "worktree/.git",
    ".claude/fixture",
    ".codex/fixture",
    ".agents/fixture",
    ".cursor/fixture",
    ".gemini/fixture",
    ".aider.chat.history.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".release-denylist.local.json",
    ".release-denylist.local.json.backup",
    ".release-denylist.local.json.example",
    ".release-allowlist.json",
    ".vscode/fixture",
    ".idea/fixture",
    ".DS_Store",
    "Thumbs.db",
)

PUBLIC = (
    ".env.example",
    ".env.production.example",
    "config/settings.json",
    "src/credentials.py",
    "src/secrets.py",
    "src/config.ts",
    "public/logo.svg",
    "public/robots.txt",
)

REQUIRED = {
    "backend": (
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "entrypoint.sh",
        "alembic.ini",
        "alembic/env.py",
        "alembic/versions/migration.py",
        "src/api/main.py",
        "src/static/logo.png",
        "config/pa11y.json",
        "scripts/configure_pa11y_chromium.py",
        "scripts/smoke_pa11y_runtime.py",
        "dashboard/dist/index.html",
        "dashboard/dist/assets/app.js",
        "LICENSE",
    ),
    "dashboard": (
        "package.json",
        "package-lock.json",
        "index.html",
        "nginx.conf",
        "vite.config.ts",
        "tsconfig.json",
        "tsconfig.node.json",
        "src/main.tsx",
        "src/components/App.tsx",
        "src/index.css",
        "LICENSE",
    ),
}


def fixture_paths(name: str) -> tuple[set[str], set[str]]:
    excluded = {
        prefix + path
        for prefix in ("", "component/", "component/nested/")
        for path in EXCLUDED
    }
    retained = {
        prefix + path
        for prefix in ("", "component/", "component/nested/")
        for path in PUBLIC
    } | set(REQUIRED[name])
    if name == "backend":
        # The dashboard's prebuilt public bundle is intentionally retained, but
        # that exception must not reinclude local inputs inside the bundle.
        excluded |= {"dashboard/dist/" + path for path in EXCLUDED}
        excluded.add("data/piper-voices/fixture")
    return excluded, retained


def verify(root: Path, name: str, ignore_path: str, docker: str) -> bool:
    excluded, retained = fixture_paths(name)
    with tempfile.TemporaryDirectory(prefix="aelira-build-context-") as temporary:
        directory = Path(temporary)
        context = directory / "fixture"
        output = directory / "export"
        context.mkdir()
        (context / ".dockerignore").write_bytes((root / ignore_path).read_bytes())
        (context / "Dockerfile").write_text("FROM scratch\nCOPY . /context/\n")
        for relative in sorted(excluded | retained):
            destination = context / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(SENTINEL)

        result = subprocess.run(
            [
                docker,
                "build",
                "--no-cache",
                "--network=none",
                "--progress=plain",
                "--output",
                f"type=local,dest={output}",
                str(context),
            ],
            env={**os.environ, "DOCKER_BUILDKIT": "1"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(f"{name}: scratch build failed\n{result.stderr}")

        exported = output / "context"
        unexpected = sorted(path for path in excluded if (exported / path).exists())
        missing = sorted(
            path
            for path in retained
            if not (exported / path).is_file()
            or (exported / path).read_bytes() != SENTINEL
        )
        if unexpected or missing:
            print(
                f"FAIL {name}: {len(unexpected)} excluded fixtures retained; "
                f"{len(missing)} required fixtures missing"
            )
            for label, paths in (("unexpected", unexpected), ("missing", missing)):
                for path in paths[:12]:
                    print(f"  {label}: {path}")
                if len(paths) > 12:
                    print(f"  ... {len(paths) - 12} more {label} fixtures")
            return False
        print(
            f"PASS {name}: {len(excluded)} exclusions and "
            f"{len(retained)} required files verified by Docker"
        )
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="checkout containing the two .dockerignore files",
    )
    parser.add_argument("--docker", default="docker", help="Docker CLI executable")
    args = parser.parse_args()
    try:
        results = [
            verify(args.root, name, ignore, args.docker)
            for name, ignore in (
                ("backend", ".dockerignore"),
                ("dashboard", "dashboard/.dockerignore"),
            )
        ]
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
