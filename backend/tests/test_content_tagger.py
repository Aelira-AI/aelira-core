"""
Unit tests for PDF content stream tagger.

Tests the BDC/EMC marked content insertion, MCID assignment,
ParentTree construction, and PDF/UA-1 identifier setting.
"""

import pytest
import tempfile
import os

# Skip all tests if pikepdf is not available
pikepdf = pytest.importorskip("pikepdf")

from pikepdf import Array, Dictionary, Name, Operator, String  # noqa: E402

from src.education.remediation.content_tagger import (  # noqa: E402
    BlockType,
    ContentBlock,
    ContentTagger,
    parse_content_blocks,
)
from src.education.remediation.pdf_structure import (  # noqa: E402
    PDFStructureTree,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def blank_pdf():
    """Create a blank single-page PDF."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


@pytest.fixture
def pdf_with_text():
    """Create a PDF with text content in the content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = b"BT /F1 12 Tf 100 700 Td (Hello World) Tj ET"
    page.obj[Name.Contents] = pdf.make_stream(content)
    return pdf


@pytest.fixture
def pdf_with_multi_text():
    """Create a PDF with multiple text blocks."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = (
        b"BT /F1 12 Tf 100 700 Td (First Block) Tj ET\n"
        b"BT /F1 10 Tf 100 680 Td (Second Block) Tj ET"
    )
    page.obj[Name.Contents] = pdf.make_stream(content)
    return pdf


@pytest.fixture
def pdf_with_image():
    """Create a PDF with an image reference in the content stream."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = b"q 100 0 0 100 50 600 cm /Im0 Do Q"
    page.obj[Name.Contents] = pdf.make_stream(content)
    return pdf


@pytest.fixture
def pdf_with_text_and_image():
    """Create a PDF with both text and image content."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = (
        b"BT /F1 12 Tf 100 700 Td (Heading Text) Tj ET\n"
        b"q 100 0 0 100 50 600 cm /Im0 Do Q\n"
        b"BT /F1 10 Tf 100 500 Td (Body text here) Tj ET"
    )
    page.obj[Name.Contents] = pdf.make_stream(content)
    return pdf


@pytest.fixture
def pdf_with_tj_array():
    """Create a PDF with TJ array text operator."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = b"BT /F1 12 Tf 100 700 Td [(Hello) -50 ( World)] TJ ET"
    page.obj[Name.Contents] = pdf.make_stream(content)
    return pdf


@pytest.fixture
def pdf_multipage():
    """Create a multi-page PDF with content."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.add_blank_page(page_size=(612, 792))

    page0 = pdf.pages[0]
    page0.obj[Name.Contents] = pdf.make_stream(
        b"BT /F1 12 Tf 100 700 Td (Page One) Tj ET"
    )

    page1 = pdf.pages[1]
    page1.obj[Name.Contents] = pdf.make_stream(
        b"BT /F1 12 Tf 100 700 Td (Page Two) Tj ET"
    )
    return pdf


@pytest.fixture
def structured_pdf():
    """Create a PDF with structure elements but no MCIDs."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = (
        b"BT /F1 14 Tf 100 700 Td (Introduction) Tj ET\n"
        b"BT /F1 10 Tf 100 680 Td (Some body text for the paragraph.) Tj ET"
    )
    page.obj[Name.Contents] = pdf.make_stream(content)

    # Add structure elements via PDFStructureTree
    tree = PDFStructureTree(pdf)
    tree.set_document_language("en")
    tree.add_heading(page_num=1, level=1, text="Introduction")
    tree.add_paragraph(page_num=1, text="Some body text for the paragraph.")
    return pdf


@pytest.fixture
def structured_pdf_with_image():
    """Create a PDF with structure elements including a Figure."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    content = (
        b"BT /F1 14 Tf 100 700 Td (Title) Tj ET\n"
        b"q 200 0 0 200 50 400 cm /Im0 Do Q"
    )
    page.obj[Name.Contents] = pdf.make_stream(content)

    tree = PDFStructureTree(pdf)
    tree.set_document_language("en")
    tree.add_heading(page_num=1, level=1, text="Title")
    tree.add_alt_text_to_image(page_num=1, alt_text="A descriptive image")
    return pdf


# ---------------------------------------------------------------------------
# Tests: parse_content_blocks
# ---------------------------------------------------------------------------

class TestParseContentBlocks:
    """Tests for content stream block parsing."""

    def test_parse_empty_page(self, blank_pdf):
        """Blank page should yield no content blocks."""
        page = blank_pdf.pages[0]
        blocks = parse_content_blocks(page)
        # Blank page may have an empty stream
        assert isinstance(blocks, list)

    def test_parse_single_text_block(self, pdf_with_text):
        """Single BT/ET pair produces one TEXT block."""
        page = pdf_with_text.pages[0]
        blocks = parse_content_blocks(page)
        assert len(blocks) == 1
        assert blocks[0].block_type == BlockType.TEXT

    def test_parse_multiple_text_blocks(self, pdf_with_multi_text):
        """Multiple BT/ET pairs produce multiple TEXT blocks."""
        page = pdf_with_multi_text.pages[0]
        blocks = parse_content_blocks(page)
        assert len(blocks) == 2
        assert all(b.block_type == BlockType.TEXT for b in blocks)

    def test_parse_image_block(self, pdf_with_image):
        """Do operator produces an IMAGE block."""
        page = pdf_with_image.pages[0]
        blocks = parse_content_blocks(page)
        assert len(blocks) == 1
        assert blocks[0].block_type == BlockType.IMAGE

    def test_parse_mixed_content(self, pdf_with_text_and_image):
        """Mixed text and image content parsed correctly."""
        page = pdf_with_text_and_image.pages[0]
        blocks = parse_content_blocks(page)
        assert len(blocks) == 3
        types = [b.block_type for b in blocks]
        assert types == [BlockType.TEXT, BlockType.IMAGE, BlockType.TEXT]

    def test_parse_tj_array(self, pdf_with_tj_array):
        """TJ array operator within BT/ET is a single text block."""
        page = pdf_with_tj_array.pages[0]
        blocks = parse_content_blocks(page)
        assert len(blocks) == 1
        assert blocks[0].block_type == BlockType.TEXT

    def test_block_indices_are_ordered(self, pdf_with_text_and_image):
        """Block start/end indices should be in ascending order."""
        page = pdf_with_text_and_image.pages[0]
        blocks = parse_content_blocks(page)
        for i in range(len(blocks)):
            assert blocks[i].start_index < blocks[i].end_index
        for i in range(len(blocks) - 1):
            assert blocks[i].end_index <= blocks[i + 1].start_index


# ---------------------------------------------------------------------------
# Tests: ContentTagger basic operations
# ---------------------------------------------------------------------------

class TestContentTaggerBasic:
    """Tests for ContentTagger initialization and document root."""

    def test_init_requires_struct_tree(self, blank_pdf):
        """ContentTagger should create StructTreeRoot if missing."""
        tagger = ContentTagger(blank_pdf)
        assert Name.StructTreeRoot in blank_pdf.Root

    def test_ensure_document_root(self, structured_pdf):
        """Tagger should wrap existing elements under a Document root."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        kids = struct_root[Name.K]

        # Should have exactly one Document element as child
        if isinstance(kids, Array):
            assert len(kids) == 1
            doc_elem = kids[0]
        else:
            doc_elem = kids

        assert str(doc_elem[Name.S]) == "/Document"

    def test_document_root_not_duplicated(self, structured_pdf):
        """Calling tag_all_pages twice should not create duplicate Document roots."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        kids = struct_root[Name.K]
        if isinstance(kids, Array):
            doc_count = sum(
                1 for k in kids
                if hasattr(k, "keys") and Name.S in k and str(k[Name.S]) == "/Document"
            )
            assert doc_count == 1


# ---------------------------------------------------------------------------
# Tests: BDC/EMC marker insertion
# ---------------------------------------------------------------------------

class TestMarkerInsertion:
    """Tests that BDC/EMC markers are inserted into content streams."""

    def test_single_text_block_gets_markers(self, pdf_with_text):
        """A single text block should be wrapped with BDC/EMC."""
        tree = PDFStructureTree(pdf_with_text)
        tree.add_paragraph(page_num=1, text="Hello World")

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        page = pdf_with_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]

        assert "BDC" in op_names
        assert "EMC" in op_names

    def test_bdc_before_bt(self, pdf_with_text):
        """BDC must appear before BT for a text block."""
        tree = PDFStructureTree(pdf_with_text)
        tree.add_paragraph(page_num=1, text="Hello World")

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        page = pdf_with_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]

        bdc_idx = op_names.index("BDC")
        bt_idx = op_names.index("BT")
        assert bdc_idx < bt_idx

    def test_emc_after_et(self, pdf_with_text):
        """EMC must appear after ET for a text block."""
        tree = PDFStructureTree(pdf_with_text)
        tree.add_paragraph(page_num=1, text="Hello World")

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        page = pdf_with_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]

        # Find last ET and EMC
        et_indices = [i for i, n in enumerate(op_names) if n == "ET"]
        emc_indices = [i for i, n in enumerate(op_names) if n == "EMC"]
        assert len(emc_indices) >= 1
        assert emc_indices[0] > et_indices[0]

    def test_multiple_blocks_get_separate_markers(self, pdf_with_multi_text):
        """Each text block should get its own BDC/EMC pair."""
        tree = PDFStructureTree(pdf_with_multi_text)
        tree.add_paragraph(page_num=1, text="First Block")
        tree.add_paragraph(page_num=1, text="Second Block")

        tagger = ContentTagger(pdf_with_multi_text)
        tagger.tag_all_pages()

        page = pdf_with_multi_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]

        assert op_names.count("BDC") >= 2
        assert op_names.count("EMC") >= 2

    def test_image_block_gets_markers(self, pdf_with_image):
        """Image blocks (Do) should be wrapped with BDC/EMC."""
        tree = PDFStructureTree(pdf_with_image)
        tree.add_alt_text_to_image(page_num=1, alt_text="Test image")

        tagger = ContentTagger(pdf_with_image)
        tagger.tag_all_pages()

        page = pdf_with_image.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]

        assert "BDC" in op_names
        assert "EMC" in op_names


# ---------------------------------------------------------------------------
# Tests: MCID assignment and structure element /K linking
# ---------------------------------------------------------------------------

class TestMCIDAssignment:
    """Tests for MCID numbering and /K entries on structure elements."""

    def test_mcid_in_bdc_dict(self, pdf_with_text):
        """BDC operand dict should contain /MCID integer."""
        tree = PDFStructureTree(pdf_with_text)
        tree.add_paragraph(page_num=1, text="Hello World")

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        page = pdf_with_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))

        bdc_ops = [op for op in ops if str(op.operator) == "BDC"]
        assert len(bdc_ops) >= 1

        # The second operand of BDC should be a dict with /MCID
        bdc = bdc_ops[0]
        props = bdc.operands[1]
        assert Name.MCID in props

    def test_mcids_are_per_page(self, pdf_multipage):
        """MCIDs should reset to 0 on each new page."""
        tree = PDFStructureTree(pdf_multipage)
        tree.add_paragraph(page_num=1, text="Page One")
        tree.add_paragraph(page_num=2, text="Page Two")

        tagger = ContentTagger(pdf_multipage)
        tagger.tag_all_pages()

        for page_idx in range(2):
            page = pdf_multipage.pages[page_idx]
            ops = list(pikepdf.parse_content_stream(page))
            bdc_ops = [op for op in ops if str(op.operator) == "BDC"]
            assert len(bdc_ops) >= 1
            first_mcid = int(bdc_ops[0].operands[1][Name.MCID])
            assert first_mcid == 0

    def test_structure_elements_get_k_entry(self, structured_pdf):
        """After tagging, structure elements should have /K with MCR dicts."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        doc_elem = struct_root[Name.K]
        if isinstance(doc_elem, Array):
            doc_elem = doc_elem[0]

        doc_kids = doc_elem[Name.K]
        if not isinstance(doc_kids, Array):
            doc_kids = Array([doc_kids])

        # At least one child element should have /K with /MCID
        found_mcid = False
        for kid in doc_kids:
            if Name.K in kid:
                k_val = kid[Name.K]
                if isinstance(k_val, Dictionary) and Name.MCID in k_val:
                    found_mcid = True
                    break
                elif isinstance(k_val, Array):
                    for item in k_val:
                        if isinstance(item, Dictionary) and Name.MCID in item:
                            found_mcid = True
                            break
        assert found_mcid, "No structure element has /K with /MCID after tagging"


# ---------------------------------------------------------------------------
# Tests: ParentTree
# ---------------------------------------------------------------------------

class TestParentTree:
    """Tests for ParentTree /Nums construction."""

    def test_parent_tree_populated(self, structured_pdf):
        """ParentTree /Nums should be populated after tagging."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        parent_tree = struct_root[Name.ParentTree]
        nums = parent_tree[Name.Nums]

        # Should have at least one page entry: [page_idx, [elem_refs...]]
        assert len(nums) >= 2  # At least one pair

    def test_parent_tree_alternates_index_and_array(self, structured_pdf):
        """ParentTree /Nums should alternate: int, array, int, array, ..."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        nums = struct_root[Name.ParentTree][Name.Nums]

        # Even indices should be integers (page indices)
        # Odd indices should be arrays
        for i in range(0, len(nums), 2):
            assert isinstance(int(nums[i]), int)
        for i in range(1, len(nums), 2):
            assert isinstance(nums[i], Array)

    def test_parent_tree_array_length_matches_mcids(self, pdf_with_multi_text):
        """ParentTree array for a page should have one entry per MCID."""
        tree = PDFStructureTree(pdf_with_multi_text)
        tree.add_paragraph(page_num=1, text="First Block")
        tree.add_paragraph(page_num=1, text="Second Block")

        tagger = ContentTagger(pdf_with_multi_text)
        tagger.tag_all_pages()

        # Count BDC ops on the page
        page = pdf_with_multi_text.pages[0]
        ops = list(pikepdf.parse_content_stream(page))
        bdc_count = sum(1 for op in ops if str(op.operator) == "BDC")

        struct_root = pdf_with_multi_text.Root[Name.StructTreeRoot]
        nums = struct_root[Name.ParentTree][Name.Nums]

        # First page entry
        page_array = nums[1]  # index 0 is page_idx=0, index 1 is the array
        assert len(page_array) == bdc_count


# ---------------------------------------------------------------------------
# Tests: StructParents on pages
# ---------------------------------------------------------------------------

class TestStructParents:
    """Tests for /StructParents entries on pages."""

    def test_page_gets_struct_parents(self, structured_pdf):
        """Each tagged page should get a /StructParents entry."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        page = structured_pdf.pages[0]
        assert Name.StructParents in page.obj

    def test_struct_parents_value_matches_parent_tree(self, pdf_multipage):
        """StructParents on page should match its index in ParentTree."""
        tree = PDFStructureTree(pdf_multipage)
        tree.add_paragraph(page_num=1, text="Page One")
        tree.add_paragraph(page_num=2, text="Page Two")

        tagger = ContentTagger(pdf_multipage)
        tagger.tag_all_pages()

        struct_root = pdf_multipage.Root[Name.StructTreeRoot]
        nums = struct_root[Name.ParentTree][Name.Nums]

        for page_idx in range(2):
            page = pdf_multipage.pages[page_idx]
            sp_val = int(page.obj[Name.StructParents])

            # Verify this index appears in ParentTree
            found = False
            for i in range(0, len(nums), 2):
                if int(nums[i]) == sp_val:
                    found = True
                    break
            assert found, f"StructParents={sp_val} not found in ParentTree"


# ---------------------------------------------------------------------------
# Tests: Element matching
# ---------------------------------------------------------------------------

class TestElementMatching:
    """Tests for matching content blocks to structure elements."""

    def test_heading_matched_by_text(self, structured_pdf):
        """H1 element should be matched to text block containing 'Introduction'."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        struct_root = structured_pdf.Root[Name.StructTreeRoot]
        doc_elem = struct_root[Name.K]
        if isinstance(doc_elem, Array):
            doc_elem = doc_elem[0]

        doc_kids = doc_elem[Name.K]
        if not isinstance(doc_kids, Array):
            doc_kids = Array([doc_kids])

        # Find the H1 element
        h1_elem = None
        for kid in doc_kids:
            if Name.S in kid and str(kid[Name.S]) == "/H1":
                h1_elem = kid
                break

        assert h1_elem is not None, "H1 element should exist"
        assert Name.K in h1_elem, "H1 should have /K after tagging"

    def test_image_matched_to_figure(self, structured_pdf_with_image):
        """Figure element should be linked to image content block."""
        tagger = ContentTagger(structured_pdf_with_image)
        tagger.tag_all_pages()

        struct_root = structured_pdf_with_image.Root[Name.StructTreeRoot]
        doc_elem = struct_root[Name.K]
        if isinstance(doc_elem, Array):
            doc_elem = doc_elem[0]

        doc_kids = doc_elem[Name.K]
        if not isinstance(doc_kids, Array):
            doc_kids = Array([doc_kids])

        # Find the Figure element
        fig_elem = None
        for kid in doc_kids:
            if Name.S in kid and str(kid[Name.S]) == "/Figure":
                fig_elem = kid
                break

        assert fig_elem is not None, "Figure element should exist"
        assert Name.K in fig_elem, "Figure should have /K after tagging"

    def test_unmatched_blocks_create_elements(self, pdf_with_text):
        """Text blocks without matching elements should get new P elements."""
        # No structure elements added -- all blocks are unmatched
        tree = PDFStructureTree(pdf_with_text)

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        struct_root = pdf_with_text.Root[Name.StructTreeRoot]
        doc_elem = struct_root[Name.K]
        if isinstance(doc_elem, Array):
            doc_elem = doc_elem[0]

        # Should have created at least one child element
        assert Name.K in doc_elem
        doc_kids = doc_elem[Name.K]
        if not isinstance(doc_kids, Array):
            doc_kids = Array([doc_kids])
        assert len(doc_kids) >= 1


# ---------------------------------------------------------------------------
# Tests: PDF/UA-1 identifier
# ---------------------------------------------------------------------------

class TestPDFUAIdentifier:
    """Tests for PDF/UA-1 XMP metadata."""

    def test_pdfua1_identifier_set(self, structured_pdf):
        """tag_all_pages should set PDF/UA-1 identifier in XMP."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        with structured_pdf.open_metadata() as meta:
            part = meta.get("{http://www.aiim.org/pdfua/ns/id/}part")
            assert part == "1"

    def test_mark_info_set(self, structured_pdf):
        """tag_all_pages should ensure MarkInfo.Marked is true."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        assert Name.MarkInfo in structured_pdf.Root
        assert bool(structured_pdf.Root[Name.MarkInfo][Name.Marked]) is True


# ---------------------------------------------------------------------------
# Tests: Round-trip save/load
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Tests that tagged PDFs survive save and reload."""

    def test_save_and_reload_preserves_tags(self, structured_pdf):
        """Tags should survive save-to-disk and reload."""
        tagger = ContentTagger(structured_pdf)
        tagger.tag_all_pages()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            structured_pdf.save(f.name)
            temp_path = f.name

        try:
            with pikepdf.open(temp_path) as reloaded:
                page = reloaded.pages[0]
                ops = list(pikepdf.parse_content_stream(page))
                op_names = [str(op.operator) for op in ops]

                assert "BDC" in op_names
                assert "EMC" in op_names

                # Structure tree should exist
                assert Name.StructTreeRoot in reloaded.Root
                struct_root = reloaded.Root[Name.StructTreeRoot]

                # ParentTree should be populated
                nums = struct_root[Name.ParentTree][Name.Nums]
                assert len(nums) >= 2
        finally:
            os.unlink(temp_path)

    def test_full_workflow_integration(self):
        """Full workflow: create PDF, add content, add structure, tag, save, verify."""
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[0]

        content = (
            b"BT /F1 14 Tf 72 720 Td (Chapter 1: Getting Started) Tj ET\n"
            b"BT /F1 10 Tf 72 700 Td (Welcome to the guide.) Tj ET\n"
            b"q 300 0 0 200 72 450 cm /Im0 Do Q\n"
            b"BT /F1 10 Tf 72 420 Td (Figure 1 shows the overview.) Tj ET"
        )
        page.obj[Name.Contents] = pdf.make_stream(content)

        tree = PDFStructureTree(pdf)
        tree.set_document_language("en")
        tree.add_heading(page_num=1, level=1, text="Chapter 1: Getting Started")
        tree.add_paragraph(page_num=1, text="Welcome to the guide.")
        tree.add_alt_text_to_image(page_num=1, alt_text="Overview diagram")
        tree.add_paragraph(
            page_num=1, text="Figure 1 shows the overview."
        )

        tagger = ContentTagger(pdf)
        tagger.tag_all_pages()

        # Verify markers
        ops = list(pikepdf.parse_content_stream(pdf.pages[0]))
        op_names = [str(op.operator) for op in ops]
        assert op_names.count("BDC") == 4
        assert op_names.count("EMC") == 4

        # Verify ParentTree
        struct_root = pdf.Root[Name.StructTreeRoot]
        nums = struct_root[Name.ParentTree][Name.Nums]
        assert len(nums) >= 2

        # Save and verify it opens without error
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pdf.save(f.name)
            temp_path = f.name

        try:
            with pikepdf.open(temp_path) as reloaded:
                assert Name.StructTreeRoot in reloaded.Root
        finally:
            os.unlink(temp_path)


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_empty_content_stream(self, blank_pdf):
        """Tagging a page with no content should not raise."""
        tree = PDFStructureTree(blank_pdf)
        tagger = ContentTagger(blank_pdf)
        tagger.tag_all_pages()  # Should not raise

    def test_already_tagged_content_skipped(self, pdf_with_text):
        """Content that already has BDC/EMC should not be double-tagged."""
        tree = PDFStructureTree(pdf_with_text)
        tree.add_paragraph(page_num=1, text="Hello World")

        tagger = ContentTagger(pdf_with_text)
        tagger.tag_all_pages()

        # Count markers after first tagging
        page = pdf_with_text.pages[0]
        ops1 = list(pikepdf.parse_content_stream(page))
        bdc_count_1 = sum(1 for op in ops1 if str(op.operator) == "BDC")

        # Tag again
        tagger2 = ContentTagger(pdf_with_text)
        tagger2.tag_all_pages()

        ops2 = list(pikepdf.parse_content_stream(page))
        bdc_count_2 = sum(1 for op in ops2 if str(op.operator) == "BDC")

        assert bdc_count_2 == bdc_count_1, "Double-tagging should not add more BDC ops"

    def test_content_stream_array(self):
        """Pages with Contents as an Array of streams should be handled."""
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[0]

        s1 = pdf.make_stream(b"BT /F1 12 Tf 100 700 Td (Part One) Tj ET")
        s2 = pdf.make_stream(b"BT /F1 10 Tf 100 680 Td (Part Two) Tj ET")
        page.obj[Name.Contents] = Array([s1, s2])

        tree = PDFStructureTree(pdf)
        tagger = ContentTagger(pdf)
        tagger.tag_all_pages()

        ops = list(pikepdf.parse_content_stream(page))
        op_names = [str(op.operator) for op in ops]
        assert "BDC" in op_names
        assert "EMC" in op_names
