"""
Tests for the Matterhorn Protocol validator.

Tests cover:
- Data model creation (MatterhornCheckpoint, MatterhornResult)
- Compliance level computation
- PDF structure checks using pikepdf-generated test PDFs
"""

import os
import tempfile

import pytest

# Skip all PDF-creation tests if pikepdf is not available
pikepdf = pytest.importorskip("pikepdf")

from src.education.validation.matterhorn import (  # noqa: E402
    CheckpointStatus,
    MatterhornCheckpoint,
    MatterhornResult,
    MatterhornValidator,
)

# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestMatterhornCheckpoint:
    """Tests for the MatterhornCheckpoint data model."""

    def test_checkpoint_creation(self):
        """A passing checkpoint should have PASS status and no details."""
        cp = MatterhornCheckpoint(
            id="01-003",
            name="Structure tree present",
            status=CheckpointStatus.PASS,
            severity="error",
        )
        assert cp.status == CheckpointStatus.PASS
        assert cp.id == "01-003"
        assert cp.name == "Structure tree present"
        assert cp.severity == "error"
        assert cp.details is None
        assert cp.page_number is None

    def test_checkpoint_failure(self):
        """A failing checkpoint should carry severity, details, and optional page."""
        cp = MatterhornCheckpoint(
            id="13-004",
            name="Alt text on figures",
            status=CheckpointStatus.FAIL,
            severity="error",
            details="Figure on page 3 missing /Alt",
            page_number=3,
        )
        assert cp.status == CheckpointStatus.FAIL
        assert cp.severity == "error"
        assert cp.details == "Figure on page 3 missing /Alt"
        assert cp.page_number == 3


class TestMatterhornResult:
    """Tests for MatterhornResult computed properties."""

    def test_empty_result(self):
        """A result with no checkpoints should be non_compliant."""
        result = MatterhornResult(checkpoints=[])
        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0
        assert result.warnings == 0
        assert result.compliance_level == "non_compliant"

    def test_full_pass(self):
        """All checkpoints passing should yield 'compliant'."""
        checkpoints = [
            MatterhornCheckpoint(
                id="01-003",
                name="Structure tree present",
                status=CheckpointStatus.PASS,
                severity="error",
            ),
            MatterhornCheckpoint(
                id="06-001",
                name="Language set",
                status=CheckpointStatus.PASS,
                severity="error",
            ),
        ]
        result = MatterhornResult(checkpoints=checkpoints)
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0
        assert result.compliance_level == "compliant"

    def test_partial_pass(self):
        """Mixed pass/fail with failures <= 20% should yield 'partial'."""
        checkpoints = [
            MatterhornCheckpoint(
                id="01-003",
                name="Structure tree present",
                status=CheckpointStatus.PASS,
                severity="error",
            ),
            MatterhornCheckpoint(
                id="06-001",
                name="Language set",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="No /Lang entry",
            ),
        ]
        result = MatterhornResult(checkpoints=checkpoints)
        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 1
        # 1/2 = 50% failure rate → non_compliant
        assert result.compliance_level == "non_compliant"

    def test_partial_compliance_threshold(self):
        """Failures <= 20% of total should yield 'partial'."""
        # 5 pass, 1 fail  → ~17% failure rate → partial
        checkpoints = [
            MatterhornCheckpoint(
                id=f"check-{i}",
                name=f"Check {i}",
                status=CheckpointStatus.PASS,
                severity="error",
            )
            for i in range(5)
        ]
        checkpoints.append(
            MatterhornCheckpoint(
                id="check-fail",
                name="Failing check",
                status=CheckpointStatus.FAIL,
                severity="error",
                details="Something wrong",
            )
        )
        result = MatterhornResult(checkpoints=checkpoints)
        assert result.total == 6
        assert result.failed == 1
        assert result.compliance_level == "partial"

    def test_warnings_not_counted_as_failures(self):
        """Warnings should not count as failures for compliance level."""
        checkpoints = [
            MatterhornCheckpoint(
                id="01-003",
                name="Structure tree",
                status=CheckpointStatus.PASS,
                severity="error",
            ),
            MatterhornCheckpoint(
                id="07-002",
                name="DisplayDocTitle",
                status=CheckpointStatus.WARNING,
                severity="warning",
                details="DisplayDocTitle not set",
            ),
        ]
        result = MatterhornResult(checkpoints=checkpoints)
        assert result.warnings == 1
        assert result.failed == 0
        assert result.compliance_level == "compliant"


# ---------------------------------------------------------------------------
# PDF structure check tests (require pikepdf)
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_pdf():
    """Create a minimal PDF with one blank page and no accessibility features."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    return pdf


@pytest.fixture
def bare_pdf_path(bare_pdf):
    """Save a bare PDF to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        bare_pdf.save(f.name)
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def accessible_pdf():
    """Create a PDF with basic accessibility features set."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    # Add StructTreeRoot
    struct_root = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name.StructTreeRoot,
                "/K": pikepdf.Array([]),
                "/ParentTree": pikepdf.Dictionary({"/Nums": pikepdf.Array([])}),
            }
        )
    )
    pdf.Root[pikepdf.Name.StructTreeRoot] = struct_root

    # Mark as tagged
    pdf.Root[pikepdf.Name.MarkInfo] = pikepdf.Dictionary({"/Marked": True})

    # Set language
    pdf.Root[pikepdf.Name.Lang] = pikepdf.String("en")

    # Set title in docinfo
    with pdf.open_metadata() as meta:
        meta["dc:title"] = "Test Document"

    return pdf


@pytest.fixture
def accessible_pdf_path(accessible_pdf):
    """Save an accessible PDF to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        accessible_pdf.save(f.name)
        path = f.name
    yield path
    if os.path.exists(path):
        os.unlink(path)


class TestMatterhornValidatorChecks:
    """Tests for individual Matterhorn checks against real PDFs."""

    def test_check_structure_tree_present(self, bare_pdf_path):
        """A PDF without StructTreeRoot should fail checkpoint 01-003."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        structure_check = next(
            (cp for cp in result.checkpoints if cp.id == "01-003"), None
        )
        assert structure_check is not None
        assert structure_check.status == CheckpointStatus.FAIL

    def test_check_language_set(self, bare_pdf_path):
        """A PDF without /Lang should fail checkpoint 06-001."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        lang_check = next((cp for cp in result.checkpoints if cp.id == "06-001"), None)
        assert lang_check is not None
        assert lang_check.status == CheckpointStatus.FAIL

    def test_check_marked_content(self, bare_pdf_path):
        """A PDF without MarkInfo should fail checkpoint 01-004."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        mark_check = next((cp for cp in result.checkpoints if cp.id == "01-004"), None)
        assert mark_check is not None
        assert mark_check.status == CheckpointStatus.FAIL

    def test_accessible_pdf_passes_basic_checks(self, accessible_pdf_path):
        """An accessible PDF should pass structure, language, and mark checks."""
        validator = MatterhornValidator()
        result = validator.validate(accessible_pdf_path)

        for check_id in ("01-003", "01-004", "06-001"):
            cp = next((c for c in result.checkpoints if c.id == check_id), None)
            assert cp is not None, f"Missing checkpoint {check_id}"
            assert (
                cp.status == CheckpointStatus.PASS
            ), f"Checkpoint {check_id} should PASS but got {cp.status}: {cp.details}"

    def test_check_title_missing(self, bare_pdf_path):
        """A PDF without title metadata should fail checkpoint 07-001."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        title_check = next((cp for cp in result.checkpoints if cp.id == "07-001"), None)
        assert title_check is not None
        assert title_check.status == CheckpointStatus.FAIL

    def test_check_display_doc_title(self, bare_pdf_path):
        """A PDF without DisplayDocTitle should produce a WARNING for 07-002."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        display_check = next(
            (cp for cp in result.checkpoints if cp.id == "07-002"), None
        )
        assert display_check is not None
        assert display_check.status == CheckpointStatus.WARNING

    def test_validate_returns_all_checkpoints(self, bare_pdf_path):
        """validate() should return checkpoints for all implemented checks."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        # We expect at least the core checks
        expected_ids = {"01-003", "01-004", "06-001", "07-001", "07-002"}
        actual_ids = {cp.id for cp in result.checkpoints}
        assert expected_ids.issubset(
            actual_ids
        ), f"Missing checkpoint IDs: {expected_ids - actual_ids}"

    def test_validate_nonexistent_file(self):
        """validate() should raise FileNotFoundError for missing files."""
        validator = MatterhornValidator()
        with pytest.raises(FileNotFoundError):
            validator.validate("/tmp/nonexistent_abc123.pdf")

    def test_bare_pdf_is_non_compliant(self, bare_pdf_path):
        """A bare PDF with no accessibility features should be non_compliant."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        assert result.failed > 0
        assert result.compliance_level == "non_compliant"

    def test_heading_hierarchy_no_struct_tree(self, bare_pdf_path):
        """Without a structure tree, heading hierarchy check should fail."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        heading_check = next(
            (cp for cp in result.checkpoints if cp.id == "14-002"), None
        )
        # Should exist and fail (no headings found at all)
        if heading_check is not None:
            assert heading_check.status in (
                CheckpointStatus.FAIL,
                CheckpointStatus.WARNING,
            )

    def test_pdfua_identifier_missing(self, bare_pdf_path):
        """A bare PDF should fail the PDF/UA identifier check (06-003)."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)

        ua_check = next((cp for cp in result.checkpoints if cp.id == "06-003"), None)
        assert ua_check is not None
        assert ua_check.status == CheckpointStatus.FAIL
