"""Retain the actual secondary runner identity in merged accessibility evidence."""

from src.scanners.engine_evidence import estimate_coverage_for_engines
from src.scanners.result_merger import ResultMerger


def test_htmlcs_results_keep_sourced_issue_attribution():
    source = {
        "engine": "htmlcs",
        "issues": [
            {
                "selector": "#chart",
                "code": "image-alt",
                "message": "Synthetic missing alternative",
                "type": "error",
                "context": '<img id="chart">',
            }
        ],
    }
    axe = {
        "violations": [
            {
                "id": "image-alt",
                "help": "Missing alternative",
                "nodes": [{"target": ["#chart"], "html": '<img id="chart">'}],
            }
        ]
    }
    result = ResultMerger.merge_axe_and_pa11y_results(axe, source)
    assert result["total_issues"] == 1
    assert result["issues"][0]["detected_by"] == ["axe-core", "htmlcs"]
    assert "pa11y" not in result["engines_used"]
    assert result["engine_counts"] == {"axe-core": 0, "htmlcs": 0, "both": 1}
    assert result["issues"][0]["message"] == source["issues"][0]["message"]
    assert estimate_coverage_for_engines(["axe-core", "htmlcs"]) == 95.0


def test_legacy_pa11y_results_retain_their_attribution():
    result = ResultMerger.merge_axe_and_pa11y_results(
        {},
        {
            "issues": [
                {
                    "selector": "#field",
                    "code": "label",
                    "message": "Missing label",
                    "type": "error",
                }
            ]
        },
    )
    assert result["issues"][0]["detected_by"] == ["pa11y"]
    assert result["engine_counts"]["pa11y"] == 1
