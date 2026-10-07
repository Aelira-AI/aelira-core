"""AI and color routes must preserve success but never reflect internal errors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api import main
from src.api.education import accessibility_routes


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


@pytest.fixture
def ai(monkeypatch):
    client = SimpleNamespace(
        classify_severity=AsyncMock(return_value={"provider": "test-provider"}),
        classify_severity_with_rag=AsyncMock(
            return_value={"severity": "High", "provider": "test-provider"}
        ),
        generate_code_fix=AsyncMock(
            return_value={"fixed_code": "<button>Save</button>"}
        ),
    )
    monkeypatch.setattr(main, "workspace_provider_runtime", lambda _workspace: object())
    monkeypatch.setattr(
        main,
        "accessibility_ai_client",
        SimpleNamespace(bind_provider_manager=lambda _runtime: client),
    )
    return client


@pytest.mark.asyncio
async def test_ai_test_success_and_private_failure(ai, caplog):
    assert (await main.test_ai((None, "user", "department")))[
        "message"
    ] == "AI test successful"
    ai.classify_severity.side_effect = RuntimeError(
        "private-token /storage/private.txt"
    )
    result = await main.test_ai((None, "user", "department"))
    assert result["message"] == "AI test failed"
    assert "private-token" not in str(result) + caplog.text
    assert "/storage/" not in str(result) + caplog.text


@pytest.mark.asyncio
async def test_batch_success_and_private_failure(ai, caplog):
    request = main.BatchAnalysisRequest(
        violations=[
            main.BatchAnalysisViolation(
                id="one",
                rule_id="button-name",
                impact="serious",
                html_snippet="<button></button>",
                selector="button",
            )
        ],
        generate_fixes=False,
    )
    assert (await main.batch_analyze_violations(request, (None, "user", "department")))[
        "results"
    ][0]["classification"]["provider"] == "test-provider"
    ai.classify_severity_with_rag.side_effect = RuntimeError(
        "private-token /storage/private.txt"
    )
    result = await main.batch_analyze_violations(request, (None, "user", "department"))
    assert "error" in result["results"][0]
    assert "private-token" not in str(result) + caplog.text
    assert "/storage/" not in str(result) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True])
async def test_failed_image_analysis_requires_human_review(ai, monkeypatch, batch):
    monkeypatch.setattr(main, "generate_image_alt_text", AsyncMock(return_value=None))
    fields = dict(
        rule_id="image-alt",
        impact="serious",
        html_snippet='<img src="http://127.0.0.1/private">',
        selector="img",
    )
    if batch:
        request = main.BatchAnalysisRequest(
            violations=[main.BatchAnalysisViolation(id="one", **fields)],
            generate_fixes=True,
        )
        result = (
            await main.batch_analyze_violations(request, (None, "user", "department"))
        )["results"][0]
    else:
        result = await main.analyze_violation(
            main.ViolationAnalysisRequest(**fields), (None, "user", "department")
        )
    assert result["fix"]["human_review_required"] is True
    assert result["fix"]["fix_recommendation"] == ""
    ai.generate_code_fix.assert_not_awaited()


@pytest.mark.asyncio
async def test_color_success_and_failure_does_not_reflect_exception(
    monkeypatch, caplog
):
    from src.education.color_blindness_simulator import ColorBlindnessSimulator

    request = accessibility_routes.CVDSimulateRequest(
        color="#ffffff", cvd_type="protanopia"
    )
    assert (await accessibility_routes.simulate_cvd_color(request)).success is True
    monkeypatch.setattr(
        ColorBlindnessSimulator,
        "simulate_color_blindness",
        MagicMock(side_effect=RuntimeError("private-token /storage/private.txt")),
    )
    result = await accessibility_routes.simulate_cvd_color(request)
    assert result.success is False
    assert "private-token" not in str(result) + caplog.text
    assert "/storage/" not in str(result) + caplog.text
