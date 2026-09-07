"""Contracts for the runtime/development Python dependency boundary."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s#]+)")

DEV_ONLY_PACKAGES = {
    "bidict",
    "black",
    "blinker",
    "configargparse",
    "coverage",
    "flask",
    "flask-cors",
    "flask-login",
    "gevent",
    "geventhttpclient",
    "iniconfig",
    "itsdangerous",
    "jinja2",
    "locust",
    "mypy-extensions",
    "pathspec",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "pytokens",
    "python-engineio",
    "python-socketio",
    "pyzmq",
    "ruff",
    "simple-websocket",
    "websocket-client",
    "werkzeug",
    "wsproto",
    "zope-event",
    "zope-interface",
}

PREVIOUSLY_UNPINNED_RUNTIME_PACKAGES = {
    "chardet",
    "encutils",
    "mpmath",
    "opencv-python",
    "pathvalidate",
    "pdfminer-six",
    "pydantic-core",
    "pypdfium2",
    "typer",
}

DEV_IMPORT_ROOTS = {
    "black",
    "coverage",
    "flask",
    "gevent",
    "locust",
    "pytest",
    "ruff",
    "werkzeug",
    "zmq",
}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _active_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _active_lines(path):
        if line.startswith("-r "):
            continue
        specification = line.split(" #", 1)[0].rstrip()
        match = PIN.fullmatch(specification)
        assert match is not None, f"dependency is not exactly pinned: {line}"
        name, version = match.groups()
        normalized = _normalize(name)
        assert normalized not in pins, f"duplicate dependency pin: {name}"
        pins[normalized] = version
    return pins


def test_runtime_and_development_requirements_are_disjoint_and_exact() -> None:
    runtime = _pins(ROOT / "requirements.txt")
    development_path = ROOT / "requirements-dev.txt"
    development = _pins(development_path)

    assert _active_lines(development_path)[0] == "-r requirements.txt"
    assert runtime.keys().isdisjoint(development)
    assert DEV_ONLY_PACKAGES <= development.keys()
    assert DEV_ONLY_PACKAGES.isdisjoint(runtime)
    assert PREVIOUSLY_UNPINNED_RUNTIME_PACKAGES <= runtime.keys()


def test_build_and_audit_consumers_use_the_correct_dependency_set() -> None:
    production = (ROOT / "Dockerfile").read_text()
    development = (ROOT / "Dockerfile.dev").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    pyproject = (ROOT / "pyproject.toml").read_text()

    assert "requirements-dev.txt" not in production
    assert "COPY requirements.txt ." in production
    assert "pip install --no-cache-dir -r requirements.txt" in production

    assert "COPY requirements.txt requirements-dev.txt ./" in development
    assert "pip install --no-cache-dir -r requirements-dev.txt" in development
    assert "# Install development dependencies" not in development

    assert "pip install -r requirements-dev.txt" in ci
    assert "pip-audit --requirement requirements.txt --strict" in ci
    assert "pip-audit --requirement requirements-dev.txt --strict" in ci
    assert 'pip install "ruff==0.16.5" "black==26.5.1"' in ci

    assert "cyclonedx-py requirements requirements.txt" in release
    assert 'dependencies = { file = ["requirements.txt"] }' in pyproject


def test_contributor_docs_name_runtime_and_development_install_paths() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    dependencies = (ROOT / "docs/DEPENDENCIES.md").read_text()

    assert "pip install -r requirements-dev.txt" in contributing
    assert "Runtime dependencies" in dependencies
    assert "Development and test dependencies" in dependencies
    assert "requirements-dev.txt" in dependencies


def test_production_source_does_not_import_development_only_packages() -> None:
    violations: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = {node.module.split(".", 1)[0]}
            else:
                continue
            overlap = imported & DEV_IMPORT_ROOTS
            if overlap:
                violations.append(f"{path.relative_to(ROOT)}: {sorted(overlap)}")

    assert violations == []
