"""Severity must be identical for identical input, every time.

This is the regression test that would have caught the 2026-08-14 finding:
``color-contrast`` alternating between Critical and High across five identical
runs of the production classifier, because severity was being sampled from an
LLM rather than computed.

The pure-function tests below are the real guard. They are cheap, they need no
network, and they fail loudly if anyone reintroduces a non-deterministic path.
"""

from __future__ import annotations

import pytest

from src.ai.severity_rules import (
    DEFAULT_SEVERITY,
    SEVERITY_BY_IMPACT,
    SEVERITY_BY_RULE,
    resolve_severity,
    severity_for,
)

RUNS = 25

#: The corpus that reproduced the original defect, plus one case per impact level.
CORPUS = [
    ("color-contrast", "serious"),
    ("image-alt", "critical"),
    ("heading-order", "moderate"),
    ("label", "critical"),
    ("region", "minor"),
    ("link-name", "serious"),
]


@pytest.mark.parametrize("rule_id,impact", CORPUS)
def test_severity_is_stable_across_repeated_calls(rule_id: str, impact: str) -> None:
    """The same violation resolves to the same severity on every call."""
    results = {severity_for(rule_id, impact) for _ in range(RUNS)}
    assert len(results) == 1, (
        f"{rule_id}/{impact} produced {len(results)} distinct severities "
        f"across {RUNS} runs: {sorted(results)}"
    )


def test_color_contrast_does_not_drift() -> None:
    """Pin the rule that exposed the defect.

    It alternated Critical/High in production. It is High, and it stays High
    unless someone changes it deliberately and updates this test.
    """
    assert severity_for("color-contrast", "serious") == "High"


def test_rule_table_takes_precedence_over_impact() -> None:
    """An explicit rule override beats whatever the scanner rated the impact."""
    resolution = resolve_severity("image-alt", "minor")
    assert resolution.severity == "Critical"
    assert resolution.source == "rule"


def test_falls_back_to_impact_when_rule_unknown() -> None:
    resolution = resolve_severity("some-rule-we-have-never-seen", "serious")
    assert resolution.severity == "High"
    assert resolution.source == "impact"


def test_falls_back_to_default_when_both_unknown() -> None:
    resolution = resolve_severity("unknown-rule", "unknown-impact")
    assert resolution.severity == DEFAULT_SEVERITY
    assert resolution.source == "default"


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_missing_inputs_degrade_rather_than_raise(missing) -> None:
    """A scanner that omits rule_id or impact must not crash a scan."""
    assert resolve_severity(missing, "critical").severity == "Critical"
    assert resolve_severity("image-alt", missing).severity == "Critical"
    assert resolve_severity(missing, missing).severity == DEFAULT_SEVERITY


@pytest.mark.parametrize(
    "rule_id,impact",
    [
        ("IMAGE-ALT", "CRITICAL"),
        ("  color-contrast  ", " Serious "),
        ("Label", "critical"),
    ],
)
def test_matching_is_case_and_whitespace_insensitive(rule_id: str, impact: str) -> None:
    assert severity_for(rule_id, impact) == severity_for(
        rule_id.strip().lower(), impact.strip().lower()
    )


def test_every_table_value_is_a_valid_severity() -> None:
    """Guards against a typo silently entering the tables."""
    valid = {"Critical", "High", "Medium", "Low"}
    assert set(SEVERITY_BY_RULE.values()) <= valid
    assert set(SEVERITY_BY_IMPACT.values()) <= valid
    assert DEFAULT_SEVERITY in valid


def test_rule_keys_are_normalised() -> None:
    """Keys must be lowercase, or lookups silently miss."""
    assert all(k == k.strip().lower() for k in SEVERITY_BY_RULE)
    assert all(k == k.strip().lower() for k in SEVERITY_BY_IMPACT)


def test_resolution_is_a_pure_function_of_its_arguments() -> None:
    """No hidden state: interleaving different inputs changes nothing."""
    first = [severity_for(r, i) for r, i in CORPUS]
    for _ in range(5):
        for other in ("label", "region", "pdf-untagged"):
            severity_for(other, "critical")
    assert [severity_for(r, i) for r, i in CORPUS] == first
