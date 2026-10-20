"""Equation publication gate: only failures that remediation introduces block.

The case tables here are shared by the queued job, the direct route and the
Brightspace publisher so all three are held to the same matrix.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from src.education.remediation.image_equation_gate import (
    ImageEquationMatterhornError,
    recorded_source_matterhorn_failures,
    require_image_equation_matterhorn_result,
)
from src.jobs import remediation_subprocess
from src.jobs.remediation_subprocess import SubprocessRemediationResult

PDFUA_IDENTIFIER_FAILURE = ("06-003", None)


def checkpoint(identifier: Any, status: str, page: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        name=f"Checkpoint {identifier}",
        status=SimpleNamespace(value=status),
        severity="error",
        details=f"{identifier} {status}",
        page_number=page,
    )


def matterhorn_result(*checkpoints: SimpleNamespace, **overrides: Any):
    statuses = [item.status.value for item in checkpoints]
    values: dict[str, Any] = {
        "checkpoints": list(checkpoints),
        "total": len(statuses),
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "warnings": statuses.count("warning"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Case(NamedTuple):
    id: str
    result: Any
    source_failures: Any
    code: str | None


GATE_ACCEPTED = [
    Case(
        "pre_existing_06_003",
        matterhorn_result(
            checkpoint("01-003", "pass"),
            checkpoint("06-003", "fail"),
        ),
        [PDFUA_IDENTIFIER_FAILURE],
        None,
    ),
    Case(
        "pre_existing_page_scoped",
        matterhorn_result(
            checkpoint("06-003", "fail"),
            checkpoint("13-004", "fail", 2),
            checkpoint("01-003", "warning"),
        ),
        [PDFUA_IDENTIFIER_FAILURE, ("13-004", 2)],
        None,
    ),
    Case(
        "pre_existing_repeated",
        matterhorn_result(
            checkpoint("13-004", "fail", 1),
            checkpoint("13-004", "fail", 1),
        ),
        [("13-004", 1), ("13-004", 1)],
        None,
    ),
    Case(
        "remediation_fixed_one_failure",
        matterhorn_result(checkpoint("06-003", "fail")),
        [PDFUA_IDENTIFIER_FAILURE, ("09-004", None)],
        None,
    ),
    Case(
        "clean_without_recorded_comparison",
        matterhorn_result(checkpoint("01-003", "pass")),
        None,
        None,
    ),
]

GATE_REFUSED = [
    Case(
        "introduced_beside_pre_existing",
        matterhorn_result(
            checkpoint("06-003", "fail"),
            checkpoint("09-004", "fail"),
        ),
        [PDFUA_IDENTIFIER_FAILURE],
        "matterhorn_result_disqualifying",
    ),
    Case(
        "introduced_on_another_page",
        matterhorn_result(checkpoint("13-004", "fail", 3)),
        [("13-004", 2)],
        "matterhorn_result_disqualifying",
    ),
    Case(
        "introduced_repeat_of_known_failure",
        matterhorn_result(
            checkpoint("13-004", "fail"),
            checkpoint("13-004", "fail"),
        ),
        [("13-004", None)],
        "matterhorn_result_disqualifying",
    ),
    Case(
        "source_was_clean",
        matterhorn_result(checkpoint("06-003", "fail")),
        [],
        "matterhorn_result_disqualifying",
    ),
    Case(
        "no_recorded_comparison_cannot_show_pre_existing",
        matterhorn_result(checkpoint("06-003", "fail")),
        None,
        "matterhorn_result_disqualifying",
    ),
    Case(
        "unavailable",
        SimpleNamespace(checkpoints=None, total=0, passed=0, failed=0, warnings=0),
        [PDFUA_IDENTIFIER_FAILURE],
        "matterhorn_result_unavailable",
    ),
    Case(
        "empty",
        matterhorn_result(),
        [PDFUA_IDENTIFIER_FAILURE],
        "matterhorn_result_unavailable",
    ),
    Case(
        "malformed_status",
        matterhorn_result(checkpoint("01-003", "unknown")),
        None,
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "inconsistent_total",
        matterhorn_result(checkpoint("01-003", "pass"), total=2),
        None,
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "inconsistent_failed_count",
        matterhorn_result(checkpoint("06-003", "fail"), failed=0, passed=1),
        [PDFUA_IDENTIFIER_FAILURE],
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "failed_checkpoint_without_identity",
        matterhorn_result(checkpoint(None, "fail")),
        [PDFUA_IDENTIFIER_FAILURE],
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "recorded_comparison_malformed",
        matterhorn_result(checkpoint("06-003", "fail")),
        ["06-003"],
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "recorded_comparison_not_a_list",
        matterhorn_result(checkpoint("06-003", "fail")),
        "06-003",
        "matterhorn_result_integrity_invalid",
    ),
    Case(
        "recorded_comparison_bad_page",
        matterhorn_result(checkpoint("06-003", "fail")),
        [("06-003", "1")],
        "matterhorn_result_integrity_invalid",
    ),
]


def publisher_params(cases: list[Case]) -> list[Any]:
    """Parameters for the publisher tests: (matterhorn result, source failures)."""
    return [
        pytest.param(case.result, case.source_failures, id=case.id) for case in cases
    ]


@pytest.mark.parametrize(("result", "source_failures"), publisher_params(GATE_ACCEPTED))
def test_gate_accepts_failures_the_source_already_had(result, source_failures):
    require_image_equation_matterhorn_result(result, source_failures)


@pytest.mark.parametrize(
    ("result", "source_failures", "code"),
    [pytest.param(c.result, c.source_failures, c.code, id=c.id) for c in GATE_REFUSED],
)
def test_gate_refuses_introduced_failures_and_bad_results(
    result, source_failures, code
):
    with pytest.raises(ImageEquationMatterhornError) as refused:
        require_image_equation_matterhorn_result(result, source_failures)
    assert str(refused.value) == code


def test_gate_accepts_recorded_comparison_after_json_round_trip():
    # The queued job receives the comparison from its child as JSON lists.
    result = matterhorn_result(checkpoint("06-003", "fail"))
    require_image_equation_matterhorn_result(result, [["06-003", None]])
    with pytest.raises(ImageEquationMatterhornError):
        require_image_equation_matterhorn_result(result, [["06-003", 1]])


def test_recorded_comparison_is_read_from_the_remediation_result():
    assert recorded_source_matterhorn_failures(SimpleNamespace()) is None
    recorded = [PDFUA_IDENTIFIER_FAILURE]
    assert (
        recorded_source_matterhorn_failures(
            SimpleNamespace(source_matterhorn_failures=recorded)
        )
        is recorded
    )


def _child_envelope(tmp_path, monkeypatch, **recorded: Any) -> dict[str, Any]:
    """Run the real child serializer, as queued jobs do, and cross JSON."""
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    source.write_bytes(b"synthetic source")
    output.write_bytes(b"synthetic output")
    result = SimpleNamespace(
        success=True,
        output_file=str(output),
        total_issues=1,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
        skipped_count=0,
        original_compliance_score=None,
        remediated_compliance_score=None,
        improvement=None,
        duration_seconds=1.0,
        verification_passed=True,
        fixed_issues=[],
        manual_issues=[],
        failed_issues=[],
        **recorded,
    )
    monkeypatch.setattr(
        remediation_subprocess,
        "_build_remediator",
        lambda *_args, **_kwargs: SimpleNamespace(remediate=lambda: result),
    )
    envelope = remediation_subprocess._run_child(
        {
            "source_path": str(source),
            "work_dir": str(tmp_path),
            "scan_type": "PDF",
            "issues": [],
            "options": {},
        }
    )
    return json.loads(json.dumps(envelope))


def test_recorded_comparison_reaches_the_gate_through_the_child_process(
    tmp_path, monkeypatch
):
    # Queued production jobs run the remediator in a child process, so the
    # comparison only reaches the gate through the child envelope.
    envelope = _child_envelope(
        tmp_path, monkeypatch, source_matterhorn_failures=[("09-004", None)]
    )
    assert envelope.get("source_matterhorn_failures") == [["09-004", None]]

    envelope.pop("output_file")  # the parent claims the output separately
    parent_result = SubprocessRemediationResult(envelope)
    recorded = recorded_source_matterhorn_failures(parent_result)

    inherited = matterhorn_result(
        checkpoint("01-003", "pass"), checkpoint("09-004", "fail")
    )
    require_image_equation_matterhorn_result(inherited, recorded)

    introduced = matterhorn_result(
        checkpoint("09-004", "fail"), checkpoint("06-003", "fail")
    )
    with pytest.raises(ImageEquationMatterhornError) as refused:
        require_image_equation_matterhorn_result(introduced, recorded)
    assert str(refused.value) == "matterhorn_result_disqualifying"


def test_child_without_a_recorded_comparison_cannot_show_pre_existing_failures(
    tmp_path, monkeypatch
):
    envelope = _child_envelope(tmp_path, monkeypatch)
    assert envelope["source_matterhorn_failures"] is None

    envelope.pop("output_file")
    recorded = recorded_source_matterhorn_failures(
        SubprocessRemediationResult(envelope)
    )
    with pytest.raises(ImageEquationMatterhornError) as refused:
        require_image_equation_matterhorn_result(
            matterhorn_result(checkpoint("09-004", "fail")), recorded
        )
    assert str(refused.value) == "matterhorn_result_disqualifying"
