"""
Unit tests for PDF Structure Tree manipulation and PDF/UA-2 detection.

Tests the pikepdf-based structure tree manipulation for PDF/UA-1 and PDF/UA-2 compliance.
"""

import pytest
import tempfile
import os

# Skip all tests if pikepdf is not available
pikepdf = pytest.importorskip("pikepdf")

from src.education.remediation.pdf_structure import (  # noqa: E402
    PDFStructureTree,
    verify_pdf_accessibility,
)
from src.education.pdf_processor import (  # noqa: E402
    PDFProcessor,
    PDFUAVersion,
)


@pytest.fixture
def sample_pdf():
    """Create a simple PDF for testing."""
    pdf = pikepdf.new()
    # Add a blank page (Letter size: 612 x 792 points)
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


@pytest.fixture
def temp_pdf_path(sample_pdf):
    """Save sample PDF to a temp file and return path."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        sample_pdf.save(f.name)
        yield f.name
    # Cleanup
    if os.path.exists(f.name):
        os.unlink(f.name)


class TestPDFStructureTree:
    """Tests for PDFStructureTree class."""

    def test_init_creates_struct_tree_root(self, sample_pdf):
        """Test that initialization creates StructTreeRoot if missing."""
        _struct_tree = PDFStructureTree(sample_pdf)  # noqa: F841 - triggers side effect

        assert pikepdf.Name.StructTreeRoot in sample_pdf.Root
        assert pikepdf.Name.MarkInfo in sample_pdf.Root
        marked = sample_pdf.Root[pikepdf.Name.MarkInfo][pikepdf.Name.Marked]
        assert marked == True  # noqa: E712 - pikepdf Boolean

    def test_set_document_language(self, sample_pdf):
        """Test setting document language."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.set_document_language("en-US")

        assert result is True
        assert pikepdf.Name.Lang in sample_pdf.Root
        assert str(sample_pdf.Root[pikepdf.Name.Lang]) == "en-US"

    def test_set_document_title(self, sample_pdf):
        """Test setting document title."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.set_document_title("Test Document Title")

        assert result is True
        assert pikepdf.Name.ViewerPreferences in sample_pdf.Root
        viewer_prefs = sample_pdf.Root[pikepdf.Name.ViewerPreferences]
        assert viewer_prefs[pikepdf.Name.DisplayDocTitle] == True  # noqa: E712

    def test_add_alt_text_to_image(self, sample_pdf):
        """Test adding alt text to an image."""
        struct_tree = PDFStructureTree(sample_pdf)
        alt_text = "Chart showing quarterly revenue growth"

        result = struct_tree.add_alt_text_to_image(
            page_num=1,
            alt_text=alt_text,
            image_index=0,
        )

        assert result is True

        # Verify structure element was created
        kids = struct_tree.kids
        assert len(kids) > 0

        # Find the Figure element
        fig_elem = kids[-1]
        assert fig_elem[pikepdf.Name.S] == pikepdf.Name.Figure
        assert str(fig_elem[pikepdf.Name.Alt]) == alt_text

    def test_add_heading(self, sample_pdf):
        """Test adding a heading structure element."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_heading(
            page_num=1,
            level=1,
            text="Introduction",
        )

        assert result is True

        # Verify heading was added
        kids = struct_tree.kids
        assert len(kids) > 0

        heading_elem = kids[-1]
        assert str(heading_elem[pikepdf.Name.S]) == "/H1"
        assert str(heading_elem[pikepdf.Name.ActualText]) == "Introduction"

    def test_add_heading_level_clamping(self, sample_pdf):
        """Test that invalid heading levels are clamped to 1-6."""
        struct_tree = PDFStructureTree(sample_pdf)

        # Level 0 should become 1
        struct_tree.add_heading(page_num=1, level=0, text="Test")
        heading_elem = struct_tree.kids[-1]
        assert str(heading_elem[pikepdf.Name.S]) == "/H1"

        # Level 10 should become 6
        struct_tree.add_heading(page_num=1, level=10, text="Test")
        heading_elem = struct_tree.kids[-1]
        assert str(heading_elem[pikepdf.Name.S]) == "/H6"

    def test_add_paragraph(self, sample_pdf):
        """Test adding a paragraph structure element."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_paragraph(
            page_num=1,
            text="This is a test paragraph.",
        )

        assert result is True

        kids = struct_tree.kids
        para_elem = kids[-1]
        assert para_elem[pikepdf.Name.S] == pikepdf.Name.P
        assert str(para_elem[pikepdf.Name.ActualText]) == "This is a test paragraph."

    def test_add_table(self, sample_pdf):
        """Test adding a table with headers."""
        struct_tree = PDFStructureTree(sample_pdf)

        headers = ["Name", "Age", "City"]
        rows = [
            ["Alice", "30", "New York"],
            ["Bob", "25", "Los Angeles"],
        ]

        result = struct_tree.add_table(
            page_num=1,
            headers=headers,
            rows=rows,
            summary="User information table",
        )

        assert result is True

        kids = struct_tree.kids
        table_elem = kids[-1]
        assert table_elem[pikepdf.Name.S] == pikepdf.Name.Table
        assert str(table_elem[pikepdf.Name.Alt]) == "User information table"

    def test_add_list(self, sample_pdf):
        """Test adding a list structure."""
        struct_tree = PDFStructureTree(sample_pdf)

        items = ["First item", "Second item", "Third item"]

        result = struct_tree.add_list(
            page_num=1,
            items=items,
            ordered=True,
        )

        assert result is True

        kids = struct_tree.kids
        list_elem = kids[-1]
        assert list_elem[pikepdf.Name.S] == pikepdf.Name.L

    def test_get_stats(self, sample_pdf):
        """Test getting structure tree statistics."""
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_document_language("en")
        struct_tree.add_heading(1, 1, "Title")
        struct_tree.add_alt_text_to_image(1, "Image description")

        stats = struct_tree.get_stats()

        assert stats["has_struct_tree"] is True
        assert stats["is_marked"] is True
        assert stats["has_language"] is True
        assert stats["language"] == "en"
        assert stats["element_count"] >= 2


class TestVerifyPdfAccessibility:
    """Tests for the verify_pdf_accessibility function."""

    def test_verify_untagged_pdf(self, temp_pdf_path):
        """Test verification of an untagged PDF."""
        results = verify_pdf_accessibility(temp_pdf_path)

        assert results["is_tagged"] is False
        assert results["has_struct_tree"] is False
        assert "Document is not marked as tagged" in results["issues"]

    def test_verify_tagged_pdf(self, sample_pdf):
        """Test verification of a tagged PDF."""
        # Create structure tree
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_document_language("en")
        struct_tree.set_document_title("Test Document")
        struct_tree.add_heading(1, 1, "Title")
        struct_tree.add_alt_text_to_image(1, "Test image")

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            results = verify_pdf_accessibility(temp_path)

            assert results["is_tagged"] is True
            assert results["has_struct_tree"] is True
            assert results["language_set"] is True
            assert results["language"] == "en"
            assert results["figure_count"] >= 1
            assert results["heading_count"] >= 1
        finally:
            os.unlink(temp_path)


class TestPDFUA2Elements:
    """Tests for PDF/UA-2 structure elements."""

    def test_add_emphasis_em(self, sample_pdf):
        """Test adding <Em> emphasis element."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_emphasis(
            page_num=1,
            text="emphasized text",
            strong=False,
        )

        assert result is True
        kids = struct_tree.kids
        em_elem = kids[-1]
        assert str(em_elem[pikepdf.Name.S]) == "/Em"
        assert str(em_elem[pikepdf.Name.ActualText]) == "emphasized text"

    def test_add_emphasis_strong(self, sample_pdf):
        """Test adding <Strong> emphasis element."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_emphasis(
            page_num=1,
            text="strong text",
            strong=True,
        )

        assert result is True
        kids = struct_tree.kids
        strong_elem = kids[-1]
        assert str(strong_elem[pikepdf.Name.S]) == "/Strong"
        assert str(strong_elem[pikepdf.Name.ActualText]) == "strong text"

    def test_add_aside(self, sample_pdf):
        """Test adding <Aside> element for supplementary content."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_aside(
            page_num=1,
            text="This is a sidebar with additional information.",
        )

        assert result is True
        kids = struct_tree.kids
        aside_elem = kids[-1]
        assert str(aside_elem[pikepdf.Name.S]) == "/Aside"

    def test_add_footnote(self, sample_pdf):
        """Test adding <FENote> footnote element."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_footnote(
            page_num=1,
            text="See reference [1] for more details.",
            note_type="footnote",
        )

        assert result is True
        kids = struct_tree.kids
        fenote_elem = kids[-1]
        assert str(fenote_elem[pikepdf.Name.S]) == "/FENote"

    def test_add_document_fragment(self, sample_pdf):
        """Test adding <DocumentFragment> container element."""
        struct_tree = PDFStructureTree(sample_pdf)

        fragment = struct_tree.add_document_fragment(
            page_num=1,
            title="Chapter 1",
        )

        assert fragment is not None
        assert str(fragment[pikepdf.Name.S]) == "/DocumentFragment"
        assert str(fragment[pikepdf.Name("/T")]) == "Chapter 1"

    def test_add_ruby_annotation(self, sample_pdf):
        """Test adding Ruby annotation for East Asian text."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.add_ruby_annotation(
            page_num=1,
            base_text="漢字",
            annotation_text="かんじ",
        )

        assert result is True
        kids = struct_tree.kids
        ruby_elem = kids[-1]
        assert str(ruby_elem[pikepdf.Name.S]) == "/Ruby"

        # Check Ruby contains RB and RT children
        ruby_kids = ruby_elem[pikepdf.Name.K]
        assert len(ruby_kids) == 2
        assert str(ruby_kids[0][pikepdf.Name.S]) == "/RB"
        assert str(ruby_kids[1][pikepdf.Name.S]) == "/RT"

    def test_set_pdfua_identifier_ua1(self, sample_pdf):
        """Test setting PDF/UA-1 identifier."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.set_pdfua_identifier(version=1)

        assert result is True
        with sample_pdf.open_metadata() as meta:
            pdfua_part = meta.get("{http://www.aiim.org/pdfua/ns/id/}part")
            assert pdfua_part == "1"

    def test_set_pdfua_identifier_ua2(self, sample_pdf):
        """Test setting PDF/UA-2 identifier."""
        struct_tree = PDFStructureTree(sample_pdf)

        result = struct_tree.set_pdfua_identifier(version=2)

        assert result is True
        with sample_pdf.open_metadata() as meta:
            pdfua_part = meta.get("{http://www.aiim.org/pdfua/ns/id/}part")
            assert pdfua_part == "2"


class TestIntegration:
    """Integration tests for full PDF remediation workflow."""

    def test_full_remediation_workflow(self, sample_pdf):
        """Test a complete remediation workflow."""
        # 1. Create structure tree
        struct_tree = PDFStructureTree(sample_pdf)

        # 2. Set metadata
        struct_tree.set_document_language("en")
        struct_tree.set_document_title("Accessible Document")

        # 3. Add structure
        struct_tree.add_heading(1, 1, "Main Title")
        struct_tree.add_paragraph(1, "Introduction paragraph text.")
        struct_tree.add_heading(1, 2, "Section 1")
        struct_tree.add_alt_text_to_image(1, "Logo showing company name")
        struct_tree.add_table(
            1,
            headers=["Column A", "Column B"],
            rows=[["Data 1", "Data 2"]],
        )
        struct_tree.add_list(1, ["Item 1", "Item 2", "Item 3"])

        # 4. Save and verify
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            results = verify_pdf_accessibility(temp_path)

            assert results["is_tagged"] is True
            assert results["has_struct_tree"] is True
            assert results["language_set"] is True
            assert results["heading_count"] >= 2
            assert results["figure_count"] >= 1
            assert results["table_count"] >= 1
            assert (
                len(results["issues"]) == 0
                or "title" not in str(results["issues"]).lower()
            )
        finally:
            os.unlink(temp_path)


class TestPDFUAVersionDetection:
    """Tests for PDF/UA version detection."""

    def test_detect_untagged_pdf(self, temp_pdf_path):
        """Test detection of non-PDF/UA compliant document."""
        processor = PDFProcessor()
        result = processor.detect_pdfua_version(temp_pdf_path)

        assert result.version_detected == PDFUAVersion.NONE
        assert result.pdfua_identifier is None
        # Check that at least one issue mentions PDF/UA compliance
        assert any("PDF/UA compliance" in issue for issue in result.ua2_issues)

    def test_detect_pdfua1_document(self, sample_pdf):
        """Test detection of PDF/UA-1 document."""
        # Create a UA-1 compliant PDF
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_document_language("en")
        struct_tree.set_document_title("Test UA-1 Document")
        struct_tree.set_pdfua_identifier(version=1)

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            processor = PDFProcessor()
            result = processor.detect_pdfua_version(temp_path)

            assert result.version_detected == PDFUAVersion.UA1
            assert result.pdfua_identifier == "1"
            # UA-1 documents get upgrade recommendations
            assert len(result.upgrade_recommendations) > 0
        finally:
            os.unlink(temp_path)

    def test_detect_pdfua2_document(self, sample_pdf):
        """Test detection of PDF/UA-2 document."""
        # Create a UA-2 compliant PDF
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_document_language("en")
        struct_tree.set_document_title("Test UA-2 Document")
        struct_tree.set_pdfua_identifier(version=2)

        # Add some UA-2 elements
        struct_tree.add_emphasis(1, "emphasized text", strong=True)
        struct_tree.add_aside(1, "sidebar content")

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            processor = PDFProcessor()
            result = processor.detect_pdfua_version(temp_path)

            assert result.version_detected == PDFUAVersion.UA2
            assert result.pdfua_identifier == "2"
            # Check UA-2 features detected
            assert (
                result.ua2_features["emphasis_elements"] is True
                or result.ua2_features["aside"] is True
            )
        finally:
            os.unlink(temp_path)

    def test_detect_ua2_features(self, sample_pdf):
        """Test detection of specific UA-2 features."""
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_pdfua_identifier(version=2)

        # Add various UA-2 elements
        struct_tree.add_emphasis(1, "test", strong=False)  # Em
        struct_tree.add_emphasis(1, "test", strong=True)  # Strong
        struct_tree.add_aside(1, "sidebar")
        struct_tree.add_footnote(1, "footnote text")
        struct_tree.add_ruby_annotation(1, "漢字", "かんじ")

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            processor = PDFProcessor()
            result = processor.detect_pdfua_version(temp_path)

            # These should be detected as True
            assert result.ua2_features["emphasis_elements"] is True
            # Note: ruby detection depends on structure tree traversal
        finally:
            os.unlink(temp_path)

    def test_upgrade_recommendations_for_ua1(self, sample_pdf):
        """Test that UA-1 documents get upgrade recommendations."""
        struct_tree = PDFStructureTree(sample_pdf)
        struct_tree.set_pdfua_identifier(version=1)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            sample_pdf.save(f.name)
            temp_path = f.name

        try:
            processor = PDFProcessor()
            result = processor.detect_pdfua_version(temp_path)

            assert result.version_detected == PDFUAVersion.UA1
            assert len(result.upgrade_recommendations) > 0
            # Should recommend updating to UA-2
            assert any(
                "UA-2" in rec or "part" in rec.lower()
                for rec in result.upgrade_recommendations
            )
        finally:
            os.unlink(temp_path)


def test_add_formula():
    """PDFStructureTree.add_formula() creates a Formula element with /Alt and /AF."""
    import pikepdf
    from pikepdf import Name

    pdf = pikepdf.new()
    page = pikepdf.Page(pikepdf.Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
    }))
    pdf.pages.append(page)

    from src.education.remediation.pdf_structure import PDFStructureTree
    tree = PDFStructureTree(pdf)

    result = tree.add_formula(
        page_num=1,
        alt_text="x squared plus 2 x plus 1 equals 0",
        mathml_string="<math><msup><mi>x</mi><mn>2</mn></msup></math>",
        bbox=(72, 700, 300, 720),
    )
    assert result is True

    # Verify Formula element exists in structure tree
    kids = tree.kids
    formula_found = False
    for kid in kids:
        if hasattr(kid, "S") and str(kid.S) == "/Formula":
            formula_found = True
            assert str(kid["/Alt"]) == "x squared plus 2 x plus 1 equals 0"
            assert "/AF" in kid
            break
    assert formula_found, "Formula element not found in structure tree"


def test_add_role_mapping():
    """PDFStructureTree.add_role_mapping() extends existing RoleMap."""
    import pikepdf
    from pikepdf import Name

    pdf = pikepdf.new()
    page = pikepdf.Page(pikepdf.Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
    }))
    pdf.pages.append(page)

    from src.education.remediation.pdf_structure import PDFStructureTree
    tree = PDFStructureTree(pdf)

    from pikepdf import Name as PName

    result = tree.add_role_mapping("textbox", "Div")
    assert result is True

    role_map = tree.struct_root["/RoleMap"]
    assert PName("/textbox") in role_map
    assert str(role_map[PName("/textbox")]) == "/Div"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
