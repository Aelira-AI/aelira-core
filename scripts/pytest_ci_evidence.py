"""Opt-in, credential-free pytest execution evidence for the CI policy gate."""

import json
import os
from pathlib import Path

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("test evidence")
    group.addoption("--test-evidence", metavar="PATH", default=None)
    group.addoption("--test-evidence-profile", default="main")
    group.addoption("--test-evidence-revision", default=None)


def pytest_configure(config):
    if config.getoption("test_evidence"):
        config.pluginmanager.register(EvidenceRecorder(config), "ci-evidence-recorder")


class EvidenceRecorder:
    def __init__(self, config):
        self.config = config
        self.collected = set()
        self.deselected = set()
        self.tests = {}
        self.collection = []

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        # Capture before -k/-m and other plugins remove items.
        self.collected.update(item.nodeid for item in items)
        yield

    def pytest_deselected(self, items):
        self.deselected.update(item.nodeid for item in items)

    def pytest_collectreport(self, report):
        if report.outcome in {"skipped", "failed"}:
            self.collection.append({"nodeid": report.nodeid, "outcome": report.outcome})

    def pytest_runtest_logreport(self, report):
        strict_xpass = (
            report.failed
            and isinstance(report.longrepr, str)
            and report.longrepr.startswith("[XPASS(strict)]")
        )
        self.tests.setdefault(report.nodeid, []).append(
            {
                "phase": report.when,
                "outcome": report.outcome,
                "expected_failure": hasattr(report, "wasxfail") or strict_xpass,
            }
        )

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        # pytest-cov can change the exit status after its sessionfinish yield.
        yield
        report = {
            "schema_version": 1,
            "revision": os.environ.get("GITHUB_SHA")
            or self.config.getoption("test_evidence_revision"),
            "profile": self.config.getoption("test_evidence_profile"),
            "session": {"finished": True, "exitstatus": int(session.exitstatus)},
            "collected": sorted(self.collected),
            "deselected": sorted(self.deselected),
            "tests": [
                {"nodeid": nodeid, "outcomes": outcomes}
                for nodeid, outcomes in sorted(self.tests.items())
            ],
            "collection": sorted(
                self.collection, key=lambda entry: (entry["nodeid"], entry["outcome"])
            ),
        }
        path = Path(self.config.getoption("test_evidence"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
