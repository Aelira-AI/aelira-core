"""Authored metadata survives; missing metadata never becomes plausible fiction."""

from types import SimpleNamespace
import shutil
import subprocess

import pikepdf
import pytest
from bs4 import BeautifulSoup

from src.education.latex_metadata import (
    extract_metadata,
    save_html_metadata,
    save_pdf_metadata,
)
from src.education.latex_diagnostics import classify, conversion_session
from src.education.remediation.base import IssueCategory, RemediationConfig
from src.education.remediation.latex_converter import LaTeXConverter
from src.education.remediation.latex_remediator import LatexRemediator


def source(language="ngerman", extra="", body="Ein Text."):
    return (
        r"\documentclass{article}"
        + "\n"
        + (rf"\usepackage[{language}]{{babel}}" + "\n" if language else "")
        + extra
        + "\n"
        + r"\begin{document}"
        + body
        + r"\end{document}"
    )


@pytest.mark.parametrize(
    "language,tag",
    [
        ("ngerman", "de"),
        ("english", "en"),
        ("english,main=ngerman", "de"),
        (None, None),
    ],
)
def test_authored_literal_fields_and_language(language, tag):
    metadata = extract_metadata(source(language, r"\title{Grüße}\author{Test Author}"))
    assert (metadata.language, metadata.title, metadata.author) == (
        tag,
        "Grüße",
        "Test Author",
    )
    assert not metadata.issues


@pytest.mark.parametrize(
    "extra",
    [r"\hypersetup{pdflang=en}", r"\title{One}\title{Two}", r"\author{\unknown}"],
)
def test_conflicts_and_macro_metadata_are_explicit(extra):
    assert extract_metadata(source(extra=extra)).issues == {"metadata_ambiguous"}


def test_no_rule_template_or_ai_can_invent_unknown_metadata(tmp_path):
    path = tmp_path / "not-a-title.tex"
    original = source(None)
    path.write_text(original)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    for category, description in [
        (IssueCategory.LANGUAGE, "Missing language"),
        (IssueCategory.TITLE, "Missing author"),
        (IssueCategory.TITLE, "Missing title"),
    ]:
        issue = SimpleNamespace(category=category, description=description)
        assert not remediator.can_auto_fix(issue)
        assert remediator._get_rule_based_fix(issue, None) is None
        assert remediator._get_template_fix(issue) is None
        assert remediator._get_ai_generated_fix(issue, None, client=object()) is None
    assert remediator._apply_structure_fix("add accessibility package")
    metadata = extract_metadata(remediator._modified_content)
    assert (metadata.language, metadata.title, metadata.author) == (None, None, None)
    assert path.read_text() == original


def test_existing_multilingual_source_is_not_rewritten(tmp_path):
    original = source(
        "english,main=ngerman",
        r"\title{Original}\author{Test Author}",
        r"Deutsch \foreignlanguage{english}{Hello}.",
    )
    path = tmp_path / "source.tex"
    path.write_text(original)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    assert not remediator._apply_language_fix("english")
    assert remediator._modified_content == original
    assert remediator._apply_language_fix("de")
    assert r"\usepackage[english,main=ngerman]{babel}" in remediator._modified_content
    assert r"\foreignlanguage{english}{Hello}" in remediator._modified_content
    assert path.read_text() == original


@pytest.mark.parametrize(
    "declarations",
    [
        r"\title{Grüße, Welt}\author{Test Author}",
        r"\RequirePackage{hyperref}\hypersetup{pdftitle={Grüße, Welt},pdfauthor={Test Author}}",
        r"\usepackage{amsmath,hyperref}\title{Grüße, Welt}\author{Test Author}",
    ],
)
def test_structure_metadata_uses_supported_keys_and_preserves_source(
    tmp_path, declarations
):
    original = source(extra=declarations)
    path = tmp_path / "source.tex"
    path.write_text(original)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    assert remediator._apply_structure_fix("accessibility")
    candidate = remediator._modified_content
    document_metadata = candidate.split(r"\documentclass", 1)[0]
    assert "pdfauthor" not in document_metadata
    assert "pdftitle" not in document_metadata
    assert "lang={de}" in document_metadata
    assert "pdfstandard=ua-1" in document_metadata
    assert "pdfversion=1.7" in document_metadata
    assert r"\hypersetup{pdfauthor={Test Author},pdftitle={Grüße, Welt}}" in candidate
    assert extract_metadata(candidate) == extract_metadata(original)
    assert (
        candidate.split(r"\begin{document}", 1)[1]
        == original.split(r"\begin{document}", 1)[1]
    )
    assert candidate.count("hyperref}") == 1
    assert remediator._apply_structure_fix("accessibility")
    assert remediator._modified_content == candidate
    assert path.read_text() == original


@pytest.mark.parametrize(
    "declarations",
    [
        r"\title{One}\hypersetup{pdftitle={Two}}",
        r"\author{\unknown}",
        r"\title[Short]{Long}",
        r"\DocumentMetadata{lang=de}\title{One}\title{Two}",
    ],
)
def test_structure_metadata_refuses_ambiguous_or_unsupported_sources(
    tmp_path, declarations
):
    original = source(extra=declarations)
    path = tmp_path / "source.tex"
    path.write_text(original)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    assert not remediator._apply_structure_fix("accessibility")
    assert remediator._modified_content == original
    assert not remediator._modifications
    assert path.read_text() == original


@pytest.mark.skipif(not shutil.which("lualatex"), reason="LuaLaTeX not available")
@pytest.mark.parametrize(
    "declarations",
    [
        r"\title{Grüße, Welt}\author{Test Author}",
        r"\RequirePackage{hyperref}\hypersetup{pdftitle={Grüße, Welt},pdfauthor={Test Author}}",
    ],
)
def test_real_structure_candidate_retains_pdf_metadata(tmp_path, declarations):
    """Compilation and saved metadata are evidence, not PDF/UA conformance."""
    original = source(extra=declarations)
    path = tmp_path / "source.tex"
    path.write_text(original)
    remediator = LatexRemediator(str(path), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    assert remediator._apply_structure_fix("accessibility")
    candidate = tmp_path / "candidate.tex"
    candidate.write_text(remediator._modified_content)
    for _ in range(2):
        compiled = subprocess.run(
            [
                shutil.which("lualatex"),
                "--no-shell-escape",
                "--interaction=nonstopmode",
                "--halt-on-error",
                candidate.name,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    with pikepdf.open(candidate.with_suffix(".pdf")) as pdf:
        assert pdf.pdf_version == "1.7"
        assert pdf.Root.Lang == "de"
        assert pdf.docinfo.Title == "Grüße, Welt"
        assert pdf.docinfo.Author == "Test Author"
    # Exercise the production preservation pass too: older TeX runtimes treat
    # commas as XMP list separators even when PDF Info retains the full title.
    assert LaTeXConverter()._preserve_metadata(
        candidate, candidate.with_suffix(".pdf"), "pdf"
    )
    with pikepdf.open(candidate.with_suffix(".pdf")) as pdf:
        assert pdf.pdf_version == "1.7"
        assert pdf.Root.Lang == "de"
        assert pdf.docinfo.Title == "Grüße, Welt"
        assert pdf.docinfo.Author == "Test Author"
        with pdf.open_metadata() as xmp:
            assert xmp["dc:title"] == "Grüße, Welt"
            assert xmp["dc:creator"] == ["Test Author"]
            assert xmp["pdfuaid:part"] == "1"
    assert path.read_text() == original


@pytest.mark.parametrize("language", ["ngerman", "english", None])
def test_reopened_html_replaces_converter_defaults_with_source(language, tmp_path):
    path = tmp_path / "saved.html"
    path.write_text(
        '<html lang="en" xml:lang="en"><head><title>Invented</title><meta name="author" content="Invented"></head><body><math><mi>x</mi></math></body></html>'
    )
    metadata = extract_metadata(
        source(language, r"\title{Grüße}\author{Test Author}" if language else "")
    )
    assert save_html_metadata(path, metadata)
    saved = BeautifulSoup(path.read_text(), "html.parser")
    assert saved.html.get("lang") == metadata.language
    assert saved.math.mi.string == "x"
    assert (saved.title.string if saved.title else None) == metadata.title
    assert not saved.html.has_attr("xml:lang")


def test_lost_or_wrong_span_language_is_refused(tmp_path):
    metadata = extract_metadata(
        source("english", body=r"Hello \foreignlanguage{ngerman}{Guten Tag}.")
    )
    assert metadata.spans == [("de", "Guten Tag")]
    path = tmp_path / "saved.html"
    for lang, accepted in [("de", True), ("en", False), ("", False)]:
        path.write_text(
            f'<html><body>Hello <span lang="{lang}">Guten Tag</span>.</body></html>'
        )
        assert save_html_metadata(path, metadata) is accepted


@pytest.mark.parametrize("language", ["ngerman", "english", None])
def test_reopened_pdf_info_and_xmp_come_from_source(language, tmp_path):
    path = tmp_path / "saved.pdf"
    with pikepdf.Pdf.new() as pdf:
        pdf.add_blank_page()
        pdf.Root.Lang = "en"
        pdf.docinfo.Title = "Invented"
        pdf.docinfo.Author = "Invented"
        pdf.save(path)
    metadata = extract_metadata(
        source(language, r"\title{Grüße}\author{Test Author}" if language else "")
    )
    assert save_pdf_metadata(path, metadata)
    with pikepdf.open(path) as pdf:
        assert pdf.Root.get("/Lang") == metadata.language
        assert pdf.docinfo.get("/Title") == metadata.title
        assert pdf.docinfo.get("/Author") == metadata.author
        with pdf.open_metadata() as xmp:
            assert xmp.get("dc:creator") == (
                [metadata.author] if metadata.author else None
            )
            assert set(xmp.get("dc:language") or []) == (
                {metadata.language} if metadata.language else set()
            )
        assert "/MarkInfo" not in pdf.Root


def test_metadata_refusal_receipt_binds_source_and_candidate_without_text(tmp_path):
    tex = tmp_path / "source.tex"
    tex.write_text(source(extra=r"\hypersetup{pdflang=en}"))
    html = tmp_path / "candidate.html"
    html.write_text("<html><body>text</body></html>")
    with conversion_session() as stages:
        assert not LaTeXConverter()._preserve_metadata(tex, html, "html")
    assert stages[-1].blocked
    assert stages[-1].diagnostics[0].code == "metadata_ambiguous"
    assert "source.tex" not in stages[-1].model_dump_json()


def test_language_package_failure_has_environment_reason():
    result = classify("Package babel Error: Unknown option 'ngerman'", "", exit_code=1)
    assert "language_environment_unavailable" in {d.code for d in result}


@pytest.mark.parametrize(
    "declaration",
    [
        r"\newcommand{\unexecuted}{\author{Not authored}}",
        r"\iftrue\title{Conditional}\fi",
        r"\setdefaultlanguage[variant=british]{english}",
        r"\hypersetup{pdftitle={Nested {title}}}",
        r"\title[Short]{Full title}",
        r"\babelprovide[main,import]{german}",
        r"\begin{otherlanguage*}{german}Text\end{otherlanguage*}",
    ],
)
def test_unsupported_declarations_never_become_literal_facts(declaration):
    assert "metadata_unsupported" in extract_metadata(source(extra=declaration)).issues


def test_document_class_language_and_comments():
    metadata = extract_metadata(
        r"\documentclass[ngerman]{article}"
        + "\n% \\author{Ignored}\n"
        + r"\begin{document}Text\end{document}"
    )
    assert metadata.language == "de" and metadata.author is None


def test_mixed_language_selects_supported_route_before_conversion(
    tmp_path, monkeypatch
):
    path = tmp_path / "source.tex"
    path.write_text(
        source("english", body=r"Hello \foreignlanguage{ngerman}{Guten Tag}.")
    )
    converter = LaTeXConverter()
    converter.ALLOWED_DIRS = [str(tmp_path)]
    converter.pandoc_available = converter.latexml_available = True
    called = []
    monkeypatch.setattr(
        converter, "_convert_with_pandoc", lambda *a: called.append("pandoc")
    )
    monkeypatch.setattr(
        converter, "_convert_with_latexml", lambda *a: called.append("latexml")
    )
    converter.convert_to_html(str(path))
    assert called == ["pandoc"]
