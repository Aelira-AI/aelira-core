"""Safety regressions for unverified PDF table structure writes."""

from pathlib import Path
from unittest.mock import MagicMock

import pikepdf
import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.table_tagger import (
    TABLE_STRUCTURE_NOT_VERIFIED,
    TABLE_STRUCTURE_TOO_COMPLEX,
    CellInfo,
    TableInfo,
    TableTagResult,
    TableTagger,
)

pytestmark = pytest.mark.unit


def _table(
    rows: int,
    columns: int,
    *,
    page_num: int = 0,
    table_index: int = 0,
    partially_populated: bool = False,
    merged: bool = False,
) -> TableInfo:
    table = TableInfo(
        page_num=page_num,
        table_index=table_index,
        bbox=(20, 20, 200, 200),
        rows=rows,
        cols=columns,
        has_merged_cells=merged,
    )
    cell_limit = 1 if partially_populated else rows * columns
    table.cells = [
        CellInfo(
            row=index // columns,
            col=index % columns,
            text="Only extracted text" if index == 0 else f"cell-{index}",
            bbox=(20, 20, 40, 40),
            col_span=2 if merged and index == 0 else 1,
        )
        for index in range(cell_limit)
    ]
    return table


def _issue(element: str, *, page_number: int = 1, table_number: int = 1):
    return {
        "type": "table",
        "severity": "high",
        "message": "Table headers are not identified",
        "element": element,
        "page_number": page_number,
        "location": f"Page {page_number}, Table {table_number}",
        "detected_headers": "Name, Score",
    }


def _title_issue():
    return {
        "type": "title",
        "severity": "high",
        "message": "Document title is missing",
    }


def _minimal_pdf(path: Path) -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(300, 300))
    pdf.save(path)
    return path.read_bytes()


def _remediator(tmp_path, issues) -> PdfRemediator:
    pdf_path = tmp_path / "input.pdf"
    if not pdf_path.exists():
        _minimal_pdf(pdf_path)
    remediator = PdfRemediator(
        str(pdf_path),
        issues,
        RemediationConfig(use_ai=False, create_backup=False, verify_fixes=False),
    )
    remediator._struct_tree = MagicMock()
    return remediator


def _mock_detection(monkeypatch, result: TableTagResult):
    import src.education.remediation.pdf_remediator as pdf_remediator_module

    tagger = MagicMock()
    tagger.detect_tables.return_value = result
    monkeypatch.setattr(pdf_remediator_module, "TableTagger", lambda **_kwargs: tagger)
    return tagger


def _assert_no_structure_tree(path: Path) -> None:
    with pikepdf.open(path) as pdf:
        assert "/StructTreeRoot" not in pdf.Root


def test_not_verified_partially_populated_merged_grid_stays_manual_without_saves(
    monkeypatch, tmp_path
):
    detected = _table(3, 3, partially_populated=True, merged=True)
    remediator = _remediator(tmp_path, [_issue("Table (3 rows x 3 cols)")])
    pending_pdf = MagicMock()
    remediator._pikepdf_doc = pending_pdf
    remediator._structure_modified = True
    _mock_detection(
        monkeypatch,
        TableTagResult(success=True, tables_found=1, tables=[detected]),
    )

    before = Path(remediator.file_path).read_bytes()
    remediator._process_issue(remediator.issues[0], None)

    assert Path(remediator.file_path).read_bytes() == before
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.failed_count == 0
    assert remediator.result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert remediator._table_fixed_issue_ids == set()
    pending_pdf.save.assert_not_called()


def test_table_only_remediation_skips_reload_final_save_and_output(
    monkeypatch, tmp_path
):
    detected = _table(2, 2)
    remediator = _remediator(tmp_path, [_issue("Table (2 rows x 2 cols)")])
    remediator.config.create_backup = True
    _mock_detection(
        monkeypatch,
        TableTagResult(success=True, tables_found=1, tables=[detected]),
    )
    save_document = MagicMock(side_effect=AssertionError("must not save"))
    monkeypatch.setattr(remediator, "_save_document", save_document)

    source = Path(remediator.file_path)
    output = source.with_name("input_remediated.pdf")
    before = source.read_bytes()
    result = remediator.remediate()

    assert source.read_bytes() == before
    assert output.exists() is False
    assert (tmp_path / "backups").exists() is False
    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.failed_count == 0
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert remediator._table_fixed_issue_ids == set()
    save_document.assert_not_called()
    _assert_no_structure_tree(source)


def test_direct_tag_tables_refuses_without_changing_bytes_or_structure(
    monkeypatch, tmp_path
):
    path = tmp_path / "direct.pdf"
    before = _minimal_pdf(path)
    detected = _table(2, 2)
    tagger = TableTagger(use_ai=False)
    monkeypatch.setattr(
        tagger,
        "detect_tables",
        lambda _path: TableTagResult(success=True, tables_found=1, tables=[detected]),
    )

    result = tagger.tag_tables(str(path))

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED
    assert result.tables_tagged == 0
    assert path.read_bytes() == before
    _assert_no_structure_tree(path)


def test_direct_write_seam_refuses_without_changing_bytes_or_structure(tmp_path):
    path = tmp_path / "write.pdf"
    before = _minimal_pdf(path)

    result = TableTagger(use_ai=False).write_tables(str(path), [_table(2, 2)])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED
    assert result.tables_tagged == 0
    assert path.read_bytes() == before
    _assert_no_structure_tree(path)


def test_single_table_public_seam_refuses_without_changing_bytes(monkeypatch, tmp_path):
    import src.education.remediation.table_tagger as table_tagger_module

    path = tmp_path / "single.pdf"
    before = _minimal_pdf(path)
    detected = MagicMock()
    detected.bbox = (20, 20, 200, 200)
    detected.extract.return_value = [["Header", "Value"], ["A", "1"]]
    detected.cells = [(20, 20, 40, 40)] * 4
    page = MagicMock()
    page.find_tables.return_value.tables = [detected]
    document = MagicMock()
    document.__len__.return_value = 1
    document.__getitem__.return_value = page
    monkeypatch.setattr(table_tagger_module.fitz, "open", lambda _path: document)

    result = TableTagger(use_ai=False).tag_table(str(path), 0, (20, 20, 200, 200))

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED
    assert result.tables_tagged == 0
    assert path.read_bytes() == before
    _assert_no_structure_tree(path)


@pytest.mark.parametrize(
    "table",
    [
        _table(42, 205, partially_populated=True),
        _table(201, 50, partially_populated=True),
    ],
)
def test_direct_write_seam_keeps_oversized_result_deterministic(table):
    result = TableTagger(use_ai=False).write_tables("unused.pdf", [table])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_TOO_COMPLEX


def test_detected_table_count_over_200_is_too_complex_before_any_write(
    monkeypatch, tmp_path
):
    import src.education.remediation.table_tagger as table_tagger_module

    path = tmp_path / "many.pdf"
    before = _minimal_pdf(path)
    document = MagicMock()
    monkeypatch.setattr(table_tagger_module.fitz, "open", lambda _path: document)
    tagger = TableTagger(use_ai=False)
    monkeypatch.setattr(tagger, "_detect_tables", lambda _doc: [_table(2, 2)] * 201)

    result = tagger.tag_tables(str(path))

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_TOO_COMPLEX
    assert path.read_bytes() == before
    _assert_no_structure_tree(path)


def test_exact_amplification_limits_are_not_classified_as_too_complex():
    tables = [_table(1, 50) for _ in range(200)]

    assert TableTagger._safety_error_code(tables) is None
    assert TableTagger._safety_error_code([_table(1, 64)]) is None


@pytest.mark.parametrize(
    "tables",
    [
        [_table(1, 65)],
        [_table(1, 50) for _ in range(200)] + [_table(1, 1)],
        [_table(1, 50) for _ in range(199)] + [_table(1, 51)],
    ],
)
def test_amplification_limits_fail_closed_only_when_exceeded(tables):
    assert TableTagger._safety_error_code(tables) == TABLE_STRUCTURE_TOO_COMPLEX


def test_too_complex_valid_first_issue_is_not_saved_when_later_is_42x205(
    monkeypatch, tmp_path
):
    remediator = _remediator(
        tmp_path,
        [
            _issue("Table (2 rows x 2 cols)", page_number=1),
            _issue("Table (42 rows x 205 cols)", page_number=2),
        ],
    )
    tagger_factory = MagicMock()
    import src.education.remediation.pdf_remediator as pdf_remediator_module

    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    before = Path(remediator.file_path).read_bytes()
    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    assert Path(remediator.file_path).read_bytes() == before
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 2
    assert remediator.result.failed_count == 0
    assert {manual.reason for manual in remediator.result.manual_issues} == {
        TABLE_STRUCTURE_TOO_COMPLEX
    }
    assert remediator._table_fixed_issue_ids == set()
    tagger_factory.assert_not_called()


def test_aggregate_issue_cells_are_bounded_before_detection(monkeypatch, tmp_path):
    remediator = _remediator(
        tmp_path,
        [
            _issue("Table (101 rows x 50 cols)", page_number=1),
            _issue("Table (101 rows x 50 cols)", page_number=2),
        ],
    )
    tagger_factory = MagicMock()
    import src.education.remediation.pdf_remediator as pdf_remediator_module

    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 2
    assert {manual.reason for manual in remediator.result.manual_issues} == {
        TABLE_STRUCTURE_TOO_COMPLEX
    }
    tagger_factory.assert_not_called()


def test_more_than_200_issues_are_bounded_before_detection(monkeypatch, tmp_path):
    remediator = _remediator(
        tmp_path,
        [_issue("Table (2 rows x 2 cols)", page_number=page) for page in range(1, 202)],
    )
    tagger_factory = MagicMock()
    import src.education.remediation.pdf_remediator as pdf_remediator_module

    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 201
    assert {manual.reason for manual in remediator.result.manual_issues} == {
        TABLE_STRUCTURE_TOO_COMPLEX
    }
    tagger_factory.assert_not_called()


def test_detector_exception_stays_manual_not_failed(monkeypatch, tmp_path):
    tagger = _mock_detection(
        monkeypatch,
        TableTagResult(success=True),
    )
    tagger.detect_tables.side_effect = RuntimeError("detector unavailable")
    remediator = _remediator(tmp_path, [_issue("Table (2 rows x 2 cols)")])

    remediator._process_issue(remediator.issues[0], None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.failed_count == 0
    assert remediator.result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert remediator._table_fixed_issue_ids == set()


def test_too_complex_mixed_title_and_42x205_refuses_before_backup_and_load(
    monkeypatch, tmp_path
):
    remediator = _remediator(
        tmp_path,
        [_title_issue(), _issue("Table (42 rows x 205 cols)")],
    )
    remediator.config.create_backup = True
    events = []
    original_preflight = remediator._prepare_table_fixes
    original_backup = remediator._create_backup
    original_load = remediator._load_document

    def tracked_preflight():
        events.append("preflight")
        return original_preflight()

    def tracked_backup():
        events.append("backup")
        return original_backup()

    def tracked_load():
        events.append("load")
        return original_load()

    monkeypatch.setattr(remediator, "_prepare_table_fixes", tracked_preflight)
    monkeypatch.setattr(remediator, "_create_backup", tracked_backup)
    monkeypatch.setattr(remediator, "_load_document", tracked_load)

    import src.education.remediation.pdf_remediator as pdf_remediator_module

    tagger_factory = MagicMock()
    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    result = remediator.remediate()

    assert events == ["preflight"]
    assert tagger_factory.call_count == 0
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_TOO_COMPLEX
    assert (
        result.manual_issues[0].metadata["remediation_error_code"]
        == TABLE_STRUCTURE_TOO_COMPLEX
    )
    assert result.failed_count == 0
    assert result.skipped_count == 1
    assert result.output_file is None
    assert result.backup_path is None
    assert (tmp_path / "backups").exists() is False
    assert Path(remediator._get_output_path()).exists() is False
    _assert_no_structure_tree(Path(remediator.file_path))


@pytest.mark.parametrize("failure_at", ["constructor_setup", "detection"])
def test_not_verified_batch_is_coherent_for_setup_or_detection_failure(
    monkeypatch, tmp_path, failure_at
):
    import src.education.remediation.pdf_remediator as pdf_remediator_module

    factory = MagicMock()
    if failure_at == "constructor_setup":
        factory.side_effect = RuntimeError("tagger setup unavailable")
    else:
        tagger = MagicMock()
        tagger.detect_tables.side_effect = RuntimeError("detector unavailable")
        factory.return_value = tagger
    monkeypatch.setattr(pdf_remediator_module, "TableTagger", factory)

    remediator = _remediator(
        tmp_path,
        [
            _issue("Table (2 rows x 2 cols)", table_number=1),
            _issue("Table (3 rows x 3 cols)", table_number=2),
        ],
    )

    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 2
    assert remediator.result.failed_count == 0
    assert {manual.reason for manual in remediator.result.manual_issues} == {
        TABLE_STRUCTURE_NOT_VERIFIED
    }
    assert remediator._table_fixed_issue_ids == set()
    assert factory.call_count == 1
    if failure_at == "detection":
        factory.return_value.detect_tables.assert_called_once_with(remediator.file_path)


def test_not_verified_read_only_detection_preflight_is_cached_for_the_batch(
    monkeypatch, tmp_path
):
    tagger = _mock_detection(
        monkeypatch,
        TableTagResult(success=True, tables_found=1, tables=[_table(2, 2)]),
    )
    remediator = _remediator(
        tmp_path,
        [
            _issue("Table (2 rows x 2 cols)", table_number=1),
            _issue("Table (3 rows x 3 cols)", table_number=2),
        ],
    )

    remediator._prepare_table_fixes()
    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    tagger.detect_tables.assert_called_once_with(remediator.file_path)
    assert remediator._table_preflight_detection.tables_found == 1
    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 2
    assert remediator.result.failed_count == 0


def test_not_verified_non_positive_declared_grid_is_rejected():
    table = _table(2, 2)
    table.rows = -42
    table.cols = 0

    result = TableTagger(use_ai=False).write_tables("unused.pdf", [table])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED


def test_too_complex_ragged_cell_at_column_204_uses_evidenced_grid():
    table = _table(2, 2, partially_populated=True)
    table.cells[0].col = 204

    result = TableTagger(use_ai=False).write_tables("unused.pdf", [table])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_TOO_COMPLEX


def test_too_complex_merged_span_past_64_columns_uses_evidenced_grid():
    table = _table(2, 64, partially_populated=True)
    table.cells[0].col = 63
    table.cells[0].col_span = 2

    result = TableTagger(use_ai=False).write_tables("unused.pdf", [table])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_TOO_COMPLEX


def test_too_complex_row_span_contributes_to_aggregate_logical_cells():
    table = _table(100, 64, partially_populated=True)
    table.cells[0].row = 99
    table.cells[0].row_span = 60

    result = TableTagger(use_ai=False).write_tables("unused.pdf", [table])

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_TOO_COMPLEX


def test_too_complex_negative_metadata_cannot_reduce_other_issue_aggregate(
    monkeypatch, tmp_path
):
    invalid = _issue("Table with invalid dimensions", page_number=1)
    invalid.update({"rows": -500, "columns": 50})
    remediator = _remediator(
        tmp_path,
        [
            invalid,
            _issue("Table (101 rows x 50 cols)", page_number=2),
            _issue("Table (101 rows x 50 cols)", page_number=3),
        ],
    )

    import src.education.remediation.pdf_remediator as pdf_remediator_module

    tagger_factory = MagicMock()
    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    for issue in remediator.issues:
        remediator._process_issue(issue, None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 3
    assert remediator.result.failed_count == 0
    assert {manual.reason for manual in remediator.result.manual_issues} == {
        TABLE_STRUCTURE_TOO_COMPLEX
    }
    tagger_factory.assert_not_called()


def test_too_complex_42x205_evidence_cannot_be_hidden_by_smaller_metadata(
    monkeypatch, tmp_path
):
    issue = _issue("Table (42 rows x 205 cols)")
    issue.update({"rows": 2, "columns": 2})
    remediator = _remediator(tmp_path, [issue])

    import src.education.remediation.pdf_remediator as pdf_remediator_module

    tagger_factory = MagicMock()
    monkeypatch.setattr(pdf_remediator_module, "TableTagger", tagger_factory)

    remediator._process_issue(remediator.issues[0], None)

    assert remediator.result.fixed_count == 0
    assert remediator.result.manual_count == 1
    assert remediator.result.failed_count == 0
    assert remediator.result.manual_issues[0].reason == TABLE_STRUCTURE_TOO_COMPLEX
    tagger_factory.assert_not_called()
