"""Evidence metadata derived from accessibility engines that completed."""

from collections.abc import Collection


def should_run_pa11y(mode: str) -> bool:
    """Return whether the requested scan mode includes Pa11y."""

    return mode in {"comprehensive", "deep"}


def estimate_coverage_for_engines(engines_used: Collection[str]) -> float:
    """Return the bounded product estimate for engines with persisted results."""

    completed = set(engines_used)
    if {"axe-core", "pa11y", "ai-vision"}.issubset(completed):
        return 98.0
    if {"axe-core", "pa11y"}.issubset(completed):
        return 95.0
    return 90.0
