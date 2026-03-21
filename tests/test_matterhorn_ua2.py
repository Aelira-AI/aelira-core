"""
Tests for PDF/UA-2 support in the Matterhorn Protocol validator.

Tests cover:
- UA version detection from XMP metadata
- UA-2 namespace validation checks
- Pronunciation attribute checks
- Ruby annotation checks
- MathML structure checks
- Associated files checks
- Artifact handling checks
- Backward compatibility (default ua_version=1 runs only UA-1 checks)
- ua_version=2 runs both UA-1 and UA-2 checks
- MatterhornResult includes ua_version field
"""

import os
import tempfile
from unittest.mock import MagicMock, patch
from xml.etree import ElementTree

import pytest

# Skip all tests if pikepdf is not available
pikepdf = pytest.importorskip("pikepdf")

from src.education.validation.matterhorn import (  # noqa: E402
    CheckpointStatus,
    MatterhornCheckpoint,
    MatterhornResult,
    MatterhornValidator,
)


# ---------------------------------------------------------------------------
# Fixtures
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
    """Create a PDF with basic accessibility features (UA-1 style)."""
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

    # Set title in XMP metadata
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


def _make_struct_element(pdf, element_type: str, **extra) -> pikepdf.Dictionary:
    """Helper to create a structure element dictionary."""
    d = {"/S": pikepdf.Name(f"/{element_type}"), "/K": pikepdf.Array([])}
    d.update(extra)
    return pdf.make_indirect(pikepdf.Dictionary(d))


def _save_pdf(pdf) -> str:
    """Save a PDF to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf.save(f.name)
        return f.name


# ---------------------------------------------------------------------------
# MatterhornResult ua_version field tests
# ---------------------------------------------------------------------------


class TestMatterhornResultUAVersion:
    """Tests for the ua_version field on MatterhornResult."""

    def test_result_has_ua_version_field(self):
        """MatterhornResult should include ua_version field."""
        result = MatterhornResult(checkpoints=[], ua_version=1)
        assert result.ua_version == 1

    def test_result_ua_version_default(self):
        """MatterhornResult should default ua_version to 1."""
        result = MatterhornResult(checkpoints=[])
        assert result.ua_version == 1

    def test_result_ua_version_2(self):
        """MatterhornResult should accept ua_version=2."""
        result = MatterhornResult(checkpoints=[], ua_version=2)
        assert result.ua_version == 2


# ---------------------------------------------------------------------------
# UA version detection tests
# ---------------------------------------------------------------------------


class TestUAVersionDetection:
    """Tests for auto-detecting PDF/UA version from XMP metadata."""

    def test_detect_ua1_from_xmp(self, accessible_pdf):
        """Should detect UA-1 from pdfuaid:part=1 in XMP metadata."""
        validator = MatterhornValidator()
        # Set pdfuaid:part to 1
        with accessible_pdf.open_metadata() as meta:
            meta["pdfuaid:part"] = "1"

        path = _save_pdf(accessible_pdf)
        try:
            result = validator.validate(path, ua_version="auto")
            assert result.ua_version == 1
        finally:
            os.unlink(path)

    def test_detect_ua2_from_xmp(self, accessible_pdf):
        """Should detect UA-2 from pdfuaid:part=2 in XMP metadata."""
        validator = MatterhornValidator()
        with accessible_pdf.open_metadata() as meta:
            meta["pdfuaid:part"] = "2"

        path = _save_pdf(accessible_pdf)
        try:
            result = validator.validate(path, ua_version="auto")
            assert result.ua_version == 2
        finally:
            os.unlink(path)

    def test_detect_defaults_to_ua1_when_no_xmp(self, bare_pdf_path):
        """Should default to UA-1 when no pdfuaid:part is found."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version="auto")
        assert result.ua_version == 1

    def test_explicit_ua_version_1(self, bare_pdf_path):
        """Explicit ua_version=1 should override auto-detection."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=1)
        assert result.ua_version == 1

    def test_explicit_ua_version_2(self, bare_pdf_path):
        """Explicit ua_version=2 should force UA-2 checks."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)
        assert result.ua_version == 2


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Tests verifying UA-1 behavior is preserved."""

    def test_default_ua_version_is_1(self, bare_pdf_path):
        """Default validate() call should use ua_version=1."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path)
        assert result.ua_version == 1

    def test_ua1_no_ua2_checkpoints(self, bare_pdf_path):
        """With ua_version=1, no UA-2 specific checkpoints should appear."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=1)
        ua2_checkpoints = [cp for cp in result.checkpoints if cp.id.startswith("ua2-")]
        assert len(ua2_checkpoints) == 0

    def test_ua2_includes_ua1_checks(self, bare_pdf_path):
        """With ua_version=2, UA-1 checks should still run."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        # UA-1 checks should be present
        ua1_ids = {cp.id for cp in result.checkpoints if not cp.id.startswith("ua2-")}
        assert "01-003" in ua1_ids  # structure tree
        assert "01-004" in ua1_ids  # mark info
        assert "06-001" in ua1_ids  # language

    def test_ua2_includes_ua2_checks(self, bare_pdf_path):
        """With ua_version=2, UA-2 specific checkpoints should appear."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ua2_checkpoints = [cp for cp in result.checkpoints if cp.id.startswith("ua2-")]
        assert len(ua2_checkpoints) > 0

    def test_ua1_result_same_checkpoint_ids(self, bare_pdf_path):
        """ua_version=1 should produce the same checkpoint IDs as before."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=1)

        expected_ids = {"01-003", "01-004", "06-001", "07-001", "07-002"}
        actual_ids = {cp.id for cp in result.checkpoints}
        assert expected_ids.issubset(actual_ids)


# ---------------------------------------------------------------------------
# UA-2 Namespace validation tests
# ---------------------------------------------------------------------------


class TestUA2NamespaceValidation:
    """Tests for UA-2 namespace validation checks."""

    def test_namespace_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include namespace checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ns_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-01-001"), None
        )
        assert ns_check is not None
        assert ns_check.name == "PDF 2.0 namespace declarations"

    def test_namespace_pass_with_namespaces(self, accessible_pdf):
        """PDF with namespace entries should pass namespace check."""
        # Add namespace array to struct tree root
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]
        ns_dict = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Namespace"),
                    "/NS": pikepdf.String("http://iso.org/pdf2/ssn"),
                }
            )
        )
        struct_root[pikepdf.Name("/Namespaces")] = pikepdf.Array([ns_dict])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            ns_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-01-001"), None
            )
            assert ns_check is not None
            assert ns_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_namespace_fail_without_namespaces(self, accessible_pdf):
        """PDF without namespace declarations should fail namespace check in UA-2."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            ns_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-01-001"), None
            )
            assert ns_check is not None
            assert ns_check.status in (CheckpointStatus.FAIL, CheckpointStatus.WARNING)
        finally:
            os.unlink(path)

    def test_namespace_fail_no_struct_tree(self, bare_pdf_path):
        """Without StructTreeRoot, namespace check should fail."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ns_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-01-001"), None
        )
        assert ns_check is not None
        assert ns_check.status == CheckpointStatus.FAIL


# ---------------------------------------------------------------------------
# UA-2 Pronunciation attribute tests
# ---------------------------------------------------------------------------


class TestUA2Pronunciation:
    """Tests for UA-2 pronunciation attribute checks."""

    def test_pronunciation_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include pronunciation checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        pron_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-02-001"), None
        )
        assert pron_check is not None
        assert "pronunciation" in pron_check.name.lower() or "phoneme" in pron_check.name.lower()

    def test_pronunciation_pass_with_phoneme(self, accessible_pdf):
        """Structure elements with /Phoneme and /PhoneticAlphabet should pass."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        # Create a Span element with pronunciation attributes
        span = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/Span"),
                    "/K": pikepdf.Array([]),
                    "/Phoneme": pikepdf.String("hEloU"),
                    "/PhoneticAlphabet": pikepdf.String("ipa"),
                }
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([span])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            pron_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-02-001"), None
            )
            assert pron_check is not None
            assert pron_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_pronunciation_warning_phoneme_without_alphabet(self, accessible_pdf):
        """Phoneme without PhoneticAlphabet should warn."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        span = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/Span"),
                    "/K": pikepdf.Array([]),
                    "/Phoneme": pikepdf.String("hEloU"),
                    # Missing /PhoneticAlphabet
                }
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([span])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            pron_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-02-001"), None
            )
            assert pron_check is not None
            assert pron_check.status == CheckpointStatus.WARNING
        finally:
            os.unlink(path)

    def test_pronunciation_pass_no_phoneme_elements(self, accessible_pdf):
        """No pronunciation elements at all is acceptable (pass)."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            pron_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-02-001"), None
            )
            assert pron_check is not None
            # No elements with Phoneme is fine - just means no pronunciation markup
            assert pron_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# UA-2 Ruby annotation tests
# ---------------------------------------------------------------------------


class TestUA2Ruby:
    """Tests for UA-2 ruby annotation checks."""

    def test_ruby_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include ruby annotation checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ruby_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-03-001"), None
        )
        assert ruby_check is not None
        assert "ruby" in ruby_check.name.lower()

    def test_ruby_pass_complete_structure(self, accessible_pdf):
        """Ruby element with RB and RT children should pass."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        rb = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {"/S": pikepdf.Name("/RB"), "/K": pikepdf.Array([])}
            )
        )
        rt = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {"/S": pikepdf.Name("/RT"), "/K": pikepdf.Array([])}
            )
        )
        ruby = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {"/S": pikepdf.Name("/Ruby"), "/K": pikepdf.Array([rb, rt])}
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([ruby])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            ruby_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-03-001"), None
            )
            assert ruby_check is not None
            assert ruby_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_ruby_fail_missing_children(self, accessible_pdf):
        """Ruby element without RB or RT children should fail."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        # Ruby with no children
        ruby = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {"/S": pikepdf.Name("/Ruby"), "/K": pikepdf.Array([])}
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([ruby])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            ruby_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-03-001"), None
            )
            assert ruby_check is not None
            assert ruby_check.status == CheckpointStatus.FAIL
        finally:
            os.unlink(path)

    def test_ruby_pass_no_ruby_elements(self, accessible_pdf):
        """No ruby elements at all is acceptable (pass)."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            ruby_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-03-001"), None
            )
            assert ruby_check is not None
            assert ruby_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# UA-2 MathML tests
# ---------------------------------------------------------------------------


class TestUA2MathML:
    """Tests for UA-2 MathML structure checks."""

    def test_mathml_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include MathML checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        math_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-04-001"), None
        )
        assert math_check is not None
        assert "math" in math_check.name.lower() or "formula" in math_check.name.lower()

    def test_mathml_pass_formula_with_alt(self, accessible_pdf):
        """Formula element with /Alt text should pass."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        formula = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/Formula"),
                    "/K": pikepdf.Array([]),
                    "/Alt": pikepdf.String("x equals negative b plus or minus the square root of b squared minus 4 a c, all over 2 a"),
                }
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([formula])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            math_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-04-001"), None
            )
            assert math_check is not None
            assert math_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_mathml_fail_formula_without_alt(self, accessible_pdf):
        """Formula element without /Alt or associated MathML should fail."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        formula = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/Formula"),
                    "/K": pikepdf.Array([]),
                    # No /Alt, no /AF (associated file)
                }
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([formula])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            math_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-04-001"), None
            )
            assert math_check is not None
            assert math_check.status == CheckpointStatus.FAIL
        finally:
            os.unlink(path)

    def test_mathml_pass_no_formula_elements(self, accessible_pdf):
        """No formula elements at all is acceptable (pass)."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            math_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-04-001"), None
            )
            assert math_check is not None
            assert math_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_mathml_pass_formula_with_af(self, accessible_pdf):
        """Formula element with /AF (associated file) should pass."""
        struct_root = accessible_pdf.Root[pikepdf.Name.StructTreeRoot]

        # Create a mock associated file spec for MathML
        filespec = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Filespec"),
                    "/F": pikepdf.String("formula.xml"),
                    "/AFRelationship": pikepdf.Name("/Supplement"),
                }
            )
        )

        formula = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/S": pikepdf.Name("/Formula"),
                    "/K": pikepdf.Array([]),
                    "/AF": pikepdf.Array([filespec]),
                }
            )
        )
        struct_root[pikepdf.Name.K] = pikepdf.Array([formula])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            math_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-04-001"), None
            )
            assert math_check is not None
            assert math_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# UA-2 Associated files tests
# ---------------------------------------------------------------------------


class TestUA2AssociatedFiles:
    """Tests for UA-2 associated files checks."""

    def test_associated_files_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include associated files checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        af_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-05-001"), None
        )
        assert af_check is not None
        assert "associated" in af_check.name.lower() or "file" in af_check.name.lower()

    def test_associated_files_pass_with_relationship(self, accessible_pdf):
        """File specs with /AFRelationship should pass."""
        filespec = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Filespec"),
                    "/F": pikepdf.String("attachment.xml"),
                    "/AFRelationship": pikepdf.Name("/Supplement"),
                }
            )
        )
        accessible_pdf.Root[pikepdf.Name("/AF")] = pikepdf.Array([filespec])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            af_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-05-001"), None
            )
            assert af_check is not None
            assert af_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)

    def test_associated_files_fail_missing_relationship(self, accessible_pdf):
        """File specs without /AFRelationship should fail in UA-2."""
        filespec = accessible_pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Filespec"),
                    "/F": pikepdf.String("attachment.xml"),
                    # Missing /AFRelationship
                }
            )
        )
        accessible_pdf.Root[pikepdf.Name("/AF")] = pikepdf.Array([filespec])

        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            af_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-05-001"), None
            )
            assert af_check is not None
            assert af_check.status == CheckpointStatus.FAIL
        finally:
            os.unlink(path)

    def test_associated_files_pass_no_files(self, accessible_pdf):
        """No associated files at all is acceptable (pass)."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            af_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-05-001"), None
            )
            assert af_check is not None
            assert af_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# UA-2 Artifact handling tests
# ---------------------------------------------------------------------------


class TestUA2Artifacts:
    """Tests for UA-2 stricter artifact handling checks."""

    def test_artifact_check_present_in_ua2(self, bare_pdf_path):
        """UA-2 validation should include artifact checkpoint."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        art_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-06-001"), None
        )
        assert art_check is not None
        assert "artifact" in art_check.name.lower()

    def test_artifact_pass_no_struct_tree(self, bare_pdf_path):
        """Without StructTreeRoot, artifact check should pass (nothing to check)."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        art_check = next(
            (cp for cp in result.checkpoints if cp.id == "ua2-06-001"), None
        )
        assert art_check is not None
        # No struct tree means no artifacts in structure to check
        assert art_check.status in (CheckpointStatus.PASS, CheckpointStatus.WARNING)

    def test_artifact_pass_no_artifact_elements(self, accessible_pdf):
        """No Artifact elements in structure is acceptable (pass)."""
        path = _save_pdf(accessible_pdf)
        try:
            validator = MatterhornValidator()
            result = validator.validate(path, ua_version=2)

            art_check = next(
                (cp for cp in result.checkpoints if cp.id == "ua2-06-001"), None
            )
            assert art_check is not None
            assert art_check.status == CheckpointStatus.PASS
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# UA-2 checkpoint filtering tests
# ---------------------------------------------------------------------------


class TestUA2CheckpointFiltering:
    """Tests for filtering UA-2 specific failures."""

    def test_can_filter_ua2_checkpoints(self, bare_pdf_path):
        """UA-2 specific checkpoints should be filterable by prefix."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ua2_only = [cp for cp in result.checkpoints if cp.id.startswith("ua2-")]
        ua1_only = [cp for cp in result.checkpoints if not cp.id.startswith("ua2-")]

        # Both should have entries
        assert len(ua2_only) > 0, "Should have UA-2 specific checkpoints"
        assert len(ua1_only) > 0, "Should have UA-1 checkpoints"

    def test_ua2_checkpoint_count(self, bare_pdf_path):
        """UA-2 should add at least 6 new checkpoints (namespace, pronunciation, ruby, mathml, associated files, artifacts)."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        ua2_ids = {cp.id for cp in result.checkpoints if cp.id.startswith("ua2-")}
        expected_ids = {
            "ua2-01-001",  # namespaces
            "ua2-02-001",  # pronunciation
            "ua2-03-001",  # ruby
            "ua2-04-001",  # mathml
            "ua2-05-001",  # associated files
            "ua2-06-001",  # artifacts
        }
        assert expected_ids.issubset(ua2_ids), (
            f"Missing UA-2 checkpoint IDs: {expected_ids - ua2_ids}"
        )


# ---------------------------------------------------------------------------
# Integration tests - full validation flow
# ---------------------------------------------------------------------------


class TestUA2Integration:
    """Integration tests for full UA-2 validation flows."""

    def test_full_ua2_validation_bare_pdf(self, bare_pdf_path):
        """Full UA-2 validation on a bare PDF should have both UA-1 and UA-2 failures."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        # Should have many failures
        assert result.failed > 0
        assert result.compliance_level == "non_compliant"

        # Should have both UA-1 and UA-2 checkpoints
        has_ua1 = any(not cp.id.startswith("ua2-") for cp in result.checkpoints)
        has_ua2 = any(cp.id.startswith("ua2-") for cp in result.checkpoints)
        assert has_ua1, "Should include UA-1 checkpoints"
        assert has_ua2, "Should include UA-2 checkpoints"

    def test_full_ua2_validation_accessible_pdf(self, accessible_pdf_path):
        """UA-2 validation on an accessible PDF should pass UA-1 but may fail UA-2 checks."""
        validator = MatterhornValidator()
        result = validator.validate(accessible_pdf_path, ua_version=2)

        # UA-1 basics should pass
        for check_id in ("01-003", "01-004", "06-001"):
            cp = next(
                (c for c in result.checkpoints if c.id == check_id), None
            )
            assert cp is not None
            assert cp.status == CheckpointStatus.PASS, (
                f"UA-1 checkpoint {check_id} should PASS: {cp.details}"
            )

    def test_validate_signature_unchanged(self, bare_pdf_path):
        """validate() without ua_version should work exactly as before (backward compat)."""
        validator = MatterhornValidator()

        # Should work without ua_version argument
        result = validator.validate(bare_pdf_path)
        assert isinstance(result, MatterhornResult)
        assert result.ua_version == 1

    def test_result_serialization_with_ua_version(self, bare_pdf_path):
        """MatterhornResult should serialize ua_version to dict/JSON."""
        validator = MatterhornValidator()
        result = validator.validate(bare_pdf_path, ua_version=2)

        result_dict = result.model_dump()
        assert "ua_version" in result_dict
        assert result_dict["ua_version"] == 2
