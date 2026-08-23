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
    real_replace = os.replace

    def recording_replace(src, dst):
        source = Path(src)
        replacements.append(
            {
                "src": str(source),
                "dst": str(dst),
                "parent_mode": stat.S_IMODE(source.parent.stat().st_mode),
            }
        )
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", recording_replace)

    result = PdfRemediator(
        str(input_pdf), _language_issue(), _config(tmp_path)
    ).remediate()

    assert result.success, result.error_message
    assert result.output_file == str(final_path)
    assert final_path.read_bytes() != previous_bytes
    final_replacements = [
        pair for pair in replacements if pair["dst"] == str(final_path)
    ]
    assert len(final_replacements) == 1
    replacement = final_replacements[0]
    candidate_path = replacement["src"]
    candidate_dir = Path(candidate_path).parent
    assert candidate_dir.parent == out_dir
    assert candidate_dir != out_dir
    assert candidate_dir.name.startswith(".typed_syllabus_remediated.candidate-")
    assert Path(candidate_path).name == "candidate.pdf"
    assert replacement["parent_mode"] == 0o700
    assert replacement["dst"] == str(final_path)
    assert not os.path.exists(candidate_path)
    assert not candidate_dir.exists()
    with fitz.open(str(final_path)) as delivered:
        assert delivered.page_count == 1


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

    real_rmtree = shutil.rmtree

    def blocked_candidate_rmtree(path, *args, **kwargs):
        candidate_dir = Path(path)
        if candidate_dir.parent == out_dir and ".candidate-" in candidate_dir.name:
            raise PermissionError("simulated candidate cleanup denial")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(PdfRemediator, "_write_pdf_output", copy_original)
    monkeypatch.setattr(mod.shutil, "rmtree", blocked_candidate_rmtree)

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
    assert (candidate_dir / "candidate.pdf").exists()
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
    real_replace = os.replace
    real_open = os.open

    def recording_replace(src, dst):
        source = Path(src)
        replacements.append(
            (str(source), str(dst), stat.S_ISREG(source.lstat().st_mode))
        )
        return real_replace(src, dst)

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        if Path(path).name == "candidate.html":
            html_open_calls.append((str(path), flags, mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(mod.os, "replace", recording_replace)
    monkeypatch.setattr(mod.os, "open", recording_open)
    remediator = PdfRemediator(str(input_pdf), _language_issue(), _config(tmp_path))
    remediator._html_output = "<main>new accessible HTML</main>"

    result = remediator.remediate()

    assert result.success, result.error_message
    assert symlink_target.read_text(encoding="utf-8") == "do not modify this target"
    assert not final_html.is_symlink()
    assert final_html.read_text(encoding="utf-8") == remediator._html_output
    html_replacements = [pair for pair in replacements if pair[1] == str(final_html)]
    assert len(html_replacements) == 1
    assert Path(html_replacements[0][0]).parent.parent == out_dir
    assert Path(html_replacements[0][0]).name == "candidate.html"
    assert html_replacements[0][2] is True
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

        def fail_html_write(self, candidate_path):
            Path(candidate_path).write_bytes(b"partial HTML")
            raise OSError("simulated HTML candidate write failure")

        monkeypatch.setattr(
            PdfRemediator, "_write_html_candidate", fail_html_write, raising=False
        )
    else:

        if failure_kind == "empty":
            html_output = ""
            expected_error = "empty"
        elif failure_kind == "invalid_utf8":

            def write_invalid_utf8(self, candidate_path):
                Path(candidate_path).write_bytes(b"\xff\xfe")

            monkeypatch.setattr(
                PdfRemediator,
                "_write_html_candidate",
                write_invalid_utf8,
                raising=False,
            )
            expected_error = "UTF-8"
        else:

            def fail_html_validation(self, candidate_path):
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
    real_replace = os.replace

    def fail_html_replace(src, dst):
        if Path(dst) == final_html:
            raise OSError("simulated final HTML replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", fail_html_replace)
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
