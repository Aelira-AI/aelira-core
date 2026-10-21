"""The printed-equation second reading, as saved in a PDF and its contract.

The full-flow tests use the real scanner, remediator, second reader and
association writer. Only the visual round-trip verifier is replaced, because it
needs a browser.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.printed_equation_policy import (
    PRINTED_VERIFIER_POLICY_SHA256,
    PRINTED_VERIFIER_PROMPT_SHA256,
)
from src.education.remediation.math_fixer import MathFixer


@pytest.fixture
def install_verifier(monkeypatch: pytest.MonkeyPatch):
    """Replace only the browser round trip; the second reading stays real."""
    from src.education.remediation import pdf_remediator
    from tests.test_equation_alt_text_coordination import _Verifier as BrowserStub

    def install() -> None:
        class StubbedMathFixer(MathFixer):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("equation_verifier", BrowserStub(True))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(pdf_remediator, "MathFixer", StubbedMathFixer)

    return install


def test_an_agreeing_second_reading_with_other_braces_is_saved_with_its_proof(
    tmp_path, install_verifier
):
    from tests.test_equation_alt_text_coordination import (
        EQUATION_ALT,
        EQUATION_INDEX,
        _fixed,
        _issue_ids,
        _Provider,
        _run,
        _structure,
        _write_untagged_source,
    )

    install_verifier()
    source = tmp_path / "source.pdf"
    _write_untagged_source(source)
    provider = _Provider(second_latex_by_width={120: "x^2 + 1 = 0"})

    remediator, result = _run(source, tmp_path, provider)
    try:
        assert result.success, result.error_message
        ids = _issue_ids(remediator, EQUATION_INDEX)
        fix = _fixed(result, ids[IMAGE_EQUATION_ISSUE_TYPE])
        kinds = [
            item.evidence_kind
            for item in fix.visual_semantic_contract.verification_evidence
        ]
        assert kinds == [
            "printed_equation_roundtrip_v1",
            "printed_equation_consensus_v1",
            "standalone_formula_saved_v1",
        ]
        consensus = fix.visual_semantic_contract.verification_evidence[1]
        assert consensus.verifier_prompt_sha256 == PRINTED_VERIFIER_PROMPT_SHA256
        assert consensus.policy_sha256 == PRINTED_VERIFIER_POLICY_SHA256
        assert consensus.primary_latex_sha256 != consensus.verifier_latex_sha256
        assert consensus.primary_mathml_sha256 == consensus.verifier_mathml_sha256
        assert consensus.primary_provider == "test-provider"
        assert consensus.verifier_model == "test-model"
        formulas = [
            alt for role, alt in _structure(result.output_file) if role == "/Formula"
        ]
        assert formulas == [EQUATION_ALT]
    finally:
        result.close_output_claim()
