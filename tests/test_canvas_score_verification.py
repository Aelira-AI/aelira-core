"""Canvas scores/counts must describe the exact measured saved candidate."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.education.canvas_score_verification import (
    current_canvas_verification,
    measured_canvas_outcome,
    paired_canvas_outcome,
    store_canvas_verification,
)


def _file():
    return SimpleNamespace(
        id="canvas-file",
        content_source="page",
        file_name="Course page",
        content_body="<p>Original</p>",
        remediated_body="<p>Saved</p>",
        last_scan_id="source-scan",
        provider_metadata={},
        remediated_compliance_score=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
    )


def _publish(file, issues, outcome):
    file.remediated_compliance_score = outcome["score"]
    file.remediated_issues_fixed = outcome["fixed"]
    file.remediated_issues_remaining = outcome["remaining"] + outcome["introduced"]
    store_canvas_verification(file, issues, outcome)


def test_duplicate_rule_nodes_are_accumulated_and_regressions_retained():
    before = [
        {"id": "alt", "nodes": [{}]},
        {"id": "alt", "nodes": [{}, {}]},
        {"id": "label", "nodes": [{}]},
    ]
    after = {
        "passes": [{}],
        "violations": [{"id": "alt", "nodes": [{}]}, {"id": "new", "nodes": [{}, {}]}],
    }
    assert measured_canvas_outcome(after, before) == {
        "score": 33.3,
        "fixed": 3,
        "remaining": 1,
        "introduced": 2,
    }


def test_pair_rejects_source_finding_drift():
    with pytest.raises(ValueError, match="Source scan findings changed"):
        paired_canvas_outcome(
            {"passes": [{}], "violations": []},
            {"passes": [{}], "violations": []},
            [{"id": "image-alt", "nodes": [{}]}],
        )


@pytest.mark.parametrize(
    "results",
    [
        {},
        {"passes": [], "violations": []},
        {"passes": [{}], "violations": [{"id": "alt"}]},
    ],
)
@pytest.mark.asyncio
async def test_empty_or_malformed_rescan_never_measures_perfect_result(
    monkeypatch, results
):
    from src.jobs import canvas_content_job

    monkeypatch.setattr(
        canvas_content_job, "run_deterministic_axe", AsyncMock(return_value=results)
    )
    assert (
        await canvas_content_job._rescan_saved_content(
            "<p>Saved</p>", [], "<p>Source</p>"
        )
        is None
    )


@pytest.mark.asyncio
async def test_rescan_failure_leaves_measurement_unknown(monkeypatch):
    from src.jobs import canvas_content_job

    monkeypatch.setattr(
        canvas_content_job,
        "run_deterministic_axe",
        AsyncMock(side_effect=RuntimeError("Unavailable")),
    )
    assert (
        await canvas_content_job._rescan_saved_content(
            "<p>Saved</p>", [], "<p>Source</p>"
        )
        is None
    )


@pytest.mark.parametrize(
    "change", ["source", "candidate", "scan", "issues", "counts", "legacy", "rescan"]
)
def test_stale_or_missing_provenance_never_reuses_measured_counts(change):
    file = _file()
    issues = [{"id": "alt", "nodes": [{}, {}]}]
    outcome = {
        "score": 100.0,
        "source_score": 50.0,
        "fixed": 2,
        "remaining": 0,
        "introduced": 0,
    }
    _publish(file, issues, outcome)
    assert current_canvas_verification(file, issues) == outcome
    if change == "source":
        file.content_body += "changed"
    elif change == "candidate":
        file.remediated_body += "changed"
    elif change == "scan":
        file.last_scan_id = "different"
    elif change == "issues":
        issues[0]["nodes"].append({})
    elif change == "counts":
        file.remediated_issues_fixed = 999
    elif change == "rescan":
        file.needs_rescan = True
    else:
        file.provider_metadata = {}
    assert current_canvas_verification(file, issues) is None


@pytest.mark.parametrize("stale", [False, True])
def test_canvas_enqueue_requires_current_source_scan(stale):
    from src.jobs import canvas_content_job
    from src.services.job_enqueue_service import JobEnqueueError

    file = _file()
    file.needs_rescan = stale
    file.department_id = "dept"
    issues = [{"id": "alt", "nodes": [{}]}]
    db = MagicMock()
    query = db.query.return_value
    query.join.return_value = query
    query.filter.return_value = query
    query.one_or_none.return_value = (
        SimpleNamespace(),
        SimpleNamespace(issues=issues, compliance_score=50.0),
    )
    if stale:
        with pytest.raises(JobEnqueueError, match="canvas_content_rescan_required"):
            canvas_content_job._scan_evidence(db, file)
        db.query.assert_not_called()
    else:
        assert canvas_content_job._scan_evidence(db, file) == (issues, 50.0)


@pytest.mark.asyncio
async def test_legacy_stale_source_cannot_remediate_or_verify(monkeypatch):
    from src.education.canvas_content_scanner import CanvasContentScanner

    file = _file()
    file.needs_rescan = True
    db = MagicMock()
    scanner = CanvasContentScanner(AsyncMock(), db, "dept", "credential")
    axe = AsyncMock()
    monkeypatch.setattr(scanner, "_run_axe_scan", axe)
    result = await scanner.remediate_content_item(file)
    assert result["success"] is False
    assert result["verified"] is False
    assert result["fixed_count"] == 0
    assert result["error_code"] == "source_rescan_required"
    assert await scanner._verify_remediation(file, "<p>Candidate</p>", []) is None
    db.query.assert_not_called()
    db.commit.assert_not_called()
    axe.assert_not_awaited()


@pytest.mark.parametrize(
    "outcome",
    [
        None,
        {
            "score": 100.0,
            "source_score": 50.0,
            "fixed": 2,
            "remaining": 0,
            "introduced": 0,
        },
        {
            "score": 50.0,
            "source_score": 50.0,
            "fixed": 1,
            "remaining": 1,
            "introduced": 3,
        },
    ],
)
@pytest.mark.asyncio
async def test_diff_returns_measured_counts_or_unverified_source_counts(
    monkeypatch, outcome
):
    from src.api import canvas_content_routes

    file = _file()
    issues = [{"id": "alt", "nodes": [{}, {}]}]
    if outcome:
        _publish(file, issues, outcome)
    else:
        file.remediated_issues_fixed = 2  # Legacy optimistic data must not be trusted.
        file.remediated_issues_remaining = 0
        file.remediated_compliance_score = 100
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=issues
    )
    monkeypatch.setattr(
        canvas_content_routes, "_get_cloud_file_or_404", lambda *args: file
    )
    monkeypatch.setattr(canvas_content_routes, "require_feature", AsyncMock())
    response = await canvas_content_routes.get_content_diff(
        file.id, db, SimpleNamespace(department_id="dept")
    )
    assert response.verified == (outcome is not None)
    assert response.issues_fixed == (outcome["fixed"] if outcome else 0)
    assert response.issues_remaining == (
        outcome["remaining"] + outcome["introduced"] if outcome else 2
    )
    assert response.remediated_compliance_score == (
        outcome["score"] if outcome else None
    )
    assert response.issues[0].nodes_affected == 2


@pytest.mark.asyncio
async def test_legacy_rescan_measures_final_postgres_sanitized_candidate(monkeypatch):
    from src.education import canvas_content_scanner
    from src.education.remediation import html_remediator

    file = _file()
    file.content_body = "<p>Orig\x00inal</p><script>bad()</script>"
    issues = [{"id": "link-name", "nodes": [{}]}]
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=issues
    )
    scanner = canvas_content_scanner.CanvasContentScanner(
        AsyncMock(), db, "dept", "credential"
    )
    remediator = MagicMock()
    remediator.remediate.return_value = SimpleNamespace(
        success=True, output_file=None, fixed_count=99, manual_count=0, failed_count=0
    )
    monkeypatch.setattr(
        html_remediator, "HtmlRemediator", lambda *args, **kwargs: remediator
    )
    captured = {}

    async def rescan(body):
        if "body" not in captured:
            captured["body"] = body
            return {"passes": [{}], "violations": issues}
        captured["body"] = body
        return {"passes": [{}], "violations": []}

    monkeypatch.setattr(scanner, "_run_axe_scan", rescan)
    result = await scanner.remediate_content_item(file)
    assert result["verified"]
    assert result["fixed_count"] == 1
    assert "\x00" not in file.remediated_body
    assert "<script" not in file.remediated_body
    assert (
        canvas_content_scanner._unwrap_html_fragment(captured["body"])
        == file.remediated_body
    )
    assert current_canvas_verification(file, issues) is not None
