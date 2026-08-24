"""Remediation-time OCR preprocessing for image-only PDFs.

The scanner's PDFProcessor creates an OCR'd searchable derivative during the
scan and then deletes it, keeping only the extracted text. Remediation later
opens the original image-only file, so the delivered ``_remediated.pdf`` used
to carry no text layer at all — and verification masked the gap because the
re-scan OCRs the output on the fly.

These tests pin the required behavior: PdfRemediator OCRs an image-only input
into a temporary working copy, remediates that copy, and preserves the
searchable text layer in ``result.output_file``. Only the external OCR engine
(``ocrmypdf.ocr``) is mocked; all PDF reads/writes use real fitz/pikepdf.
"""

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import fitz
import ocrmypdf
import pikepdf
import pytest

from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator

OCR_MARKER = "OCR LAYER TEXT recovered from scanned page"


def _make_image_only_pdf(path: Path) -> None:
    """Write a PDF whose single page is an image with no extractable text."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100))
    pix.clear_with(90)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(50, 50, 550, 300), pixmap=pix)
    doc.save(str(path))
    doc.close()
    with fitz.open(str(path)) as check:
        assert not check[0].get_text().strip(), "fixture must be image-only"


def _make_text_pdf(path: Path) -> None:
    """Write a PDF with a real, extractable text layer."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    text = "Searchable syllabus content. " * 10
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    with fitz.open(str(path)) as check:
        assert len(check[0].get_text().strip()) >= 100


def _fake_ocr_engine(calls: list):
    """Deterministic stand-in for ocrmypdf.ocr.

    Writes a real text-bearing PDF derivative (same pages as the input, plus
    an inserted text layer) to ``output_file``, exactly like a searchable OCR
    output. Records each call so tests can assert on invocation and paths.
    """

    def fake_ocr(input_file=None, output_file=None, **kwargs):
        calls.append(
            {
                "input_file": str(input_file),
                "output_file": str(output_file),
                "kwargs": dict(kwargs),
            }
        )
        src = fitz.open(str(input_file))
        out = fitz.open()
        out.insert_pdf(src)
        for page in out:
            # Mirror skip_text semantics: pages with a text layer are
            # passed through untouched, image-only pages gain OCR text.
            if page.get_text().strip():
                continue
            page.insert_text((72, 72), OCR_MARKER)
            for line in range(1, 6):
                page.insert_text(
                    (72, 72 + line * 14),
                    f"Recognized line {line} of the scanned page content.",
                )
        out.save(str(output_file))
        out.close()
        src.close()

    return fake_ocr


def _config(tmp_path: Path) -> RemediationConfig:
    return RemediationConfig(
        use_ai=False,
        verify_fixes=False,
        create_backup=False,
        output_directory=str(tmp_path / "out"),
    )


def _language_issue():
    return [
        {
            "type": "language",
            "severity": "medium",
            "message": "Document language is not set",
        }
    ]


@pytest.fixture(autouse=True)
def _close_direct_test_output_claims(monkeypatch):
    """Every direct remediator test discharges successful claim ownership."""
    results = []
    real_remediate = PdfRemediator.remediate

    def tracked_remediate(self):
        result = real_remediate(self)
        results.append(result)
        return result

    monkeypatch.setattr(PdfRemediator, "remediate", tracked_remediate)
    yield
    for result in results:
        result.close_output_claim()


def test_image_only_pdf_output_preserves_ocr_text_layer(tmp_path, monkeypatch):
    """The delivered remediated PDF must keep the OCR-added searchable text."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    calls = []
    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine(calls))

    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    result = remediator.remediate()

    assert result.success, f"remediation failed: {result.error_message}"
    assert result.output_file is not None
    assert os.path.exists(result.output_file)
    assert len(calls) == 1, "OCR engine should run exactly once for image-only input"

    with fitz.open(result.output_file) as delivered:
        delivered_text = "".join(page.get_text() for page in delivered)
    assert OCR_MARKER in delivered_text, (
        "delivered output lost the OCR text layer — an image-only input must "
        "produce a searchable remediated PDF"
    )


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _track_workdirs(monkeypatch):
    """Record every temp working directory the remediator creates."""
    import src.education.remediation.pdf_remediator as mod

    made = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "aelira_pdf_remediation_":
            made.append(path)
        return path

    monkeypatch.setattr(mod.tempfile, "mkdtemp", recording_mkdtemp)
    return made


def _candidate_directories(output_dir: Path):
    """Return private publication directories retained under output_dir."""
    return list(output_dir.glob(".*_remediated.candidate-*"))


def _track_serialization_dirs(monkeypatch):
    """Record private directories used only for library PDF serialization."""
    import src.education.remediation.pdf_remediator as mod

    made = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "aelira_pdf_serialization_":
            made.append(path)
        return path

    monkeypatch.setattr(mod.tempfile, "mkdtemp", recording_mkdtemp)
    return made


def test_original_file_bytes_never_change(tmp_path, monkeypatch):
    """Remediation must never rewrite the original upload in place."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))
    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success
    assert _sha256(input_pdf) == original_sha
    assert result.original_file == str(input_pdf)


def test_searchable_input_skips_ocr_but_still_succeeds(tmp_path, monkeypatch):
    """A PDF with a real text layer must never be sent to the OCR engine."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("ocrmypdf.ocr must not run for searchable input")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)
    workdirs = _track_workdirs(monkeypatch)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, f"remediation failed: {result.error_message}"
    assert result.output_file is not None
    assert _sha256(input_pdf) == original_sha
    # Staged copy is still used and cleaned up
    assert len(workdirs) == 1
    assert not os.path.exists(workdirs[0])


def test_output_name_is_based_on_original_not_working_copy(tmp_path, monkeypatch):
    """Output must be <original stem>_remediated.pdf, not a temp-derived name."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))
    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success
    assert Path(result.output_file).name == "scanned_handout_remediated.pdf"
    assert Path(result.output_file).parent == tmp_path / "out"


def test_temp_working_dir_removed_after_success(tmp_path, monkeypatch):
    """The staged copy and OCR derivative must not outlive a successful run."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    calls = []
    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine(calls))
    workdirs = _track_workdirs(monkeypatch)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success
    assert len(workdirs) == 1
    assert not os.path.exists(workdirs[0])
    assert not os.path.exists(calls[0]["output_file"])


def test_temp_working_dir_removed_after_ocr_failure(tmp_path, monkeypatch):
    """OCR engine failure must clean the temp dir and fail closed."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    def broken_ocr(*args, **kwargs):
        raise ocrmypdf.exceptions.MissingDependencyError("tesseract not installed")

    monkeypatch.setattr(ocrmypdf, "ocr", broken_ocr)
    workdirs = _track_workdirs(monkeypatch)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert len(workdirs) == 1
    assert not os.path.exists(workdirs[0])
    assert _sha256(input_pdf) == original_sha


def test_ocr_failure_produces_no_success_shaped_output(tmp_path, monkeypatch):
    """OCR failure: success False, no output file anywhere, error explains why."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    def broken_ocr(*args, **kwargs):
        raise ocrmypdf.exceptions.MissingDependencyError("tesseract not installed")

    monkeypatch.setattr(ocrmypdf, "ocr", broken_ocr)
    out_dir = tmp_path / "out"

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert result.error_message
    remediated = list(out_dir.glob("*_remediated.pdf")) if out_dir.exists() else []
    assert remediated == [], "fail-closed run must leave no remediated artifact"


def test_ocr_yielding_no_text_fails_closed(tmp_path, monkeypatch):
    """An OCR derivative with no extractable text is a failure, not a success."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    def textless_ocr(input_file=None, output_file=None, **kwargs):
        shutil.copy2(str(input_file), str(output_file))  # no text layer added

    monkeypatch.setattr(ocrmypdf, "ocr", textless_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "text layer" in (result.error_message or "")


def test_ocr_yielding_subthreshold_page_text_fails_closed(tmp_path, monkeypatch):
    """A nonblank OCR layer below the usable threshold is still a failure."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    def insufficient_ocr(input_file=None, output_file=None, **kwargs):
        with fitz.open(str(input_file)) as source:
            output = fitz.open()
            output.insert_pdf(source)
            output[0].insert_text((72, 340), "Too short")
            output.save(str(output_file))
            output.close()

    monkeypatch.setattr(ocrmypdf, "ocr", insufficient_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "page(s) [1]" in (result.error_message or "")
    assert "usable text layer" in (result.error_message or "")


def test_signed_pdf_fails_closed_before_any_rewrite(tmp_path, monkeypatch):
    """A PDF with /Sig fields must be rejected, never silently re-signed-away."""
    input_pdf = tmp_path / "signed_form.pdf"
    _make_image_only_pdf(input_pdf)
    with pikepdf.open(str(input_pdf), allow_overwriting_input=True) as pdf:
        sig_field = pdf.make_indirect(
            pikepdf.Dictionary(FT=pikepdf.Name("/Sig"), T=pikepdf.String("Signature1"))
        )
        pdf.Root.AcroForm = pdf.make_indirect(
            pikepdf.Dictionary(Fields=pikepdf.Array([sig_field]), SigFlags=3)
        )
        pdf.save(str(input_pdf))
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("ocrmypdf.ocr must not run for signed input")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "signature" in (result.error_message or "").lower()
    assert _sha256(input_pdf) == original_sha


@pytest.mark.parametrize(
    "staged_kind, expected_error", [("signed", "signature"), ("xfa", "xfa")]
)
def test_signature_preflight_inspects_exact_staged_snapshot(
    tmp_path, monkeypatch, staged_kind, expected_error
):
    """Preflight must inspect private staged bytes, not the source pathname."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "unsigned_source.pdf"
    _make_image_only_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    replacement_pdf = tmp_path / f"{staged_kind}_replacement.pdf"
    _make_image_only_pdf(replacement_pdf)
    with pikepdf.open(str(replacement_pdf), allow_overwriting_input=True) as pdf:
        if staged_kind == "signed":
            sig_field = pdf.make_indirect(
                pikepdf.Dictionary(
                    FT=pikepdf.Name("/Sig"), T=pikepdf.String("Signature1")
                )
            )
            pdf.Root.AcroForm = pdf.make_indirect(
                pikepdf.Dictionary(Fields=pikepdf.Array([sig_field]), SigFlags=3)
            )
        else:
            pdf.Root.AcroForm = pdf.make_indirect(
                pikepdf.Dictionary(
                    Fields=pikepdf.Array([]), XFA=pikepdf.String("<xdp/>")
                )
            )
        pdf.save(str(replacement_pdf))
    replacement_sha = _sha256(replacement_pdf)

    real_copy2 = shutil.copy2
    staged_hashes = []

    def substitute_staged_bytes(src, dst, *args, **kwargs):
        copied = real_copy2(str(replacement_pdf), dst, *args, **kwargs)
        staged_hashes.append(_sha256(dst))
        return copied

    ocr_calls = []

    def forbidden_ocr(*args, **kwargs):
        ocr_calls.append((args, kwargs))
        raise AssertionError("OCR must not run before staged-byte preflight")

    monkeypatch.setattr(mod.shutil, "copy2", substitute_staged_bytes)
    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert expected_error in (result.error_message or "").lower()
    assert staged_hashes == [replacement_sha]
    assert ocr_calls == []
    assert _sha256(input_pdf) == original_sha
    assert not (tmp_path / "out" / "unsigned_source_remediated.pdf").exists()


def _add_image_page(path: Path) -> None:
    """Append an image-only page to an existing PDF."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100))
    pix.clear_with(90)
    with fitz.open(str(path)) as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(50, 50, 550, 300), pixmap=pix)
        doc.save(str(path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)


def _make_image_with_short_direct_text_pdf(path: Path) -> None:
    """Write one image page with a non-empty, sub-threshold text layer."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100))
    pix.clear_with(90)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(50, 50, 550, 300), pixmap=pix)
    page.insert_text((72, 340), "Partial scan text")
    doc.save(str(path))
    doc.close()
    with fitz.open(str(path)) as check:
        extracted = check[0].get_text().strip()
        assert 1 <= len(extracted) < PdfRemediator._MIN_OCR_TEXT_CHARS


def test_image_page_with_partial_direct_text_fails_closed(tmp_path, monkeypatch):
    """Mixed-content pages cannot be safely repaired with skip_text OCR."""
    input_pdf = tmp_path / "partially_searchable_scan.pdf"
    _make_image_with_short_direct_text_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("skip_text OCR would silently skip this partial-text page")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "page(s) [1]" in (result.error_message or "")
    assert "partial" in (result.error_message or "").lower()
    assert _sha256(input_pdf) == original_sha
    out_dir = tmp_path / "out"
    remediated = list(out_dir.glob("*_remediated.pdf")) if out_dir.exists() else []
    assert remediated == []


def test_mixed_pages_ocr_only_image_pages_with_skip_text(tmp_path, monkeypatch):
    """A text page plus a scanned page must OCR the scanned page only.

    Document-total text assessment sees the >100-char first page and skips
    OCR entirely, delivering page 2 with no text layer. OCR need must be
    assessed per page, run mixed-safe (skip_text=True), and every page
    that needed OCR must have direct text in the delivered output.
    """
    input_pdf = tmp_path / "mixed_report.pdf"
    _make_text_pdf(input_pdf)  # page 1: >100 chars of real text
    _add_image_page(input_pdf)  # page 2: image only

    calls = []
    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine(calls))

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, f"remediation failed: {result.error_message}"
    assert len(calls) == 1, "mixed input must be sent to the OCR engine"
    assert calls[0]["kwargs"].get("skip_text") is True, (
        "mixed input must use skip_text=True so existing text pages are "
        "passed through untouched"
    )
    with fitz.open(result.output_file) as delivered:
        page_texts = [page.get_text().strip() for page in delivered]
    assert len(page_texts) == 2
    assert all(page_texts), "every page must have direct extractable text"
    assert OCR_MARKER in page_texts[1], "scanned page must carry the OCR layer"


def test_mixed_output_missing_ocr_page_text_fails_closed(tmp_path, monkeypatch):
    """A text-rich page must not mask OCR-layer loss on another page."""
    input_pdf = tmp_path / "mixed_report.pdf"
    _make_text_pdf(input_pdf)
    _add_image_page(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))

    def save_without_page_two_text(self, document, output_path):
        # Write a valid, changed candidate that retains page 1's abundant text
        # but loses the OCR layer from page 2.
        with fitz.open(str(input_pdf)) as candidate:
            metadata = candidate.metadata
            metadata["producer"] = "text-layer-loss regression candidate"
            candidate.set_metadata(metadata)
            candidate.save(output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", save_without_page_two_text)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "page" in (result.error_message or "").lower()
    assert "2" in (result.error_message or "")
    out_dir = tmp_path / "out"
    remediated = list(out_dir.glob("*_remediated.pdf")) if out_dir.exists() else []
    assert remediated == []


def test_non_english_lang_pdf_fails_closed_instead_of_english_ocr(
    tmp_path, monkeypatch
):
    """Only eng OCR is supported; a declared non-English /Lang fails closed."""
    input_pdf = tmp_path / "german_scan.pdf"
    _make_image_only_pdf(input_pdf)
    with pikepdf.open(str(input_pdf), allow_overwriting_input=True) as pdf:
        pdf.Root.Lang = pikepdf.String("de-DE")
        pdf.save(str(input_pdf))
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("English OCR must not run on a de-DE document")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "language" in (result.error_message or "").lower()
    assert _sha256(input_pdf) == original_sha


def test_prior_ocr_refusal_with_needy_page_fails_closed(tmp_path, monkeypatch):
    """A text-rich page must not mask a needy page when OCR refuses to run."""
    input_pdf = tmp_path / "partial_text_scan.pdf"
    _make_partial_text_pdf(input_pdf)  # page 1: 50-99 chars
    _add_image_page(input_pdf)  # page 2: image only, triggers OCR attempt
    original_sha = _sha256(input_pdf)

    calls = []

    def prior_ocr(*args, **kwargs):
        calls.append(dict(kwargs))
        raise ocrmypdf.exceptions.PriorOcrFoundError("page already has text")

    monkeypatch.setattr(ocrmypdf, "ocr", prior_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert len(calls) == 1
    assert "unsearchable" in (result.error_message or "").lower()
    assert "2" in (result.error_message or "")
    assert _sha256(input_pdf) == original_sha


def test_single_partial_text_page_without_image_skips_ocr(tmp_path, monkeypatch):
    """Partial direct text alone is remediated without invoking OCR."""
    input_pdf = tmp_path / "partial_text.pdf"
    _make_partial_text_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("OCR must not run when no image page needs it")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, f"remediation failed: {result.error_message}"
    assert result.output_file is not None
    assert _sha256(input_pdf) == original_sha


def test_nested_kids_signature_with_inherited_ft_fails_closed(tmp_path, monkeypatch):
    """A /Sig buried in /Kids (with /FT inherited) must be detected.

    AcroForm field trees nest: a terminal signature widget can sit two
    levels down with its /FT inherited from an intermediate parent. A
    top-level-only scan misses it and silently invalidates the signature.
    """
    input_pdf = tmp_path / "nested_signed_form.pdf"
    _make_image_only_pdf(input_pdf)
    with pikepdf.open(str(input_pdf), allow_overwriting_input=True) as pdf:
        leaf = pdf.make_indirect(pikepdf.Dictionary(T=pikepdf.String("sig-widget")))
        mid = pdf.make_indirect(
            pikepdf.Dictionary(
                T=pikepdf.String("sig-parent"),
                FT=pikepdf.Name("/Sig"),
                Kids=pikepdf.Array([leaf]),
            )
        )
        top = pdf.make_indirect(
            pikepdf.Dictionary(T=pikepdf.String("group"), Kids=pikepdf.Array([mid]))
        )
        pdf.Root.AcroForm = pdf.make_indirect(
            pikepdf.Dictionary(Fields=pikepdf.Array([top]))
        )
        pdf.save(str(input_pdf))
    original_sha = _sha256(input_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("ocrmypdf.ocr must not run for signed input")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "signature" in (result.error_message or "").lower()
    assert _sha256(input_pdf) == original_sha


def test_signature_preflight_fails_closed_when_pikepdf_unavailable(
    tmp_path, monkeypatch
):
    """No pikepdf means signatures cannot be ruled out: fail closed."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))
    monkeypatch.setattr(mod, "HAS_PIKEPDF", False)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "preflight" in (result.error_message or "").lower()


def test_signature_preflight_parse_error_fails_closed(tmp_path, monkeypatch):
    """A pikepdf failure during preflight is indeterminate, never 'unsigned'."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))

    def broken_open(*args, **kwargs):
        raise RuntimeError("simulated pikepdf parse failure")

    monkeypatch.setattr(mod.pikepdf, "open", broken_open)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "preflight" in (result.error_message or "").lower()


def test_xfa_form_fails_closed_as_indeterminate(tmp_path, monkeypatch):
    """XFA forms can hide signatures; treat them as indeterminate input."""
    input_pdf = tmp_path / "xfa_form.pdf"
    _make_image_only_pdf(input_pdf)
    with pikepdf.open(str(input_pdf), allow_overwriting_input=True) as pdf:
        pdf.Root.AcroForm = pdf.make_indirect(
            pikepdf.Dictionary(Fields=pikepdf.Array([]), XFA=pikepdf.String("<xdp/>"))
        )
        pdf.save(str(input_pdf))

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "xfa" in (result.error_message or "").lower()


def test_tagged_image_only_pdf_fails_closed(tmp_path, monkeypatch):
    """A tagged PDF whose pages are still pure images must fail closed.

    TaggedPDFError means the engine refuses to OCR, and the staged copy has
    no usable text of its own — delivering a success-shaped unsearchable
    artifact here would repeat the exact defect this fix exists to close.
    """
    input_pdf = tmp_path / "previously_remediated_scan.pdf"
    _make_image_only_pdf(input_pdf)
    original_sha = _sha256(input_pdf)

    def tagged_ocr(*args, **kwargs):
        raise ocrmypdf.exceptions.TaggedPDFError("already tagged")

    monkeypatch.setattr(ocrmypdf, "ocr", tagged_ocr)
    workdirs = _track_workdirs(monkeypatch)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "tagged" in (result.error_message or "").lower()
    assert _sha256(input_pdf) == original_sha
    assert len(workdirs) == 1
    assert not os.path.exists(workdirs[0])


def _make_partial_text_pdf(path: Path) -> None:
    """Write a PDF whose extractable text is in the 50-99 char band.

    Below the output text-layer threshold (100), while still containing
    enough direct text to remediate without OCR when no page has an image.
    """
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(
        (72, 72), "Partial text layer content from a lightly scanned page."
    )
    page.insert_text((72, 90), "Second short line of text.")
    doc.save(str(path))
    doc.close()
    with fitz.open(str(path)) as check:
        extracted = check[0].get_text().strip()
        assert 50 <= len(extracted) < 100, f"fixture text is {len(extracted)} chars"


def test_partial_text_no_ocr_still_gates_delivered_output(tmp_path, monkeypatch):
    """A no-OCR save must still retain the input's partial text layer."""
    input_pdf = tmp_path / "partial_text_scan.pdf"
    _make_partial_text_pdf(input_pdf)
    blank_pdf = tmp_path / "blank.pdf"
    _make_image_only_pdf(blank_pdf)

    def forbidden_ocr(*args, **kwargs):
        raise AssertionError("OCR must not run when no image page needs it")

    monkeypatch.setattr(ocrmypdf, "ocr", forbidden_ocr)

    def save_without_text_layer(self, document, output_path):
        # Simulate a save path that drops the partial text layer.
        shutil.copy2(str(blank_pdf), output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", save_without_text_layer)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "text layer" in (result.error_message or "")
    out_dir = tmp_path / "out"
    remediated = list(out_dir.glob("*_remediated.pdf")) if out_dir.exists() else []
    assert remediated == [], "the unsearchable artifact must be removed"


def test_delivered_output_missing_text_layer_fails_closed(tmp_path, monkeypatch):
    """If the saved output somehow lost the OCR layer, delivery must fail.

    Guards the verification gate itself: the check reads the delivered file
    with direct extraction, so on-the-fly OCR in the verification re-scan
    can no longer mask a text-free artifact.
    """
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))

    def save_without_text_layer(self, document, output_path):
        # Simulate a save path that drops the text layer: deliver the
        # original image-only bytes instead of the OCR'd working copy.
        shutil.copy2(str(input_pdf), output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", save_without_text_layer)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "text layer" in (result.error_message or "")
    out_dir = tmp_path / "out"
    remediated = list(out_dir.glob("*_remediated.pdf")) if out_dir.exists() else []
    assert remediated == [], "the unsearchable artifact must be removed"


def test_delivered_output_with_subthreshold_ocr_text_fails_closed(
    tmp_path, monkeypatch
):
    """The final per-page gate requires usable OCR text, not merely nonblank."""
    input_pdf = tmp_path / "scanned_handout.pdf"
    _make_image_only_pdf(input_pdf)
    insufficient_pdf = tmp_path / "insufficient.pdf"
    _make_image_with_short_direct_text_pdf(insufficient_pdf)

    monkeypatch.setattr(ocrmypdf, "ocr", _fake_ocr_engine([]))

    def save_with_insufficient_text(self, document, output_path):
        shutil.copy2(str(insufficient_pdf), output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", save_with_insufficient_text)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "page(s) [1]" in (result.error_message or "")
    assert not (tmp_path / "out" / "scanned_handout_remediated.pdf").exists()


def test_invalid_candidate_preserves_preexisting_valid_output(tmp_path, monkeypatch):
    """Candidate validation failure must not overwrite the last valid output."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_text_pdf(final_path)
    previous_bytes = final_path.read_bytes()

    def write_corrupt_candidate(self, document, output_path):
        Path(output_path).write_bytes(b"%PDF-1.7\ntruncated candidate")

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", write_corrupt_candidate)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert final_path.read_bytes() == previous_bytes
    assert _candidate_directories(out_dir) == []


def test_oversized_candidate_is_rejected_before_read_and_preserves_prior_output(
    tmp_path, monkeypatch
):
    """Candidate size limits must fail before reading or publishing its bytes."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_image_only_pdf(final_path)
    previous_bytes = final_path.read_bytes()

    monkeypatch.setattr(mod, "_MAX_PDF_CANDIDATE_BYTES", 1)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "size" in (result.error_message or "").lower()
    assert final_path.read_bytes() == previous_bytes
    assert _candidate_directories(out_dir) == []


def test_save_failure_preserves_preexisting_valid_output(tmp_path, monkeypatch):
    """A partial candidate write must not touch the last valid output."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_text_pdf(final_path)
    previous_bytes = final_path.read_bytes()

    def interrupted_write(self, document, output_path):
        Path(output_path).write_bytes(b"%PDF-1.7\npartial")
        raise OSError("simulated interrupted save")

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", interrupted_write)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert final_path.read_bytes() == previous_bytes
    assert _candidate_directories(out_dir) == []


def test_successful_candidate_atomically_replaces_existing_output(
    tmp_path, monkeypatch
):
    """Only a validated candidate may replace the deterministic output."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_image_only_pdf(final_path)
    previous_bytes = final_path.read_bytes()

    replacements = []
    real_rename = os.rename

    def recording_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        replacements.append(
            {
                "src": str(src),
                "dst": str(dst),
                "src_dir_fd": src_dir_fd,
                "dst_dir_fd": dst_dir_fd,
                "parent_mode": stat.S_IMODE(os.fstat(src_dir_fd).st_mode),
            }
        )
        return real_rename(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(mod.os, "rename", recording_rename)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, result.error_message
    assert result.output_file == str(final_path)
    assert final_path.read_bytes() != previous_bytes
    final_replacements = [
        pair for pair in replacements if pair["dst"] == final_path.name
    ]
    assert len(final_replacements) == 1
    replacement = final_replacements[0]
    assert replacement["src"] == "candidate.pdf"
    assert isinstance(replacement["src_dir_fd"], int)
    assert isinstance(replacement["dst_dir_fd"], int)
    assert replacement["src_dir_fd"] != replacement["dst_dir_fd"]
    assert replacement["parent_mode"] == 0o700
    assert replacement["dst"] == final_path.name
    assert _candidate_directories(out_dir) == []
    with fitz.open(str(final_path)) as delivered:
        assert delivered.page_count == 1


def test_serialized_pdf_is_copied_to_exclusive_descriptor_bound_candidate(
    tmp_path, monkeypatch
):
    """The publication candidate is created only by basename through its dir fd."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    candidate_opens = []
    fsynced = []
    real_open = os.open
    real_fsync = os.fsync

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if path == "candidate.pdf" and flags & os.O_WRONLY:
            candidate_opens.append(
                {
                    "path": path,
                    "flags": flags,
                    "mode": mode,
                    "dir_fd": kwargs.get("dir_fd"),
                    "dir_mode": stat.S_IMODE(os.fstat(kwargs["dir_fd"]).st_mode),
                    "descriptor": descriptor,
                }
            )
        return descriptor

    def recording_fsync(descriptor):
        fsynced.append(descriptor)
        return real_fsync(descriptor)

    monkeypatch.setattr(mod.os, "open", recording_open)
    monkeypatch.setattr(mod.os, "fsync", recording_fsync)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, result.error_message
    assert len(candidate_opens) == 1
    opened = candidate_opens[0]
    assert opened["path"] == "candidate.pdf"
    assert isinstance(opened["dir_fd"], int)
    assert opened["dir_mode"] == 0o700
    assert opened["flags"] & os.O_CREAT
    assert opened["flags"] & os.O_EXCL
    assert opened["flags"] & os.O_NOFOLLOW
    assert opened["mode"] == 0o600
    assert opened["descriptor"] in fsynced


def test_output_parent_replaced_before_serialization_copy_never_receives_pdf_write(
    tmp_path, monkeypatch
):
    """Library serialization never receives a lexical path under caller output."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    bound_dir = tmp_path / "bound-output"
    attacker_marker = b"attacker directory must remain untouched"
    serialization_paths = []
    serialization_modes = []
    real_writer = PdfRemediator._write_pdf_output

    def replace_parent_before_writer_returns(self, document, output_path):
        serialized_path = Path(output_path)
        serialization_paths.append(serialized_path)
        serialization_modes.append(stat.S_IMODE(serialized_path.parent.stat().st_mode))
        os.rename(out_dir, bound_dir)
        out_dir.mkdir()
        (out_dir / "attacker-marker").write_bytes(attacker_marker)
        try:
            relative_output = serialized_path.relative_to(out_dir)
        except ValueError:
            pass
        else:
            (out_dir / relative_output.parent).mkdir(parents=True, exist_ok=True)
        real_writer(self, document, output_path)

    monkeypatch.setattr(
        PdfRemediator, "_write_pdf_output", replace_parent_before_writer_returns
    )

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert len(serialization_paths) == 1
    assert serialization_modes == [0o700]
    assert not serialization_paths[0].is_relative_to(out_dir)
    assert not serialization_paths[0].is_relative_to(bound_dir)
    assert list(out_dir.rglob("candidate.pdf")) == []
    assert (out_dir / "attacker-marker").read_bytes() == attacker_marker
    assert list(bound_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("source_kind", "expected_error"),
    [
        ("symlink", "symbolic link"),
        ("nonregular", "regular file"),
        ("wrong-owner", "current user"),
        ("oversize", "size"),
        ("empty", "greater than zero"),
    ],
)
def test_serialization_source_fails_closed(
    tmp_path, monkeypatch, source_kind, expected_error
):
    """Only a bounded regular current-user-owned serialization is copied."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_image_only_pdf(final_path)
    previous_bytes = final_path.read_bytes()
    serialization_dirs = _track_serialization_dirs(monkeypatch)
    source_identities = set()
    real_writer = PdfRemediator._write_pdf_output
    real_fstat = os.fstat

    def hostile_serialization(self, document, output_path):
        path = Path(output_path)
        if source_kind == "symlink":
            path.symlink_to(input_pdf)
        elif source_kind == "nonregular":
            path.mkdir()
        elif source_kind == "empty":
            path.write_bytes(b"")
        else:
            real_writer(self, document, output_path)
            source_identities.add((path.stat().st_dev, path.stat().st_ino))

    def wrong_owner_fstat(descriptor):
        metadata = real_fstat(descriptor)
        if (
            source_kind != "wrong-owner"
            or (
                metadata.st_dev,
                metadata.st_ino,
            )
            not in source_identities
        ):
            return metadata
        fields = list(metadata)
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", hostile_serialization)
    monkeypatch.setattr(mod.os, "fstat", wrong_owner_fstat)
    if source_kind == "oversize":
        monkeypatch.setattr(mod, "_MAX_PDF_CANDIDATE_BYTES", 1)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert expected_error.lower() in (result.error_message or "").lower()
    assert final_path.read_bytes() == previous_bytes
    assert _candidate_directories(out_dir) == []
    assert len(serialization_dirs) == 1
    assert not os.path.exists(serialization_dirs[0])


def test_destination_partial_copy_failure_unlinks_only_bound_candidate(
    tmp_path, monkeypatch
):
    """An interrupted copy removes its candidate and preserves the prior final."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    _make_image_only_pdf(final_path)
    previous_bytes = final_path.read_bytes()
    serialization_dirs = _track_serialization_dirs(monkeypatch)
    destination_fds = set()
    candidate_dir_fds = set()
    successful_unlinks = []
    real_open = os.open
    real_write = os.write
    real_unlink = os.unlink
    writes = 0

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if path == "candidate.pdf" and flags & os.O_WRONLY:
            destination_fds.add(descriptor)
            candidate_dir_fds.add(kwargs.get("dir_fd"))
        return descriptor

    def partial_then_fail(descriptor, data):
        nonlocal writes
        if descriptor not in destination_fds:
            return real_write(descriptor, data)
        writes += 1
        if writes == 1:
            return real_write(descriptor, data[: max(1, len(data) // 2)])
        raise OSError("simulated destination copy failure")

    def recording_unlink(path, *args, **kwargs):
        result = real_unlink(path, *args, **kwargs)
        successful_unlinks.append((path, kwargs.get("dir_fd")))
        return result

    monkeypatch.setattr(mod.os, "open", tracking_open)
    monkeypatch.setattr(mod.os, "write", partial_then_fail)
    monkeypatch.setattr(mod.os, "unlink", recording_unlink)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert "destination copy failure" in (result.error_message or "")
    assert final_path.read_bytes() == previous_bytes
    assert writes == 2
    candidate_unlinks = [
        unlink for unlink in successful_unlinks if unlink[0] == "candidate.pdf"
    ]
    assert candidate_unlinks == [("candidate.pdf", next(iter(candidate_dir_fds)))]
    assert all(Path(path).name != final_path.name for path, _ in successful_unlinks)
    assert _candidate_directories(out_dir) == []
    assert len(serialization_dirs) == 1
    assert not os.path.exists(serialization_dirs[0])


@pytest.mark.parametrize("failure_kind", ["success", "save", "copy", "validation"])
def test_serialization_temp_directory_is_cleaned_on_every_exit(
    tmp_path, monkeypatch, failure_kind
):
    """The library-writable serialization directory never outlives the save."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    serialization_dirs = _track_serialization_dirs(monkeypatch)

    if failure_kind == "save":

        def fail_save(self, document, output_path):
            Path(output_path).write_bytes(b"partial")
            raise OSError("simulated serialization failure")

        monkeypatch.setattr(PdfRemediator, "_write_pdf_output", fail_save)
    elif failure_kind == "copy":
        monkeypatch.setattr(
            mod.os,
            "write",
            lambda descriptor, data: (_ for _ in ()).throw(
                OSError("simulated serialization copy failure")
            ),
        )
    elif failure_kind == "validation":

        def fail_validation(self, candidate_path, *, dir_fd=None):
            raise RuntimeError("simulated copied candidate validation failure")

        monkeypatch.setattr(
            PdfRemediator, "_validate_output_candidate", fail_validation
        )

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is (failure_kind == "success")
    assert len(serialization_dirs) == 1
    assert not os.path.exists(serialization_dirs[0])


def test_serialization_cleanup_failure_surfaces_retained_path(tmp_path, monkeypatch):
    """A failed serialization cleanup reports the private path left behind."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    serialization_dirs = _track_serialization_dirs(monkeypatch)
    real_rmtree = shutil.rmtree

    def block_serialization_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith("aelira_pdf_serialization_"):
            raise PermissionError("simulated serialization cleanup denial")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mod.shutil, "rmtree", block_serialization_cleanup)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert len(serialization_dirs) == 1
    retained_path = serialization_dirs[0]
    assert os.path.isdir(retained_path)
    assert retained_path in (result.error_message or "")
    assert any(retained_path in warning for warning in result.warnings)


def test_pdf_publication_fails_closed_when_output_directory_path_is_replaced(
    tmp_path, monkeypatch
):
    """A post-validation parent swap must prevent PDF publication."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    bound_dir = tmp_path / "bound-output"
    attacker_marker = b"attacker directory must remain untouched"
    real_validate = PdfRemediator._validate_output_candidate

    def validate_then_replace_parent(self, candidate_path, *, dir_fd=None):
        identity = real_validate(self, candidate_path, dir_fd=dir_fd)
        os.rename(out_dir, bound_dir)
        out_dir.mkdir()
        (out_dir / "attacker-marker").write_bytes(attacker_marker)
        return identity

    monkeypatch.setattr(
        PdfRemediator, "_validate_output_candidate", validate_then_replace_parent
    )

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    final_name = "typed_syllabus_remediated.pdf"
    assert result.success is False
    assert result.output_file is None
    assert not (bound_dir / final_name).exists()
    assert not (out_dir / final_name).exists()
    assert (out_dir / "attacker-marker").read_bytes() == attacker_marker
    assert list(bound_dir.iterdir()) == []
    assert _candidate_directories(bound_dir) == []
    assert _candidate_directories(out_dir) == []


def test_pdf_and_html_publication_fail_closed_when_output_directory_path_is_replaced(
    tmp_path, monkeypatch
):
    """A post-validation parent swap must prevent PDF and HTML publication."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    bound_dir = tmp_path / "bound-output"
    attacker_marker = b"attacker directory must remain untouched"
    real_validate = PdfRemediator._validate_html_candidate

    def validate_then_replace_parent(candidate_path, *, dir_fd=None):
        identity = real_validate(candidate_path, dir_fd=dir_fd)
        os.rename(out_dir, bound_dir)
        out_dir.mkdir()
        (out_dir / "attacker-marker").write_bytes(attacker_marker)
        return identity

    monkeypatch.setattr(
        PdfRemediator,
        "_validate_html_candidate",
        staticmethod(validate_then_replace_parent),
    )
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = "<main>descriptor-bound HTML</main>"

    result = remediator.remediate()

    assert result.success is False
    assert result.output_file is None
    assert not (bound_dir / "typed_syllabus_remediated.pdf").exists()
    assert not (bound_dir / "typed_syllabus_remediated_accessible.html").exists()
    assert list(out_dir.iterdir()) == [out_dir / "attacker-marker"]
    assert (out_dir / "attacker-marker").read_bytes() == attacker_marker
    assert list(bound_dir.iterdir()) == []
    assert _candidate_directories(bound_dir) == []
    assert _candidate_directories(out_dir) == []


def test_output_directory_replacement_never_verifies_stale_output_path(
    tmp_path, monkeypatch
):
    """Verification must not consume a lexical path after its parent drifts."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    bound_dir = tmp_path / "bound-output"
    attacker_marker = b"attacker directory must remain untouched"
    real_validate = PdfRemediator._validate_output_candidate
    verification_calls = []

    def validate_then_replace_parent(self, candidate_path, *, dir_fd=None):
        identity = real_validate(self, candidate_path, dir_fd=dir_fd)
        os.rename(out_dir, bound_dir)
        out_dir.mkdir()
        (out_dir / "attacker-marker").write_bytes(attacker_marker)
        return identity

    def record_verification(self, output_path):
        verification_calls.append(output_path)
        raise AssertionError("verification must not run on a stale output path")

    monkeypatch.setattr(
        PdfRemediator, "_validate_output_candidate", validate_then_replace_parent
    )
    monkeypatch.setattr(PdfRemediator, "_verify_fixes", record_verification)
    config = _config(tmp_path)
    config.verify_fixes = True

    result = PdfRemediator(str(input_pdf), _language_issue(), config).remediate()

    final_name = "typed_syllabus_remediated.pdf"
    assert result.success is False
    assert result.output_file is None
    assert verification_calls == []
    assert not (bound_dir / final_name).exists()
    assert not (out_dir / final_name).exists()
    assert (out_dir / "attacker-marker").read_bytes() == attacker_marker
    assert list(bound_dir.iterdir()) == []
    assert _candidate_directories(bound_dir) == []
    assert _candidate_directories(out_dir) == []


def test_output_directory_symlink_fails_closed(tmp_path):
    """Publication must never bind through an output-directory symlink."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    attacker_dir = tmp_path / "attacker-output"
    attacker_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.symlink_to(attacker_dir, target_is_directory=True)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert list(attacker_dir.iterdir()) == []
    assert "output directory" in (result.error_message or "").lower()


def test_descriptor_publication_fails_closed_when_platform_support_is_missing(
    tmp_path, monkeypatch
):
    """A host missing a required dir-fd primitive cannot publish by pathname."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    unsupported = dict(mod._PUBLICATION_DIR_FD_FUNCTIONS)
    unsupported["rename"] = None
    monkeypatch.setattr(mod, "_PUBLICATION_DIR_FD_FUNCTIONS", unsupported)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "unsupported" in (result.error_message or "").lower()
    assert _candidate_directories(tmp_path / "out") == []


def test_world_writable_output_directory_fails_closed(tmp_path):
    """Publication rejects an output directory writable by other principals."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out_dir.chmod(0o777)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "unexpectedly permissive" in (result.error_message or "")
    assert list(out_dir.iterdir()) == []


@pytest.mark.parametrize("failure_kind", ["success", "save", "validation", "html"])
def test_publication_directory_descriptors_close_on_every_exit(
    tmp_path, monkeypatch, failure_kind
):
    """Both bound directory descriptors close on every save-time exit."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    opened_directory_fds = []
    closed_directory_fds = []
    real_open = os.open
    real_close = os.close

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if flags & getattr(os, "O_DIRECTORY", 0):
            opened_directory_fds.append(descriptor)
        return descriptor

    def tracking_close(descriptor):
        if descriptor in opened_directory_fds:
            closed_directory_fds.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(mod.os, "open", tracking_open)
    monkeypatch.setattr(mod.os, "close", tracking_close)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))

    if failure_kind == "save":

        def fail_save(self, document, candidate_path):
            raise OSError("simulated candidate save failure")

        monkeypatch.setattr(PdfRemediator, "_write_pdf_output", fail_save)
    elif failure_kind == "validation":

        def fail_validation(self, candidate_path, *, dir_fd=None):
            raise RuntimeError("simulated candidate validation failure")

        monkeypatch.setattr(
            PdfRemediator, "_validate_output_candidate", fail_validation
        )
    elif failure_kind == "html":
        remediator._html_output = "<main>candidate</main>"

        def fail_html(self, candidate_path, *, dir_fd=None):
            raise OSError("simulated HTML candidate failure")

        monkeypatch.setattr(PdfRemediator, "_write_html_candidate", fail_html)

    result = remediator.remediate()

    assert result.success is (failure_kind == "success")
    assert len(opened_directory_fds) == 2
    assert set(closed_directory_fds) == set(opened_directory_fds)


def test_publication_descriptor_close_failure_reports_bound_path(tmp_path, monkeypatch):
    """A directory-fd close failure is explicit and fails the save closed."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    opened_directory_fds = []
    failed_fds = set()
    real_open = os.open
    real_close = os.close

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if flags & os.O_DIRECTORY:
            opened_directory_fds.append(descriptor)
        return descriptor

    def failing_directory_close(descriptor):
        if descriptor in opened_directory_fds and not failed_fds:
            failed_fds.add(descriptor)
            real_close(descriptor)
            raise OSError("simulated descriptor close failure")
        return real_close(descriptor)

    monkeypatch.setattr(mod.os, "open", tracking_open)
    monkeypatch.setattr(mod.os, "close", failing_directory_close)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert "descriptor close failed" in (result.error_message or "").lower()
    assert str(tmp_path / "out") in (result.error_message or "")
    assert any(
        "descriptor close failed" in warning.lower()
        and str(tmp_path / "out") in warning
        for warning in result.warnings
    )
    assert _candidate_directories(tmp_path / "out") == []


def test_output_byte_identical_to_original_is_rejected(tmp_path, monkeypatch):
    """A no-op byte-for-byte copy cannot be reported as remediated output."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)

    def copy_original(self, document, output_path):
        shutil.copy2(self.file_path, output_path)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", copy_original)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert "byte-identical" in (result.error_message or "")
    assert not (tmp_path / "out" / "typed_syllabus_remediated.pdf").exists()
    assert _candidate_directories(tmp_path / "out") == []


def test_failed_save_never_exposes_partial_final_output(tmp_path, monkeypatch):
    """A failed first save may leave no deterministic corrupt artifact."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"

    def interrupted_write(self, document, output_path):
        Path(output_path).write_bytes(b"not a complete PDF")
        raise OSError("disk full during save")

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", interrupted_write)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.output_file is None
    assert not (out_dir / "typed_syllabus_remediated.pdf").exists()
    assert _candidate_directories(out_dir) == []


def test_workdir_cleanup_failure_returns_unsuccessful_result(tmp_path, monkeypatch):
    """A workdir cleanup failure must be returned with its retained path."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    real_rmtree = shutil.rmtree

    def blocked_rmtree(path, *args, **kwargs):
        if remediator._work_dir and Path(path) == Path(remediator._work_dir):
            raise PermissionError("simulated workdir cleanup denial")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mod.shutil, "rmtree", blocked_rmtree)

    result = remediator.remediate()

    assert result.success is False
    assert remediator._work_dir is not None
    assert os.path.isdir(remediator._work_dir)
    assert remediator._work_dir in (result.error_message or "")
    assert any(
        remediator._work_dir in warning and "cleanup" in warning.lower()
        for warning in result.warnings
    )


def test_rejected_candidate_cleanup_failure_reports_private_directory(
    tmp_path, monkeypatch
):
    """A rejected candidate cleanup failure reports its retained private dir."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"

    def copy_original(self, document, output_path):
        shutil.copy2(self.file_path, output_path)

    real_rmdir = os.rmdir

    def blocked_candidate_rmdir(path, *args, **kwargs):
        if ".candidate-" in str(path):
            raise PermissionError("simulated candidate cleanup denial")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", copy_original)
    monkeypatch.setattr(mod.os, "rmdir", blocked_candidate_rmdir)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    candidate_dirs = _candidate_directories(out_dir)
    final_path = out_dir / "typed_syllabus_remediated.pdf"
    assert result.success is False
    assert not final_path.exists()
    assert len(candidate_dirs) == 1
    candidate_dir = candidate_dirs[0]
    assert stat.S_IMODE(candidate_dir.stat().st_mode) == 0o700
    assert list(candidate_dir.iterdir()) == []
    assert str(candidate_dir) in (result.error_message or "")
    assert any(
        str(candidate_dir) in warning and "cleanup" in warning.lower()
        for warning in result.warnings
    )


@pytest.mark.parametrize("failure_kind", ["save", "validation"])
def test_prior_html_unchanged_if_pdf_candidate_fails(
    tmp_path, monkeypatch, failure_kind
):
    """PDF preparation failure must not touch either prior final artifact."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_pdf = out_dir / "typed_syllabus_remediated.pdf"
    final_html = out_dir / "typed_syllabus_remediated_accessible.html"
    _make_image_only_pdf(final_pdf)
    final_html.write_text("previous accessible HTML", encoding="utf-8")
    previous_pdf = final_pdf.read_bytes()
    previous_html = final_html.read_bytes()

    if failure_kind == "save":

        def failed_pdf_write(self, document, output_path):
            Path(output_path).write_bytes(b"%PDF-1.7\npartial")
            raise OSError("simulated PDF candidate write failure")

    else:

        def failed_pdf_write(self, document, output_path):
            Path(output_path).write_bytes(b"%PDF-1.7\ninvalid candidate")

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", failed_pdf_write)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = "<main>new accessible HTML</main>"

    result = remediator.remediate()

    assert result.success is False
    assert result.output_file is None
    assert final_pdf.read_bytes() == previous_pdf
    assert final_html.read_bytes() == previous_html
    assert _candidate_directories(out_dir) == []


def test_prior_html_symlink_is_atomically_replaced_without_touching_target(
    tmp_path, monkeypatch
):
    """Publishing HTML replaces a final-path symlink, never its target bytes."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_html = out_dir / "typed_syllabus_remediated_accessible.html"
    symlink_target = tmp_path / "shared_target.html"
    symlink_target.write_text("do not modify this target", encoding="utf-8")
    final_html.symlink_to(symlink_target)

    replacements = []
    html_open_calls = []
    real_rename = os.rename
    real_open = os.open

    def recording_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        source_stat = os.stat(src, dir_fd=src_dir_fd, follow_symlinks=False)
        replacements.append(
            (
                str(src),
                str(dst),
                stat.S_ISREG(source_stat.st_mode),
                src_dir_fd,
                dst_dir_fd,
            )
        )
        return real_rename(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        if Path(path).name == "candidate.html":
            html_open_calls.append((str(path), flags, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(mod.os, "rename", recording_rename)
    monkeypatch.setattr(mod.os, "open", recording_open)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = "<main>new accessible HTML</main>"

    result = remediator.remediate()

    assert result.success, result.error_message
    assert symlink_target.read_text(encoding="utf-8") == "do not modify this target"
    assert not final_html.is_symlink()
    assert final_html.read_text(encoding="utf-8") == remediator._html_output
    html_replacements = [pair for pair in replacements if pair[1] == final_html.name]
    assert len(html_replacements) == 1
    assert html_replacements[0][0] == "candidate.html"
    assert html_replacements[0][2] is True
    assert isinstance(html_replacements[0][3], int)
    assert isinstance(html_replacements[0][4], int)
    html_write_calls = [call for call in html_open_calls if call[1] & os.O_WRONLY]
    assert len(html_write_calls) == 1
    _, write_flags, write_mode = html_write_calls[0]
    assert write_flags & os.O_CREAT
    assert write_flags & os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert write_flags & os.O_NOFOLLOW
    assert write_mode == 0o600
    assert _candidate_directories(out_dir) == []


@pytest.mark.parametrize(
    "failure_kind", ["write", "validation", "empty", "invalid_utf8"]
)
def test_html_candidate_failure_leaves_prior_pdf_and_html_untouched(
    tmp_path, monkeypatch, failure_kind
):
    """HTML preparation must finish before the valid PDF is committed."""
    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_pdf = out_dir / "typed_syllabus_remediated.pdf"
    final_html = out_dir / "typed_syllabus_remediated_accessible.html"
    _make_image_only_pdf(final_pdf)
    final_html.write_text("previous accessible HTML", encoding="utf-8")
    previous_pdf = final_pdf.read_bytes()
    previous_html = final_html.read_bytes()

    html_output = "<main>new accessible HTML</main>"
    expected_error = failure_kind

    if failure_kind == "write":

        def fail_html_write(self, candidate_path, *, dir_fd=None):
            descriptor = os.open(
                candidate_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dir_fd,
            )
            try:
                os.write(descriptor, b"partial HTML")
            finally:
                os.close(descriptor)
            raise OSError("simulated HTML candidate write failure")

        monkeypatch.setattr(
            PdfRemediator, "_write_html_candidate", fail_html_write, raising=False
        )
    else:

        if failure_kind == "empty":
            html_output = ""
            expected_error = "empty"
        elif failure_kind == "invalid_utf8":

            def write_invalid_utf8(self, candidate_path, *, dir_fd=None):
                descriptor = os.open(
                    candidate_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=dir_fd,
                )
                try:
                    os.write(descriptor, b"\xff\xfe")
                finally:
                    os.close(descriptor)

            monkeypatch.setattr(
                PdfRemediator,
                "_write_html_candidate",
                write_invalid_utf8,
                raising=False,
            )
            expected_error = "UTF-8"
        else:

            def fail_html_validation(self, candidate_path, *, dir_fd=None):
                raise RuntimeError("simulated HTML candidate validation failure")

            monkeypatch.setattr(
                PdfRemediator,
                "_validate_html_candidate",
                fail_html_validation,
                raising=False,
            )

    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = html_output

    result = remediator.remediate()

    assert result.success is False
    assert result.output_file is None
    assert final_pdf.read_bytes() == previous_pdf
    assert final_html.read_bytes() == previous_html
    assert expected_error.lower() in (result.error_message or "").lower()
    assert _candidate_directories(out_dir) == []


def test_html_final_replace_failure_keeps_pdf_success_and_prior_html(
    tmp_path, monkeypatch
):
    """Optional HTML publication failure cannot roll back a valid PDF commit."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    final_pdf = out_dir / "typed_syllabus_remediated.pdf"
    final_html = out_dir / "typed_syllabus_remediated_accessible.html"
    _make_image_only_pdf(final_pdf)
    final_html.write_text("previous accessible HTML", encoding="utf-8")
    previous_pdf = final_pdf.read_bytes()
    previous_html = final_html.read_bytes()
    real_rename = os.rename

    def fail_html_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if dst == final_html.name:
            raise OSError("simulated final HTML replace failure")
        return real_rename(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(mod.os, "rename", fail_html_rename)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = "<main>new accessible HTML</main>"

    result = remediator.remediate()

    assert result.success, result.error_message
    assert result.output_file == str(final_pdf)
    assert final_pdf.read_bytes() != previous_pdf
    assert final_html.read_bytes() == previous_html
    assert any(
        "HTML" in warning and "failed" in warning.lower() and "prior" in warning.lower()
        for warning in result.warnings
    )
    assert _candidate_directories(out_dir) == []


@pytest.mark.parametrize("mutation", ["replace", "truncate", "unlink"])
def test_output_claim_and_verification_use_exact_bytes_after_output_path_mutates(
    tmp_path, monkeypatch, mutation
):
    """Verification consumes the retained claim even after lexical path drift."""
    import src.education.pdf_processor as processor_mod
    import src.education.remediation.pdf_remediator as remediator_mod
    import src.education.validation.matterhorn as matterhorn_mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    expected = []
    processor_reads = []
    matterhorn_reads = []
    verification_dirs = []
    real_save = PdfRemediator._save_document
    real_mkdtemp = tempfile.mkdtemp

    def save_then_mutate_path(self, document):
        output_path = real_save(self, document)
        with self.result.open_output_stream() as stream:
            expected.append(stream.read())
        output = Path(output_path)
        if mutation == "replace":
            replacement = output.with_name("attacker.pdf")
            replacement.write_bytes(b"attacker replacement")
            os.replace(replacement, output)
        elif mutation == "truncate":
            output.write_bytes(b"")
        else:
            output.unlink()
        return output_path

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "aelira_pdf_verification_":
            verification_dirs.append(path)
        return path

    def process_exact_bytes(self, path):
        verification_path = Path(path)
        processor_reads.append(verification_path.read_bytes())
        assert stat.S_IMODE(verification_path.parent.stat().st_mode) == 0o700
        assert verification_path.parent != Path(expected_output).parent
        return SimpleNamespace(issues=[], compliance_score=100.0)

    def validate_exact_bytes(self, path):
        matterhorn_reads.append(Path(path).read_bytes())
        return None

    config = _config(tmp_path)
    config.verify_fixes = True
    expected_output = str(tmp_path / "out" / "typed_syllabus_remediated.pdf")
    monkeypatch.setattr(PdfRemediator, "_save_document", save_then_mutate_path)
    monkeypatch.setattr(remediator_mod.tempfile, "mkdtemp", recording_mkdtemp)
    monkeypatch.setattr(processor_mod.PDFProcessor, "process_pdf", process_exact_bytes)
    monkeypatch.setattr(
        matterhorn_mod.MatterhornValidator, "validate", validate_exact_bytes
    )

    result = PdfRemediator(str(input_pdf), _language_issue(), config).remediate()

    assert result.success, result.error_message
    assert result.verification_passed is True
    assert result.has_output_claim() is True
    assert len(expected) == 1
    assert processor_reads == expected
    assert matterhorn_reads == expected
    with result.open_output_stream() as stream:
        assert stream.read() == expected[0]
    assert len(verification_dirs) == 1
    assert not os.path.exists(verification_dirs[0])


def test_output_claim_snapshots_validated_bytes_before_final_rename_returns(
    tmp_path, monkeypatch
):
    """A caller-visible inode mutation during rename cannot alter the claim."""
    import src.education.pdf_processor as processor_mod
    import src.education.remediation.pdf_remediator as mod
    import src.education.validation.matterhorn as matterhorn_mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    validated_bytes = []
    processor_reads = []
    matterhorn_reads = []
    mutation = b"caller-visible inode mutation"
    real_rename = os.rename

    def rename_then_mutate_final(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src != "candidate.pdf":
            return real_rename(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        candidate_fd = os.open(src, os.O_RDONLY, dir_fd=src_dir_fd)
        try:
            validated_bytes.append(os.read(candidate_fd, 100 * 1024 * 1024))
        finally:
            os.close(candidate_fd)
        result = real_rename(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        final_fd = os.open(dst, os.O_WRONLY | os.O_TRUNC, dir_fd=dst_dir_fd)
        try:
            os.write(final_fd, mutation)
        finally:
            os.close(final_fd)
        return result

    def process_exact_bytes(self, path):
        processor_reads.append(Path(path).read_bytes())
        return SimpleNamespace(issues=[], compliance_score=100.0)

    def validate_exact_bytes(self, path):
        matterhorn_reads.append(Path(path).read_bytes())
        return None

    config = _config(tmp_path)
    config.verify_fixes = True
    monkeypatch.setattr(mod.os, "rename", rename_then_mutate_final)
    monkeypatch.setattr(processor_mod.PDFProcessor, "process_pdf", process_exact_bytes)
    monkeypatch.setattr(
        matterhorn_mod.MatterhornValidator, "validate", validate_exact_bytes
    )

    result = PdfRemediator(str(input_pdf), _language_issue(), config).remediate()

    assert result.success, result.error_message
    assert result.verification_passed is True
    assert len(validated_bytes) == 1
    expected = validated_bytes[0]
    assert expected != mutation
    assert Path(result.output_file).read_bytes() == mutation
    assert result.output_claim_metadata() == {
        "size_bytes": len(expected),
        "sha256": hashlib.sha256(expected).hexdigest(),
        "mime_type": "application/pdf",
        "filename": Path(result.output_file).name,
    }
    assert processor_reads == [expected]
    assert matterhorn_reads == [expected]
    with result.open_output_stream() as stream:
        assert stream.read() == expected


@pytest.mark.parametrize(
    "failure_kind", ["validation", "rename", "html", "verification", "normal"]
)
def test_validated_candidate_descriptor_has_one_owner_and_closes_on_every_exit(
    tmp_path, monkeypatch, failure_kind
):
    """The exact validated fd transfers once or closes on every failure path."""
    import src.education.pdf_processor as processor_mod
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    candidate_read_fds = []
    created_claims = []
    real_open = os.open
    real_rename = os.rename
    real_fitz_open = mod.fitz.open
    real_snapshot = mod.DescriptorBoundOutputClaim._snapshot_from_owned_descriptor

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if path == "candidate.pdf" and flags & os.O_ACCMODE == os.O_RDONLY:
            candidate_read_fds.append(descriptor)
        return descriptor

    def recording_snapshot(cls, descriptor, **kwargs):
        claim = real_snapshot(descriptor, **kwargs)
        created_claims.append(claim)
        return claim

    monkeypatch.setattr(mod.os, "open", tracking_open)
    monkeypatch.setattr(
        mod.DescriptorBoundOutputClaim,
        "_snapshot_from_owned_descriptor",
        classmethod(recording_snapshot),
    )
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))

    if failure_kind == "validation":

        def fail_candidate_parse(*args, **kwargs):
            if "stream" in kwargs:
                raise RuntimeError("simulated candidate parse failure")
            return real_fitz_open(*args, **kwargs)

        monkeypatch.setattr(mod.fitz, "open", fail_candidate_parse)
    elif failure_kind == "rename":

        def fail_pdf_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            if src == "candidate.pdf":
                raise OSError("simulated PDF rename failure")
            return real_rename(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(mod.os, "rename", fail_pdf_rename)
    elif failure_kind == "html":
        remediator._html_output = "<main>candidate</main>"
        monkeypatch.setattr(
            PdfRemediator,
            "_validate_html_candidate",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated HTML validation failure")
            ),
        )
    elif failure_kind == "verification":
        remediator.config.verify_fixes = True
        monkeypatch.setattr(
            processor_mod.PDFProcessor,
            "process_pdf",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("simulated verification failure")
            ),
        )

    result = remediator.remediate()

    assert len(candidate_read_fds) == 1
    if failure_kind == "normal":
        assert result.success is True
        assert result.has_output_claim() is True
        result.close_output_claim()
    else:
        if failure_kind != "verification":
            assert result.success is False
        assert result.has_output_claim() is False
    if failure_kind == "validation":
        assert created_claims == []
    else:
        assert len(created_claims) == 1
        assert created_claims[0].closed is True
    with pytest.raises(OSError):
        os.fstat(candidate_read_fds[0])


def test_post_commit_no_success_closes_output_claim(tmp_path, monkeypatch):
    """A later remediation failure cannot leak a successfully committed claim."""
    import src.education.remediation.pdf_remediator as mod

    input_pdf = tmp_path / "typed_syllabus.pdf"
    _make_text_pdf(input_pdf)
    candidate_read_fds = []
    real_open = os.open

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        descriptor = real_open(path, flags, mode, *args, **kwargs)
        if path == "candidate.pdf" and flags & os.O_ACCMODE == os.O_RDONLY:
            candidate_read_fds.append(descriptor)
        return descriptor

    monkeypatch.setattr(mod.os, "open", tracking_open)
    monkeypatch.setattr(
        PdfRemediator,
        "_reconcile_content_tagger_fixes",
        lambda self: (_ for _ in ()).throw(RuntimeError("post-commit failure")),
    )

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success is False
    assert result.has_output_claim() is False
    assert len(candidate_read_fds) == 1
    with pytest.raises(OSError):
        os.fstat(candidate_read_fds[0])
