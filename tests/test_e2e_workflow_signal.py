"""Release E2E signal is deterministic and separate from the scheduled matrix."""

from pathlib import Path

from tests.conftest import KNOWN_BROKEN

ROOT = Path(__file__).parents[1]


def test_release_smoke_is_chromium_only_and_gates_ci():
    config = (ROOT / "dashboard/playwright.release.config.ts").read_text()
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "name: 'release-chromium'" in config
    assert "testMatch: '**/*.spec.ts'" in config
    assert "Desktop Firefox" not in config
    assert "grep: /@release/" in config
    assert "npm run test:e2e:release" in workflow
    assert "playwright install --with-deps chromium" in workflow


def test_scheduled_matrix_is_not_a_release_gate():
    workflow = (ROOT / ".github/workflows/browser-matrix.yml").read_text()
    config = (ROOT / "dashboard/playwright.matrix.config.ts").read_text()

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "npm run test:e2e:matrix" in workflow
    assert "playwright install --with-deps chromium firefox" in workflow
    assert "Desktop Chrome" in config
    assert "Desktop Firefox" in config
    assert "grep: /@release/" in config


def test_no_known_broken_or_blanket_browser_e2e_quarantine_remains():
    conftest = (ROOT / "tests/conftest.py").read_text()

    assert KNOWN_BROKEN == {}
    assert "skip_browser" not in conftest
    assert "skip_e2e" not in conftest
