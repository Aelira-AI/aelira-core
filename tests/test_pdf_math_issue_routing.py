import pytest
from types import SimpleNamespace

from src.education.remediation.base import (
    BaseRemediator,
    IssueCategory,
    RemediationConfig,
)


class _ProbeRemediator(BaseRemediator):
    SUPPORTED_EXTENSIONS = [".pdf"]

    def _load_document(self):
        raise NotImplementedError

    def _save_document(self, document):
        raise NotImplementedError

    def can_auto_fix(self, issue):
        return False

    def apply_fix(self, issue, document, fix_content):
        return False


OCCURRENCE = {
    "page_number": 2,
    "image_xref": 41,
    "image_index": 3,
    "occurrence_ordinal": 1,
    "bbox": [10.0, 20.0, 110.0, 70.0],
    "occurrence_id": "imgocc-v1-example",
}


def _raw(issue_type, *, nested=False):
    issue = {
        "id": "math-1",
        "severity": "high",
        "message": "Equation image requires accessible math",
        "rule": "WCAG 1.1.1",
        **OCCURRENCE,
    }
    if nested:
        issue["metadata"] = {"issue_type": issue_type}
    else:
        issue["issue_type"] = issue_type
    return issue


def test_math_contract_is_immutable_and_canonical():
    from src.education.math_contracts import (
        CONCRETE_MATH_ISSUE_TYPES,
        DOCUMENT_WIDE_MATH_ISSUE_TYPES,
        IMAGE_EQUATION_ISSUE_TYPE,
        MATH_ISSUE_TYPES,
    )

    assert IMAGE_EQUATION_ISSUE_TYPE == "image_equation_inaccessible"
    assert isinstance(MATH_ISSUE_TYPES, frozenset)
    assert CONCRETE_MATH_ISSUE_TYPES == frozenset({IMAGE_EQUATION_ISSUE_TYPE})
    assert CONCRETE_MATH_ISSUE_TYPES.isdisjoint(DOCUMENT_WIDE_MATH_ISSUE_TYPES)


@pytest.mark.parametrize("nested", [False, True])
def test_api_and_base_normalizers_route_candidate_once_and_preserve_identity(
    tmp_path, nested
):
    from src.api.education.remediation_routes import _normalize_issues_for_remediation
    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE

    raw = _raw(IMAGE_EQUATION_ISSUE_TYPE, nested=nested)
    api_issue = _normalize_issues_for_remediation([raw])[0]

    assert api_issue["category"] == IssueCategory.STRUCTURE.value
    assert api_issue["metadata"]["issue_type"] == IMAGE_EQUATION_ISSUE_TYPE
    assert api_issue["metadata"]["rule"] == "WCAG 1.1.1"
    for key, value in OCCURRENCE.items():
        assert api_issue["metadata"][key] == value

    pdf = tmp_path / "probe.pdf"
    pdf.write_bytes(b"%PDF-probe")
    remediator = _ProbeRemediator(
        str(pdf),
        [api_issue],
        RemediationConfig(allow_legacy_nested_ai=False),
    )
    normalized = remediator.issues[0]
    assert normalized.category is IssueCategory.STRUCTURE
    assert normalized.metadata["issue_type"] == IMAGE_EQUATION_ISSUE_TYPE
    assert normalized.metadata["rule"] == "WCAG 1.1.1"
    for key, value in OCCURRENCE.items():
        assert normalized.metadata[key] == value


@pytest.mark.parametrize(
    "issue_type",
    [
        "latex_equations_inaccessible",
        "math_content_accessibility",
        "raw_latex_code",
        "mathml_recommendation",
    ],
)
def test_document_wide_math_warnings_route_to_structure_but_are_not_concrete(
    issue_type,
):
    from src.api.education.remediation_routes import _infer_category
    from src.education.math_contracts import is_concrete_math_issue_type

    assert _infer_category(_raw(issue_type)) == IssueCategory.STRUCTURE.value
    assert not is_concrete_math_issue_type(issue_type)


def test_pdf_specialist_routing_imports_the_central_concrete_contract():
    from src.education.math_contracts import CONCRETE_MATH_ISSUE_TYPES
    from src.education.remediation import pdf_remediator

    assert pdf_remediator.MATH_SPECIALIST_ISSUE_TYPES is CONCRETE_MATH_ISSUE_TYPES


def test_image_candidate_fails_closed_until_recognition_pipeline_exists():
    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
    from src.education.remediation.math_fixer import MathFixer

    class _StructTree:
        def add_formula(self, **kwargs):
            raise AssertionError("candidate must not mutate structure")

    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=_StructTree(),
    )
    result = fixer._fix_math_issue(
        SimpleNamespace(
            metadata={
                "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
                "page_number": 1,
                "occurrence_id": "imgocc-v1-example",
            }
        )
    )

    assert not result.success
    assert result.error == "alt_text_client_unavailable"


def test_image_equation_disables_legacy_manager_alias_and_keeps_one_manual(tmp_path):
    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
    from src.education.remediation.pdf_remediator import PdfRemediator

    class Manager:
        def __init__(self):
            self.calls = 0

        def recognize_image_equation(self, **_kwargs):
            self.calls += 1
            raise AssertionError("legacy manager must not recognize image equations")

    path = tmp_path / "equation.pdf"
    path.write_bytes(b"%PDF-probe")
    manager = Manager()
    remediator = PdfRemediator(
        str(path),
        [_raw(IMAGE_EQUATION_ISSUE_TYPE)],
        RemediationConfig(allow_legacy_nested_ai=True),
        manager,
    )
    remediator._pikepdf_doc = SimpleNamespace(pages=[object()])
    remediator._pdf = SimpleNamespace()
    remediator._struct_tree = SimpleNamespace()

    remediator._run_specialist("math", remediator.issues, object())

    assert remediator.config.allow_legacy_nested_ai is False
    assert remediator.alt_text_client is None
    assert manager.calls == 0
    assert remediator.result.manual_count == 1
    assert len(remediator.result.manual_issues) == 1
    assert remediator.result.manual_issues[0].reason == "alt_text_client_unavailable"


def test_image_equation_accepts_only_explicit_alt_text_purpose_client(tmp_path):
    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE

    path = tmp_path / "equation.pdf"
    path.write_bytes(b"%PDF-probe")
    explicit = SimpleNamespace(purpose="alt_text")
    manager = SimpleNamespace(purpose="remediation")

    allowed = _ProbeRemediator(
        str(path),
        [_raw(IMAGE_EQUATION_ISSUE_TYPE)],
        RemediationConfig(allow_legacy_nested_ai=True),
        manager,
        alt_text_client=explicit,
    )
    rejected = _ProbeRemediator(
        str(path),
        [_raw(IMAGE_EQUATION_ISSUE_TYPE)],
        RemediationConfig(allow_legacy_nested_ai=True),
        manager,
        alt_text_client=manager,
    )

    assert allowed.config.allow_legacy_nested_ai is False
    assert (
        allowed.ai_client is manager
    )  # non-equation legacy behavior remains available
    assert allowed.alt_text_client is explicit
    assert rejected.alt_text_client is None
