"""Project exports retain full context and refuse unverified representations."""

from io import BytesIO
from pathlib import Path
import zipfile
import pytest

from src.education.latex_project import inspect_archive
from src.education import latex_project_conversion as conversion


def project(files=None):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in (
            files
            or {
                "main.tex": r"\documentclass{article}\begin{document}\input{chapter}\end{document}",
                "chapter.tex": "Nested text.",
            }
        ).items():
            archive.writestr(name, content)
    return inspect_archive(stream.getvalue(), "main.tex")


def test_full_context_is_converted_and_originals_unchanged(tmp_path, monkeypatch):
    source = project()
    before = dict(source.files)
    seen = []

    def render(source_path, output, log):
        seen.append(source_path.read_text())
        output.write_text("<p>Nested text.</p>")
        log.write_text("")
        return 0, None

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    result = conversion.convert_project_html(source, tmp_path)
    assert result.path
    assert "Nested text." in seen[0]
    assert r"\input" not in seen[0]
    assert dict(source.files) == before
    receipt = conversion.public_project_provenance(result.provenance)
    assert receipt["output_sha256"] == conversion.digest(Path(result.path).read_bytes())
    assert receipt["archive_sha256"] == source.archive_digest
    assert receipt["accessibility_status"] == "not_verified"


def test_missing_dependency_does_not_invoke_converter(tmp_path, monkeypatch):
    source = project({"main.tex": r"\input{missing}"})
    monkeypatch.setattr(conversion, "_run_pandoc", lambda *a: 1 / 0)
    result = conversion.convert_project_html(source, tmp_path)
    assert result.path is None
    assert result.provenance["status"] == "refused"


def test_unexpected_graphic_remains_a_refusal(tmp_path, monkeypatch):
    def render(source, output, log):
        output.write_text('<p>Nested text.</p><img src="outside.png">')
        log.write_text("")
        return 0, None

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    result = conversion.convert_project_html(project(), tmp_path)
    assert result.path is None
    assert "semantics_not_preserved" in result.provenance["reasons"]


def test_persisted_provenance_cannot_smuggle_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        conversion, "_run_pandoc", lambda *a: (None, "tool_unavailable")
    )
    receipt = conversion.convert_project_html(project(), tmp_path).provenance
    assert conversion.public_project_provenance(receipt)
    assert (
        conversion.public_project_provenance({**receipt, "stderr": "/private/data"})
        is None
    )
    assert (
        conversion.public_project_provenance({**receipt, "reasons": ["/private/data"]})
        is None
    )


def test_dropped_command_is_refused_even_on_exit_zero(tmp_path, monkeypatch):
    def render(source, output, log):
        output.write_text("<p>Surviving text.</p>")
        log.write_text("[INFO] Skipped '\\unknown{LOST}' at line 1 column 2\n")
        return 0, None

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    result = conversion.convert_project_html(project(), tmp_path)
    assert result.path is None
    assert result.provenance["reasons"] == ["unsupported_command"]


@pytest.mark.parametrize(
    "extension,expected", [("pdf", "semantics_unsupported"), ("png", "missing_asset")]
)
def test_unsupported_or_invalid_image_is_an_explicit_refusal(
    tmp_path, monkeypatch, extension, expected
):
    source = project(
        {
            "main.tex": rf"\documentclass{{article}}\begin{{document}}\includegraphics[alt={{A plot.}}]{{plot.{extension}}}\end{{document}}",
            f"plot.{extension}": b"invalid image bytes",
        }
    )

    def render(source, output, log):
        output.write_text(f'<img src="plot.{extension}" alt="A plot.">')
        log.write_text("")
        return 0, None

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    result = conversion.convert_project_html(source, tmp_path)
    assert result.path is None
    assert expected in result.provenance["reasons"]


@pytest.mark.parametrize(
    "definition",
    [
        r"\renewcommand{\title}{Wrong}",
        r"\newcommand{\title}{Wrong}",
        r"\newcommand{\safe}{\title{Wrong}}",
        r"\newcommand{\safe}{\input{other}}",
    ],
)
def test_metadata_affecting_macros_are_not_ignored(definition):
    assert conversion._metadata_source(definition) == definition


def test_project_subprocess_refuses_automatic_multifile_edits(tmp_path):
    from src.jobs.remediation_subprocess import (
        _build_remediator,
        RemediationSubprocessError,
    )

    with pytest.raises(
        RemediationSubprocessError, match="project_source_review_required"
    ):
        _build_remediator(
            {"scan_type": "LATEX", "options": {"use_ai": True}},
            tmp_path / "project.zip",
            tmp_path,
        )


def test_project_original_and_analysis_identities_are_distinct(tmp_path, monkeypatch):
    original = project(
        {
            "main.tex": r"\documentclass{article}\begin{document}\input{chapter}\end{document}",
            "chapter.tex": "Nested $x+1$.\r\n",
        }
    )

    def render(source, output, log):
        output.write_text(
            '<p>Nested <math><semantics><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow><annotation encoding="application/x-tex">x+1</annotation></semantics></math>.</p>'
        )
        log.write_text("")
        return 0, None

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    result = conversion.convert_project_html(original, tmp_path)
    assert result.path
    receipt = conversion.public_project_provenance(result.provenance)
    source = next(
        item
        for item in receipt["original_equations"]
        if item["path_sha256"] == conversion.digest(b"chapter.tex")
    )["equations"]
    assert source["source_sha256"] == conversion.digest(original.files["chapter.tex"])
    assert receipt["source_equations"]["source_sha256"] == receipt["analysis_sha256"]
    assert (
        source["expressions"][0]["content_sha256"]
        == receipt["source_equations"]["expressions"][0]["content_sha256"]
    )
    assert (
        source["expressions"][0]["expression_id"]
        != receipt["source_equations"]["expressions"][0]["expression_id"]
    )
    assert receipt["equations"]["candidate_sha256"] == receipt["output_sha256"]


@pytest.mark.parametrize("kind", ["empty", "unsupported", "duplicate"])
def test_complete_original_inventory_cannot_be_forged(kind):
    from src.education.latex_equation_provenance import source_provenance

    value = conversion.ProjectProvenance(
        archive_sha256="0" * 64, source_sha256="1" * 64
    ).model_dump()
    original = {
        "path_sha256": "2" * 64,
        "equations": source_provenance(
            r"\input{missing}" if kind == "unsupported" else "$x$"
        ).model_dump(),
    }
    value["original_equations_complete"] = True
    value["original_equations"] = (
        [] if kind == "empty" else [original] * (2 if kind == "duplicate" else 1)
    )
    assert conversion.public_project_provenance(value) is None


def test_final_project_html_size_limit_is_enforced_after_enrichment(
    tmp_path, monkeypatch
):
    def render(source, output, log):
        output.write_text("<p>Nested text.</p>")
        log.write_text("")
        return 0, None

    preserve = conversion.save_html_metadata

    def enrich(candidate, metadata):
        saved = preserve(candidate, metadata)
        monkeypatch.setattr(conversion, "MAX_OUTPUT", 4)
        return saved

    monkeypatch.setattr(conversion, "_run_pandoc", render)
    monkeypatch.setattr(conversion, "save_html_metadata", enrich)
    result = conversion.convert_project_html(project(), tmp_path)
    assert result.path is None
    assert result.provenance["reasons"] == ["project_export_limit"]
