from src.scanners.engine_evidence import (
    estimate_coverage_for_engines,
    should_run_pa11y,
)


def test_quick_scan_remains_axe_only() -> None:
    assert should_run_pa11y("quick") is False


def test_comprehensive_and_deep_scans_run_pa11y() -> None:
    assert should_run_pa11y("comprehensive") is True
    assert should_run_pa11y("deep") is True


def test_coverage_is_derived_from_completed_engines() -> None:
    assert estimate_coverage_for_engines(["axe-core"]) == 90.0
    assert estimate_coverage_for_engines(["axe-core", "pa11y"]) == 95.0
    assert estimate_coverage_for_engines(["axe-core", "pa11y", "ai-vision"]) == 98.0


def test_requested_mode_cannot_inflate_failed_pa11y_evidence() -> None:
    engines_after_pa11y_failure = ["axe-core"]

    assert estimate_coverage_for_engines(engines_after_pa11y_failure) == 90.0
