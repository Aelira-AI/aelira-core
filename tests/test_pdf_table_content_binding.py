"""Saved-file proofs for verified PDF table content binding."""

from pathlib import Path
from unittest.mock import patch

import fitz
import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, Operator
from reportlab.pdfgen import canvas

from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.table_tagger import (
    TABLE_STRUCTURE_NOT_VERIFIED,
    TableTagger,
)

pytestmark = pytest.mark.unit


def _make_table_pdf(path: Path, values=None) -> str:
    values = values or [["Name", "Score"], ["Ada", "97"]]
    pdf = canvas.Canvas(str(path), pagesize=(400, 400))
    pdf.drawString(40, 360, "Before table")
    pdf.drawString(
        40,
        345,
        "Verified source content remains searchable before and after remediation.",
    )
    x_positions = [40, 170, 300]
    y_positions = [320 - 40 * row for row in range(len(values) + 1)]
    for x_position in x_positions:
        pdf.line(x_position, y_positions[-1], x_position, y_positions[0])
    for y_position in y_positions:
        pdf.line(x_positions[0], y_position, x_positions[-1], y_position)
    for row, row_values in enumerate(values):
        for column, value in enumerate(row_values):
            if value:
                pdf.drawString(
                    x_positions[column] + 8,
                    y_positions[row + 1] + 14,
                    value,
                )
    pdf.drawString(40, max(30, y_positions[-1] - 40), "After table")
    pdf.save()
    return str(path)


def _issue():
    return {
        "type": "table",
        "severity": "high",
        "message": "Table headers are not identified",
        "page_number": 1,
        "location": "Page 1, Table 1",
    }


def _remediate(path: Path, tmp_path: Path, extra_issues=None):
    return PdfRemediator(
        str(path),
        [*(extra_issues or []), _issue()],
        RemediationConfig(
            use_ai=False,
            create_backup=False,
            verify_fixes=False,
            output_directory=str(tmp_path),
        ),
    ).remediate()


def _stale_output(path: Path, tmp_path: Path) -> Path:
    output = tmp_path / f"{path.stem}_remediated{path.suffix}"
    output.write_bytes(b"stale output from a prior run")
    return output


def _walk_struct_elements(element):
    if not hasattr(element, "get"):
        return
    if element.get(Name.Type) == Name.StructElem:
        yield element
    kids = element.get(Name.K)
    if isinstance(kids, Array):
        for kid in kids:
            yield from _walk_struct_elements(kid)
    elif hasattr(kids, "get") and kids.get(Name.Type) == Name.StructElem:
        yield from _walk_struct_elements(kids)


def _same_object(first, second) -> bool:
    return first.objgen == second.objgen


def _marked_text_by_mcid(page):
    marked = {}
    stack = []
    for instruction in pikepdf.parse_content_stream(page):
        operator = str(instruction.operator)
        if operator == "BDC":
            properties = instruction.operands[1]
            mcid = properties.get(Name("/MCID"))
            stack.append(
                None
                if mcid is None
                else [int(mcid), str(instruction.operands[0]).lstrip("/"), []]
            )
        elif operator == "BMC":
            stack.append(None)
        elif operator == "EMC":
            marker = stack.pop()
            if marker is not None:
                assert marker[0] not in marked
                marked[marker[0]] = (marker[1], TableTagger._extract_text(marker[2]))
        else:
            for marker in stack:
                if marker is not None:
                    marker[2].append(instruction)
    assert stack == []
    return marked


def _mark_existing_paragraph(path: Path) -> None:
    """Give "Before table" a valid pre-existing MCID and ParentTree owner."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        start = end = None
        for index, instruction in enumerate(ops):
            if str(instruction.operator) != "BT":
                continue
            candidate_end = index + 1
            while candidate_end < len(ops) and str(ops[candidate_end].operator) != "ET":
                candidate_end += 1
            if (
                TableTagger._extract_text(ops[index : candidate_end + 1]).strip()
                == "Before table"
            ):
                start, end = index, candidate_end + 1
                break
        assert start is not None and end is not None

        root = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructTreeRoot,
                    "/K": Array([]),
                    "/ParentTree": Dictionary({"/Nums": Array([])}),
                }
            )
        )
        paragraph = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name.P,
                    "/P": root,
                    "/Pg": page.obj,
                    "/K": Dictionary(
                        {"/Type": Name("/MCR"), "/MCID": 0, "/Pg": page.obj}
                    ),
                }
            )
        )
        object_owner = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructElem,
                    "/S": Name.Link,
                    "/P": root,
                    "/Pg": page.obj,
                }
            )
        )
        root[Name.K].append(paragraph)
        root[Name.K].append(object_owner)
        root[Name.ParentTree][Name.Nums] = Array(
            [3, object_owner, 7, pdf.make_indirect(Array([paragraph]))]
        )
        pdf.Root[Name.StructTreeRoot] = root
        pdf.Root[Name.MarkInfo] = Dictionary({"/Marked": True})
        page.obj[Name.StructParents] = 7
        ops.insert(end, pikepdf.ContentStreamInstruction([], Operator("EMC")))
        ops.insert(
            start,
            pikepdf.ContentStreamInstruction(
                [Name.P, Dictionary({"/MCID": 0})], Operator("BDC")
            ),
        )
        page.obj[Name.Contents] = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def _reverse_repeated_text_object_order(path: Path, text: str) -> None:
    """Reverse equal-text BT objects while retaining their real coordinates."""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        blocks = []
        index = 0
        while index < len(ops):
            if str(ops[index].operator) != "BT":
                index += 1
                continue
            end = index + 1
            while end < len(ops) and str(ops[end].operator) != "ET":
                end += 1
            end += 1
            if TableTagger._extract_text(ops[index:end]).strip() == text:
                blocks.append((index, end))
            index = end
        assert len(blocks) == 2
        first, second = blocks
        first_ops = ops[first[0] : first[1]]
        second_ops = ops[second[0] : second[1]]
        ops = (
            ops[: first[0]]
            + second_ops
            + ops[first[1] : second[0]]
            + first_ops
            + ops[second[1] :]
        )
        page.obj[Name.Contents] = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(path)


def test_verified_table_binds_every_saved_cell_and_preserves_visible_text(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "ordinary.pdf"))
    with fitz.open(source) as document:
        original_text = document[0].get_text("text")

    result = _remediate(source, tmp_path)

    assert result.success is True
    assert result.fixed_count == 1
    assert result.manual_count == 0
    assert result.failed_count == 0
    assert result.output_file is not None
    output = Path(result.output_file)
    assert output.exists()
    with fitz.open(output) as document:
        assert document[0].get_text("text") == original_text
        assert "Before table" in original_text
        assert "After table" in original_text

    with pikepdf.open(output) as pdf:
        root = pdf.Root[Name.StructTreeRoot]
        elements = [
            element for kid in root[Name.K] for element in _walk_struct_elements(kid)
        ]
        tables = [element for element in elements if str(element[Name.S]) == "/Table"]
        rows = [element for element in elements if str(element[Name.S]) == "/TR"]
        cells = [
            element for element in elements if str(element[Name.S]) in ("/TH", "/TD")
        ]
        paragraphs = [element for element in elements if str(element[Name.S]) == "/P"]
        assert len(tables) == 1
        assert len(rows) == 2
        assert len(cells) == 4
        assert paragraphs, "mixed non-table content must remain structured"

        page = pdf.pages[0]
        struct_parent_key = int(page.obj[Name.StructParents])
        nums = root[Name.ParentTree][Name.Nums]
        parent_map = {
            int(nums[index]): nums[index + 1] for index in range(0, len(nums), 2)
        }
        owners = parent_map[struct_parent_key]
        marked = _marked_text_by_mcid(page)

        bound_cells = []
        for cell in cells:
            mcr = cell[Name.K]
            assert str(mcr[Name.Type]) == "/MCR"
            mcid = int(mcr[Name("/MCID")])
            assert _same_object(mcr[Name.Pg], page.obj)
            assert _same_object(owners[mcid], cell)
            expected_tag = str(cell[Name.S]).lstrip("/")
            assert marked[mcid][0] == expected_tag
            assert marked[mcid][1].strip()
            attributes = cell.get(Name.A)
            scope = str(attributes.get(Name("/Scope"))) if attributes else None
            bound_cells.append((expected_tag, scope, marked[mcid][1].strip()))
        assert bound_cells == [
            ("TH", "/Column", "Name"),
            ("TH", "/Column", "Score"),
            ("TD", None, "Ada"),
            ("TD", None, "97"),
        ]


def test_repeated_cell_text_is_bound_by_real_position_not_ordinal(tmp_path):
    source = Path(
        _make_table_pdf(
            tmp_path / "repeated.pdf",
            [["Status", "Status"], ["Open", "Closed"]],
        )
    )
    _reverse_repeated_text_object_order(source, "Status")

    result = _remediate(source, tmp_path)

    assert result.success is True
    assert result.fixed_count == 1
    assert result.manual_count == 0
    with pikepdf.open(result.output_file) as pdf:
        marked = _marked_text_by_mcid(pdf.pages[0])
        elements = [
            element
            for kid in pdf.Root[Name.StructTreeRoot][Name.K]
            for element in _walk_struct_elements(kid)
        ]
        cells = [
            element
            for element in elements
            if str(element.get(Name.S, "")) in ("/TH", "/TD")
        ]
        assert [marked[int(cell[Name.K][Name("/MCID")])][1] for cell in cells] == [
            "Status",
            "Status",
            "Open",
            "Closed",
        ]


def test_first_column_data_is_never_invented_as_row_headers(tmp_path):
    source = Path(
        _make_table_pdf(
            tmp_path / "id-name.pdf",
            [["ID", "Name"], ["1", "Ada"], ["2", "Bob"]],
        )
    )

    result = _remediate(source, tmp_path)

    assert result.success is True
    assert result.fixed_count == 1
    with pikepdf.open(result.output_file) as pdf:
        marked = _marked_text_by_mcid(pdf.pages[0])
        elements = [
            element
            for kid in pdf.Root[Name.StructTreeRoot][Name.K]
            for element in _walk_struct_elements(kid)
        ]
        cells = [
            element
            for element in elements
            if str(element.get(Name.S, "")) in ("/TH", "/TD")
        ]
        assert [
            (
                str(cell[Name.S]).lstrip("/"),
                marked[int(cell[Name.K][Name("/MCID")])][1],
            )
            for cell in cells
        ] == [
            ("TH", "ID"),
            ("TH", "Name"),
            ("TD", "1"),
            ("TD", "Ada"),
            ("TD", "2"),
            ("TD", "Bob"),
        ]
        assert all(str(cell[Name.A][Name("/Scope")]) == "/Column" for cell in cells[:2])
        assert all(Name.A not in cell for cell in cells[2:])


def test_existing_parent_tree_owner_and_marked_non_table_content_survive(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "existing-parent-tree.pdf"))
    _mark_existing_paragraph(source)

    result = _remediate(source, tmp_path)

    assert result.success is True
    assert result.fixed_count == 1
    with pikepdf.open(result.output_file) as pdf:
        root = pdf.Root[Name.StructTreeRoot]
        nums = root[Name.ParentTree][Name.Nums]
        parent_map = {
            int(nums[index]): nums[index + 1] for index in range(0, len(nums), 2)
        }
        assert str(parent_map[3][Name.S]) == "/Link"
        assert 7 in parent_map
        original_owner = parent_map[7][0]
        assert str(original_owner[Name.S]) == "/P"
        marked = _marked_text_by_mcid(pdf.pages[0])
        assert marked[0] == ("P", "Before table")
        assert _same_object(original_owner[Name.K][Name.Pg], pdf.pages[0].obj)


def test_existing_structure_root_without_mark_info_can_be_completed(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "root-without-mark-info.pdf"))
    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        pdf.Root[Name.StructTreeRoot] = pdf.make_indirect(
            Dictionary(
                {
                    "/Type": Name.StructTreeRoot,
                    "/K": Array([]),
                    "/ParentTree": Dictionary({"/Nums": Array([])}),
                }
            )
        )
        pdf.save(source)

    result = _remediate(source, tmp_path)

    assert result.success is True
    assert result.fixed_count == 1
    with pikepdf.open(result.output_file) as pdf:
        assert pdf.Root[Name.MarkInfo][Name.Marked] is True
        assert TableTagger.verify_file(result.output_file, 4) is True


def test_empty_cell_stays_manual_and_creates_no_output_or_structure(tmp_path):
    source = Path(
        _make_table_pdf(tmp_path / "empty.pdf", [["Name", "Score"], ["Ada", ""]])
    )
    before = source.read_bytes()

    result = _remediate(source, tmp_path)

    assert source.read_bytes() == before
    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    with pikepdf.open(source) as pdf:
        assert Name.StructTreeRoot not in pdf.Root


def test_missing_source_header_evidence_keeps_table_issue_manual(monkeypatch, tmp_path):
    source = Path(_make_table_pdf(tmp_path / "no-header-evidence.pdf"))
    monkeypatch.setattr(
        TableTagger,
        "_apply_detected_header_evidence",
        lambda *_args, **_kwargs: None,
    )

    result = _remediate(source, tmp_path)

    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED


def test_mixed_non_table_fix_survives_while_unverified_table_stays_manual(tmp_path):
    source = Path(
        _make_table_pdf(tmp_path / "mixed.pdf", [["Name", "Score"], ["Ada", ""]])
    )
    title_issue = {
        "type": "title",
        "severity": "high",
        "message": "Document title is missing",
    }

    result = _remediate(source, tmp_path, [title_issue])

    assert result.success is True
    assert result.fixed_count == 1
    assert result.fixed_issues[0].category.value == "title"
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    with pikepdf.open(result.output_file) as pdf:
        elements = [
            element
            for kid in pdf.Root[Name.StructTreeRoot][Name.K]
            for element in _walk_struct_elements(kid)
        ]
        assert all(str(element.get(Name.S, "")) != "/Table" for element in elements)


def test_duplicate_existing_mcid_fails_closed_without_changing_bytes(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "duplicate-mcid.pdf"))
    detection = TableTagger(use_ai=False).detect_tables(str(source))
    assert detection.success and len(detection.tables) == 1

    with pikepdf.open(source, allow_overwriting_input=True) as pdf:
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        text_blocks = []
        for index, instruction in enumerate(ops):
            if str(instruction.operator) == "BT":
                end = index + 1
                while end < len(ops) and str(ops[end].operator) != "ET":
                    end += 1
                if TableTagger._extract_text(ops[index : end + 1]).strip():
                    text_blocks.append((index, end + 1))
        for start, end in reversed(text_blocks[:2]):
            ops.insert(end, pikepdf.ContentStreamInstruction([], Operator("EMC")))
            ops.insert(
                start,
                pikepdf.ContentStreamInstruction(
                    [Name.P, Dictionary({"/MCID": 0})], Operator("BDC")
                ),
            )
        pdf.pages[0].obj[Name.Contents] = pdf.make_stream(
            pikepdf.unparse_content_stream(ops)
        )
        pdf.save(source)
    before = source.read_bytes()

    result = TableTagger(use_ai=False).write_tables(str(source), detection.tables)

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED
    assert source.read_bytes() == before


def test_binding_exception_is_transactional_and_returns_manual_code(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "failure.pdf"))
    tagger = TableTagger(use_ai=False)
    detection = tagger.detect_tables(str(source))
    before = source.read_bytes()

    with patch.object(tagger, "_apply_binding_plans", side_effect=RuntimeError("boom")):
        result = tagger.write_tables(str(source), detection.tables)

    assert result.success is False
    assert result.error_code == TABLE_STRUCTURE_NOT_VERIFIED
    assert source.read_bytes() == before


def test_v2_failure_reclassifies_provisional_table_and_publishes_nothing(
    monkeypatch, tmp_path
):
    source = Path(_make_table_pdf(tmp_path / "v2-failure.pdf"))
    before = source.read_bytes()
    stale_output = _stale_output(source, tmp_path)

    def fail_v2(_self):
        raise RuntimeError("forced v2 failure")

    monkeypatch.setattr(
        "src.education.remediation.pdf_remediator.ContentTaggerV2.tag_all_pages",
        fail_v2,
    )

    result = _remediate(source, tmp_path)

    assert source.read_bytes() == before
    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert not stale_output.exists()


def test_v2_failure_rolls_back_table_but_preserves_mixed_title_fix(
    monkeypatch, tmp_path
):
    source = Path(_make_table_pdf(tmp_path / "mixed-v2-failure.pdf"))
    stale_output = _stale_output(source, tmp_path)
    title_issue = {
        "type": "title",
        "severity": "high",
        "message": "Document title is missing",
    }

    def fail_v2(_self):
        raise RuntimeError("forced v2 failure")

    monkeypatch.setattr(
        "src.education.remediation.pdf_remediator.ContentTaggerV2.tag_all_pages",
        fail_v2,
    )

    result = _remediate(source, tmp_path, [title_issue])

    assert result.output_file is not None
    assert result.fixed_count == 1
    assert result.fixed_issues[0].category.value == "title"
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert Path(result.output_file) == stale_output
    assert stale_output.read_bytes() != b"stale output from a prior run"
    with pikepdf.open(result.output_file) as pdf:
        elements = [
            element
            for kid in pdf.Root[Name.StructTreeRoot][Name.K]
            for element in _walk_struct_elements(kid)
        ]
        assert all(str(element.get(Name.S, "")) != "/Table" for element in elements)
        with pdf.open_metadata() as metadata:
            assert metadata.get("dc:title")


def test_failed_saved_binding_verification_has_no_pymupdf_fallback(
    monkeypatch, tmp_path
):
    source = Path(_make_table_pdf(tmp_path / "saved-verification-failure.pdf"))
    stale_output = _stale_output(source, tmp_path)
    monkeypatch.setattr(
        "src.education.remediation.pdf_remediator.TableTagger.verify_file",
        lambda *_args, **_kwargs: False,
    )

    result = _remediate(source, tmp_path)

    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert not stale_output.exists()


def test_failed_bound_pdf_save_removes_stale_deterministic_output(
    monkeypatch, tmp_path
):
    source = Path(_make_table_pdf(tmp_path / "saved-write-failure.pdf"))
    stale_output = _stale_output(source, tmp_path)
    original_save = pikepdf.Pdf.save

    def fail_verified_table_save(pdf, destination, *args, **kwargs):
        if "aelira_pdf_serialization_" in str(destination):
            raise RuntimeError("forced bound PDF save failure")
        return original_save(pdf, destination, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", fail_verified_table_save)

    result = _remediate(source, tmp_path)

    assert result.output_file is None
    assert result.fixed_count == 0
    assert result.manual_count == 1
    assert result.manual_issues[0].reason == TABLE_STRUCTURE_NOT_VERIFIED
    assert not stale_output.exists()


def test_failed_output_cleanup_refuses_files_outside_configured_directory(tmp_path):
    source = Path(_make_table_pdf(tmp_path / "cleanup-scope.pdf"))
    managed_directory = tmp_path / "managed"
    managed_directory.mkdir()
    unrelated = tmp_path / "unrelated.pdf"
    unrelated.write_bytes(b"unrelated sentinel")
    remediator = PdfRemediator(
        str(source),
        [_issue()],
        RemediationConfig(
            use_ai=False,
            create_backup=False,
            verify_fixes=False,
            output_directory=str(managed_directory),
        ),
    )

    remediator._discard_failed_table_output(str(unrelated))

    assert unrelated.read_bytes() == b"unrelated sentinel"
