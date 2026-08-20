from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.education.remediation.base import (
    BaseRemediator,
    IssueCategory,
    RemediationConfig,
    RemediationIssue,
    classify_issue_category,
    materialize_manual_issues,
)

ALT_ALIASES = (
    "alt_text",
    "alternative_text",
    "image",
    "image_alt",
    "image_alt_text",
    "image_description",
    "image_of_text",
    "missing_alt_text",
    "missing_figure_caption",
    "missing_image_description",
    "area_alt",
    "figure_alt",
    "input_image_alt",
    "object_alt",
    "role_img_alt",
    "svg_img_alt",
)

CHART_ALIASES = (
    "chart",
    "chart_alt_text",
    "chart_description",
    "missing_chart_description",
)


class PurposeProbeRemediator(BaseRemediator):
    SUPPORTED_EXTENSIONS = [".txt"]
    AUTO_FIXABLE_CATEGORIES = list(IssueCategory)

    def _load_document(self):
        return object()

    def _save_document(self, document):
        return self.file_path

    def can_auto_fix(self, issue):
        return True

    def apply_fix(self, issue, document, fix_content):
        return True

    def _get_ai_generated_fix(self, issue, document, *, client):
        self.result.ai_calls_made += 1
        return client.generate(issue.category.value)


class RecordingClient:
    def __init__(self, prefix="generated", *, fail=False):
        self.prefix = prefix
        self.fail = fail
        self.categories = []

    def generate(self, category):
        self.categories.append(category)
        if self.fail:
            raise RuntimeError("provider failed")
        return f"{self.prefix}:{category}"


@pytest.fixture
def probe_path(tmp_path):
    path = tmp_path / "probe.txt"
    path.write_text("probe")
    return path


def issue(category):
    return RemediationIssue(
        id=f"issue-{category.value}",
        category=category,
        severity="medium",
        description=category.value,
    )


def test_base_routes_alt_and_chart_only_to_alt_client(probe_path):
    remediation = RecordingClient("remediation")
    alt = RecordingClient("alt")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
        alt_text_client=alt,
    )

    assert (
        remediator._generate_fix(issue(IssueCategory.ALT_TEXT), object())
        == "alt:alt_text"
    )
    assert remediator._generate_fix(issue(IssueCategory.CHART), object()) == "alt:chart"
    assert (
        remediator._generate_fix(issue(IssueCategory.LINK), object())
        == "remediation:link"
    )
    assert remediation.categories == ["link"]
    assert alt.categories == ["alt_text", "chart"]


def test_authoritative_mode_never_aliases_remediation_client_for_alt(probe_path):
    remediation = RecordingClient("remediation")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False, fix_alt_text=True),
        remediation,
    )

    remediator._process_issue(issue(IssueCategory.ALT_TEXT), object())

    assert remediation.categories == []
    assert remediator.result.manual_count == 1
    assert len(remediator.result.manual_issues) == 1
    assert remediator.result.failed_count == 0


def test_failed_selected_client_produces_exactly_one_manual_issue(probe_path):
    alt = RecordingClient("alt", fail=True)
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False, fix_alt_text=True),
        None,
        alt_text_client=alt,
    )

    remediator._process_issue(issue(IssueCategory.ALT_TEXT), object())

    assert alt.categories == ["alt_text"]
    assert remediator.result.ai_calls_made == 1
    assert remediator.result.manual_count == 1
    assert len(remediator.result.manual_issues) == 1
    assert remediator.result.failed_count == 0


def test_explicit_legacy_mode_may_alias_remediation_client_for_alt(probe_path):
    remediation = RecordingClient("legacy")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=True),
        remediation,
    )

    assert remediator.alt_text_client is remediation
    assert (
        remediator._generate_fix(issue(IssueCategory.ALT_TEXT), object())
        == "legacy:alt_text"
    )


@pytest.mark.parametrize(
    ("alias", "expected_category"),
    [
        *[(alias, IssueCategory.ALT_TEXT) for alias in ALT_ALIASES],
        *[(alias, IssueCategory.CHART) for alias in CHART_ALIASES],
    ],
)
def test_canonical_visual_aliases_map_and_route_only_to_alt_client(
    probe_path, alias, expected_category
):
    remediation = RecordingClient("remediation")
    alt = RecordingClient("alt")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
        alt_text_client=alt,
    )

    category = remediator._map_category(alias)
    assert category is expected_category
    assert (
        remediator._generate_fix(issue(category), object()) == f"alt:{category.value}"
    )
    assert remediation.categories == []
    assert alt.categories == [category.value]


@pytest.mark.parametrize("alias", ["heading", "image_link", "chartreuse"])
def test_non_visual_alias_controls_never_route_to_alt_client(probe_path, alias):
    remediation = RecordingClient("remediation")
    alt = RecordingClient("alt")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
        alt_text_client=alt,
    )

    category = remediator._map_category(alias)
    assert category not in {IssueCategory.ALT_TEXT, IssueCategory.CHART}
    remediator._generate_fix(issue(category), object())
    assert alt.categories == []


def test_worker_partition_uses_the_same_visual_aliases_and_negative_controls():
    from src.jobs.remediation_job import _partition_authoritative_document_issues

    visual = [{"type": alias} for alias in (*ALT_ALIASES, *CHART_ALIASES)]
    non_visual = [{"type": alias} for alias in ("heading", "image_link", "chartreuse")]

    automatic, manual = _partition_authoritative_document_issues(visual + non_visual)

    assert manual == visual
    assert automatic == non_visual


@pytest.mark.parametrize(
    "raw_issue",
    [
        {"category": "heading", "type": "missing-alt-text"},
        {"category": "structure", "metadata": {"axe_rule_id": "image-alt"}},
        {"rule": "table", "metadata": {"issue_type": "chart-description"}},
        {"metadata": {"category": "structure", "rule_id": "svg-img-alt"}},
    ],
)
def test_visual_category_wins_every_field_conflict_and_routes_only_to_alt(
    probe_path, raw_issue
):
    remediation = RecordingClient("remediation")
    alt = RecordingClient("alt")
    classification = classify_issue_category(raw_issue, authoritative=True)
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [raw_issue],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
        alt_text_client=alt,
    )

    assert classification.category in {IssueCategory.ALT_TEXT, IssueCategory.CHART}
    assert classification.manual_reason is None
    normalized_issue = remediator.issues[0]
    remediator._generate_fix(normalized_issue, object())
    assert remediation.categories == []
    assert alt.categories == [normalized_issue.category.value]


def test_authoritative_conflicting_nonvisual_categories_fail_closed_without_clients(
    probe_path,
):
    remediation = RecordingClient("remediation")
    alt = RecordingClient("alt")
    raw_issue = {"category": "heading", "metadata": {"rule_id": "table-header"}}
    classification = classify_issue_category(raw_issue, authoritative=True)
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [raw_issue],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
        alt_text_client=alt,
    )

    remediator._process_issue(remediator.issues[0], object())

    assert classification.category is IssueCategory.OTHER
    assert classification.manual_reason == "conflicting_issue_categories"
    assert remediation.categories == []
    assert alt.categories == []
    assert remediator.result.manual_count == 1


def test_authoritative_ambiguous_unknown_category_fails_closed(probe_path):
    remediation = RecordingClient("remediation")
    raw_issue = {"category": "vendor_magic", "metadata": {"type": "vendor_other"}}
    classification = classify_issue_category(raw_issue, authoritative=True)
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [raw_issue],
        RemediationConfig(allow_legacy_nested_ai=False),
        remediation,
    )

    remediator._process_issue(remediator.issues[0], object())

    assert classification.manual_reason == "ambiguous_issue_category"
    assert remediation.categories == []
    assert remediator.result.manual_count == 1


def test_authoritative_single_known_category_is_unchanged(probe_path):
    classification = classify_issue_category(
        {"metadata": {"axe_id": "heading"}}, authoritative=True
    )
    assert classification.category is IssueCategory.HEADING
    assert classification.manual_reason is None


def test_worker_partition_fails_closed_for_conflicts_but_visual_purpose_wins():
    from src.jobs.remediation_job import _partition_authoritative_document_issues

    visual_conflict = {"id": "visual", "category": "structure", "type": "image-alt"}
    nonvisual_conflict = {
        "id": "conflict",
        "category": "heading",
        "metadata": {"rule": "table"},
    }
    ordinary = {"id": "ordinary", "metadata": {"issue_type": "heading"}}

    automatic, manual = _partition_authoritative_document_issues(
        [visual_conflict, nonvisual_conflict, ordinary]
    )

    assert automatic == [ordinary]
    assert manual == [visual_conflict, nonvisual_conflict]


def test_partitioned_manual_records_follow_issue_node_count_without_deduping_ids():
    raw = [
        {
            "id": "duplicate",
            "category": "missing_alt_text",
            "description": "First image group",
            "nodes": [{"target": ["#one"]}, {"target": ["#two"]}],
        },
        {
            "id": "duplicate",
            "metadata": {"axe_rule_id": "image-alt"},
            "description": "Second image",
        },
    ]

    records = materialize_manual_issues(
        raw, reason="alt_text_client_unavailable", purpose="alt_text"
    )

    assert len(records) == 3
    assert [record.issue_id for record in records] == [
        "duplicate:node:0",
        "duplicate:node:1",
        "duplicate",
    ]
    assert all(record.category is IssueCategory.ALT_TEXT for record in records)
    assert all(record.reason == "alt_text_client_unavailable" for record in records)
    assert [record.metadata["node_index"] for record in records] == [0, 1, 0]
    assert all(record.metadata["purpose"] == "alt_text" for record in records)


@pytest.mark.parametrize(
    ("module_name", "class_name", "extension"),
    [
        ("src.education.remediation.pdf_remediator", "PdfRemediator", ".pdf"),
        ("src.education.remediation.docx_remediator", "DocxRemediator", ".docx"),
        ("src.education.remediation.pptx_remediator", "PptxRemediator", ".pptx"),
        ("src.education.remediation.xlsx_remediator", "XlsxRemediator", ".xlsx"),
    ],
)
@pytest.mark.parametrize(
    ("suggestion_key", "suggestion_location"),
    [
        ("suggested_alt_text", "metadata"),
        ("generated_alt_text", "metadata"),
        ("fix_suggestion", "issue"),
    ],
)
def test_document_alt_suggestions_are_legacy_only(
    tmp_path, module_name, class_name, extension, suggestion_key, suggestion_location
):
    import importlib

    remediator_class = getattr(importlib.import_module(module_name), class_name)
    path = tmp_path / f"document{extension}"
    path.write_bytes(b"placeholder")
    kwargs = {
        "category": IssueCategory.ALT_TEXT,
        "severity": "high",
        "description": "missing alt",
    }
    if suggestion_location == "metadata":
        kwargs["metadata"] = {suggestion_key: "unreviewed scanner output"}
    else:
        kwargs[suggestion_key] = "unreviewed scanner output"
    alt_issue = RemediationIssue(**kwargs)

    authoritative = remediator_class(
        str(path), [], RemediationConfig(allow_legacy_nested_ai=False)
    )
    legacy = remediator_class(
        str(path), [], RemediationConfig(allow_legacy_nested_ai=True)
    )

    assert authoritative._get_rule_based_fix(alt_issue, None) is None
    assert legacy._get_rule_based_fix(alt_issue, None) == "unreviewed scanner output"


def test_explicitly_disabled_alt_never_uses_legacy_aliased_manager(probe_path):
    manager = RecordingClient("manager")
    remediator = PurposeProbeRemediator(
        str(probe_path),
        [],
        RemediationConfig(use_ai=True, fix_alt_text=False, allow_legacy_nested_ai=True),
        manager,
    )

    remediator._process_issue(issue(IssueCategory.ALT_TEXT), object())

    assert manager.categories == []
    assert remediator.result.manual_count == 1


def test_authoritative_docx_rejects_unreviewed_scanner_alt_suggestions(tmp_path):
    pytest.importorskip("docx")
    from docx import Document
    from src.education.remediation.docx_remediator import DocxRemediator

    path = tmp_path / "document.docx"
    Document().save(path)
    remediator = DocxRemediator(
        str(path),
        [],
        RemediationConfig(allow_legacy_nested_ai=False),
        None,
    )
    alt_issue = remediator._normalize_issues(
        [
            {
                "id": "alt-1",
                "type": "alt_text",
                "severity": "high",
                "description": "missing alt",
                "suggested_alt_text": "unreviewed scanner output",
                "fix_suggestion": "another unreviewed output",
            }
        ]
    )[0]

    assert remediator._get_rule_based_fix(alt_issue, None) is None


@pytest.mark.parametrize("scan_type", ["PDF", "DOCX", "PPTX", "XLSX"])
def test_worker_factory_forwards_distinct_client_identities(scan_type):
    from src.jobs.remediation_job import _get_remediator_for_scan_type

    remediation = object()
    alt = object()
    constructor = MagicMock(return_value=object())
    module_by_type = {
        "PDF": "src.education.remediation.pdf_remediator.PdfRemediator",
        "DOCX": "src.education.remediation.docx_remediator.DocxRemediator",
        "PPTX": "src.education.remediation.pptx_remediator.PptxRemediator",
        "XLSX": "src.education.remediation.xlsx_remediator.XlsxRemediator",
    }
    with patch(module_by_type[scan_type], constructor):
        _get_remediator_for_scan_type(
            scan_type,
            "/tmp/file",
            [],
            True,
            ai_client=remediation,
            alt_text_client=alt,
            allow_legacy_nested_ai=False,
        )

    kwargs = constructor.call_args.kwargs
    assert kwargs["ai_client"] is remediation
    assert kwargs["alt_text_client"] is alt
    assert kwargs["config"].use_ai is True
    assert kwargs["config"].fix_alt_text is True


def test_table_tagger_safe_default_does_not_discover_provider():
    from src.education.remediation.table_tagger import TableTagger

    table = MagicMock(cells=[])
    with patch("src.ai.providers.get_provider_manager") as manager:
        TableTagger()._confirm_headers_with_ai("unused.pdf", [table])
    manager.assert_not_called()


def test_table_tagger_legacy_mode_discovers_provider():
    from src.education.remediation.table_tagger import TableTagger

    with patch(
        "src.ai.providers.get_provider_manager", return_value=object()
    ) as manager:
        with patch(
            "src.education.remediation.table_tagger.fitz.open", side_effect=RuntimeError
        ):
            with pytest.raises(RuntimeError):
                TableTagger(
                    allow_legacy_provider_manager=True
                )._confirm_headers_with_ai("unused.pdf", [])
    manager.assert_called_once_with()


def test_vision_strategy_safe_default_does_not_discover_provider(tmp_path):
    import src.education.remediation.reading_order as module

    path = Path(tmp_path) / "unused.pdf"
    path.write_bytes(b"unused")
    with (
        patch.object(module, "HAS_PYMUPDF", True),
        patch.object(module, "HAS_PIKEPDF", True),
        patch("src.ai.providers.get_provider_manager") as manager,
    ):
        result = module.VisionStrategy().fix(str(path))
    manager.assert_not_called()
    assert result.success is False
    assert result.error == "AI provider unavailable"
