"""Wrapper and consumer controls for known loss, independent of converter exits."""

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.education.latex_diagnostics import (
    ConversionDiagnostics,
    ConversionStage,
    classify,
    diagnostic,
    inspect_candidate,
    sha,
)
from src.education.remediation.latex_converter import LaTeXConverter


def test_cold_font_database_creation_is_not_a_missing_dependency():
    cold_cache = "luaotfload | db : Font names database not found, generating new one."
    assert classify(cold_cache, "", exit_code=0) == []
    assert {
        item.code
        for item in classify(
            cold_cache + "\nmodule 'luaotfload-main' not found", "", exit_code=0
        )
    } == {"missing_dependency"}


def test_owned_format_initialization_warnings_do_not_hide_failures():
    notice = "\n".join(
        [
            "mktexfmt [INFO]: writing formats under owned scratch",
            "Beginning to dump on file lualatex.fmt",
            "warning  (pdf backend): no pages of output.",
            "* WARNING: you are switching to fmtutil's per-user formats. *",
            "*         Please read the following warnings!               *",
        ]
    )
    assert classify(notice, "", exit_code=0) == []
    assert {
        d.code for d in classify(notice + "\n! Font not found", "", exit_code=1)
    } == {"process_failed", "missing_dependency"}
    assert (
        classify("warning  (pdf backend): no pages of output.", "", exit_code=0)[
            0
        ].severity
        == "error"
    )


@pytest.fixture
def converter(tmp_path):
    value = LaTeXConverter()
    value.ALLOWED_DIRS = [str(tmp_path)]
    value.latexml_available = True
    value.latexmlpost_available = True
    value.pandoc_available = True
    value.lualatex_available = False
    value.pdflatex_available = False
    return value


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.tex"
    path.write_text(r"\documentclass{article}\begin{document}\[x+1\]\end{document}")
    return path


def runner(
    monkeypatch,
    *,
    parse="",
    post="",
    pandoc="",
    xml=None,
    html=None,
    exit_code=0,
    timeout=False,
):
    calls = []

    def run(args, **kwargs):
        if args[1] in {"--version", "--VERSION"}:
            return SimpleNamespace(
                returncode=0, stdout=f"{args[0]} version 1.2.3", stderr=""
            )
        calls.append(args[0])
        if timeout:
            raise subprocess.TimeoutExpired(args, 10)
        tool = args[0]
        destination = next((x[7:] for x in args if x.startswith("--dest=")), None)
        if tool == "pandoc":
            destination = args[args.index("-o") + 1]
        content = (
            xml or "<document><Math><XMath><XMTok>x</XMTok></XMath></Math></document>"
        )
        if tool != "latexml":
            content = (
                html
                or "<html><body><article><math><mi>x</mi></math></article></body></html>"
            )
        if destination:
            Path(destination).write_text(content)
        else:
            directory = next(
                (
                    x.split("=", 1)[1]
                    for x in args
                    if x.startswith("-output-directory=")
                ),
                None,
            )
            if directory is None:
                directory = kwargs["cwd"]
            (Path(directory) / "source.pdf").write_bytes(b"synthetic PDF")
        return SimpleNamespace(
            returncode=exit_code,
            stdout="",
            stderr={"latexml": parse, "latexmlpost": post, "pandoc": pandoc}.get(
                tool, parse
            ),
        )

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    "stage,message,code",
    [
        (
            "parse",
            "Package tagpdf Warning: The package unicode-math is missing",
            "missing_dependency",
        ),
        (
            "parse",
            "Error:missing_file:include Can't find TeX file private.tex line 12",
            "missing_dependency",
        ),
        (
            "parse",
            "Error:undefined:macro Undefined control sequence line 5",
            "unsupported_command",
        ),
        ("parse", "Error:expected:brace Missing brace line 7", "malformed_expression"),
        (
            "post",
            "Warning:expected:source No graphic source found; skipping",
            "missing_asset",
        ),
        (
            "post",
            "Warning:expected:label unresolved reference private-id",
            "unresolved_reference",
        ),
    ],
)
def test_exit_zero_losses_are_terminal_and_safe(
    converter, source, monkeypatch, stage, message, code
):
    calls = runner(monkeypatch, **{stage: message})
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    report = receipts["html"]
    assert report.status == "refused"
    assert code in [d.code for s in report.stages for d in s.diagnostics]
    assert "pandoc" not in calls
    assert "private" not in report.model_dump_json()
    stages = [s for s in report.stages if s.tool != "inspection"]
    assert stages[-1].version == "1.2.3"
    assert stages[-1].exit_code == 0
    assert stages[-1].candidate_sha256
    if "line" in message:
        assert stages[-1].diagnostics[0].source_line in {5, 7, 12}


def test_positive_retains_semantics_and_warning_classification(
    converter, source, monkeypatch
):
    runner(monkeypatch, post="Warning: font substitution used")
    receipts = {}
    output = converter.convert_to_html(str(source), conversion_receipts=receipts)
    assert output and Path(output).is_file()
    report = receipts["html"]
    assert report.status == "accepted"
    assert report.candidate_sha256 == sha(Path(output).read_bytes())
    assert report.fidelity == "not_assessed"
    attempt = Path(output).parent
    assert attempt.stat().st_mode & 0o777 == 0o700
    xml = attempt / "source.xml"
    assert xml.is_file() and xml.stat().st_mode & 0o777 == 0o600
    parse = next(s for s in report.stages if s.phase == "parse")
    post = next(s for s in report.stages if s.phase == "postprocess")
    assert parse.candidate_sha256 == post.input_sha256 == sha(xml.read_bytes())
    assert "<XMath>" in xml.read_text()
    assert (attempt / "latexml-parse-1.diagnostics.json").is_file()
    assert any(d.code == "font_warning" for s in report.stages for d in s.diagnostics)


@pytest.mark.parametrize(
    "body,code",
    [
        ("<merror>bad</merror>", "error_node"),
        ('<span class="ltx_ERROR">bad</span>', "error_node"),
        (r'<span class="math display">\(\unknown{x}\)</span>', "raw_tex"),
        ('<a href="#missing">1</a>', "unresolved_reference"),
        ('<span class="math">raw</span><math><mi>x</mi></math>', "raw_tex"),
        ('<span class="citation" data-cites="lost"></span>', "unresolved_reference"),
        ('<div class="ltx_equation"><span>1</span></div>', "malformed_expression"),
        ('<img src="absent.png">', "missing_asset"),
        ('<img src="https://example.invalid/image.png">', "missing_asset"),
    ],
)
def test_candidate_inspection_refuses_loss(converter, source, monkeypatch, body, code):
    converter.latexml_available = False
    runner(monkeypatch, html=f"<html><body>{body}</body></html>")
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    assert code in [d.code for s in receipts["html"].stages for d in s.diagnostics]


def test_target_presence_is_not_identity_or_reader_activation(source, tmp_path):
    html = tmp_path / "out.html"
    html.write_text('<a href="#eq1">1</a><div id="eq1">x</div>')
    observation = inspect_candidate(source, html).references[0]
    assert observation.target_exists
    assert (
        observation.target_identity == observation.reader_activation == "not_assessed"
    )
    assert observation.target_sha256 == sha(b"eq1")


@pytest.mark.parametrize(
    "case,code", [("N02", "missing_dependency"), ("N03", "missing_asset")]
)
def test_missing_authored_dependencies_refused_before_conversion(
    converter, source, monkeypatch, case, code
):
    source.write_bytes(
        (
            Path(__file__).parent / "fixtures/latex_validation" / f"{case}.tex"
        ).read_bytes()
    )
    calls = runner(monkeypatch)
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    assert not calls
    assert receipts["html"].stages[0].diagnostics[0].code == code
    assert receipts["html"].stages[0].diagnostics[0].source_line > 0


@pytest.mark.parametrize("engine", ["lualatex", "pdflatex"])
def test_compile_final_warning_blocks_pdf_validation(
    converter, source, monkeypatch, engine
):
    converter.latexml_available = False
    setattr(converter, engine + "_available", True)
    calls = runner(monkeypatch, parse="LaTeX Warning: There were undefined references.")
    receipts = {}
    output, validation = converter.convert_to_pdf_with_validation(
        str(source), conversion_receipts=receipts
    )
    assert output is None
    assert validation.reason == "conversion_failed"
    stages = [s for s in receipts["pdf"].stages if s.tool == engine]
    assert calls == [engine, engine]
    assert [s.diagnostics[0].severity for s in stages] == ["warning", "error"]
    assert [s.pass_number for s in stages] == [1, 2]


@pytest.mark.parametrize("mode", ["timeout", "nonzero", "overflow"])
def test_stage_failure_never_uses_stale_output(converter, source, monkeypatch, mode):
    (source.parent / "source.html").write_text("stale")
    runner(
        monkeypatch,
        timeout=mode == "timeout",
        exit_code=1 if mode == "nonzero" else 0,
        parse="z" * 70000 if mode == "overflow" else "",
    )
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    assert receipts["html"].status == "refused"
    assert len(receipts["html"].model_dump_json()) < 5000


def test_unclassified_warning_requires_review_but_known_warning_does_not():
    assert (
        classify("", "Warning: unexpected converter condition", exit_code=0)[0].severity
        == "error"
    )
    for message in [
        "Overfull \\hbox (2pt)",
        "Warning: font substitution",
        "[WARNING] Deprecated: --mathml",
        "[WARNING] This document requires a nonempty <title> element.",
    ]:
        assert all(d.severity != "error" for d in classify("", message, exit_code=0))


def test_nested_sessions_and_reuse_do_not_share_diagnostics(
    converter, source, monkeypatch
):
    runner(monkeypatch, parse="Error:undefined:macro Undefined control sequence")
    first = {}
    assert converter.convert_to_html(str(source), conversion_receipts=first) is None
    runner(monkeypatch)
    second = {}
    assert converter.convert_to_html(str(source), conversion_receipts=second)
    assert first["html"].status == "refused"
    assert second["html"].status == "accepted"


def test_report_survives_public_job_projection_without_raw_messages():
    from src.education.latex_evidence import conversion_evidence, public_latex_evidence
    from src.jobs.contracts import public_job_result

    report = ConversionDiagnostics(
        source_sha256=sha(b"source"),
        status="refused",
        stages=[
            ConversionStage(
                tool="pandoc",
                phase="parse",
                input_sha256=sha(b"source"),
                diagnostics=[diagnostic("raw_tex")],
            )
        ],
    )
    receipt = conversion_evidence(b"source", None, "html").model_dump()
    receipt["conversion_diagnostics"] = report.model_dump()
    result = public_job_result({"latex_evidence": {"html": receipt}})
    assert (
        result["latex_evidence"]["html"]["conversion_diagnostics"]
        == report.model_dump()
    )
    receipt["conversion_diagnostics"]["stages"][0][
        "raw_error"
    ] = "/private/document.tex"
    assert public_latex_evidence({"html": receipt}) == {}


def test_accepted_report_cannot_hide_failed_stage():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ConversionDiagnostics(
            source_sha256=sha(b"x"),
            candidate_sha256=sha(b"y"),
            status="accepted",
            stages=[
                ConversionStage(
                    tool="inspection",
                    phase="inspect",
                    input_sha256=sha(b"x"),
                    diagnostics=[diagnostic("raw_tex")],
                )
            ],
        )


def test_remediator_preserves_refused_html_diagnostics(converter, source, monkeypatch):
    from src.education.remediation import latex_remediator
    from src.education.remediation.base import RemediationConfig

    runner(
        monkeypatch, post="Warning:expected:source No graphic source found; skipping"
    )
    monkeypatch.setattr(latex_remediator, "get_latex_converter", lambda: converter)
    result = latex_remediator.LatexRemediator(
        str(source), [], RemediationConfig(use_ai=False, latex_output_formats=["html"])
    ).remediate()
    assert not result.success
    report = result.latex_evidence["html"].conversion_diagnostics
    assert report.status == "refused"
    assert result.latex_evidence["html"].conversion.status == "failed"
    assert any(d.code == "missing_asset" for s in report.stages for d in s.diagnostics)


def test_source_figure_without_illustration_is_incomplete(source, tmp_path):
    source.write_text(
        r"\begin{figure}\includegraphics{plot.png}\caption{Caption}\end{figure}"
    )
    candidate = tmp_path / "out.html"
    candidate.write_text("<figure><figcaption>Caption</figcaption></figure>")
    assert inspect_candidate(source, candidate).blocked


@pytest.mark.parametrize(
    "xml,code",
    [
        ("<document><ERROR>lost</ERROR></document>", "error_node"),
        ("<document><Math></document>", "candidate_unreadable"),
    ],
)
def test_semantic_xml_loss_cannot_disappear_in_html(
    converter, source, monkeypatch, xml, code
):
    calls = runner(monkeypatch, xml=xml)
    receipts = {}
    assert converter.convert_to_html(str(source), conversion_receipts=receipts) is None
    assert calls == ["latexml"]
    assert code in [d.code for s in receipts["html"].stages for d in s.diagnostics]


def test_simultaneous_calls_keep_stage_evidence_separate(
    converter, source, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    bad = source.with_name("broken.tex")
    bad.write_text("broken source")

    def run(args, **kwargs):
        if args[1] in {"--version", "--VERSION"}:
            return SimpleNamespace(returncode=0, stdout="1.2.3", stderr="")
        target = Path(next(a[7:] for a in args if a.startswith("--dest=")))
        if args[0] == "latexml":
            barrier.wait(timeout=5)
            target.write_text("<document><Math>x</Math></document>")
            error = (
                "Error:undefined:macro Undefined control sequence"
                if Path(args[-1]).read_text() == "broken source"
                else ""
            )
        else:
            target.write_text("<html><body><math>x</math></body></html>")
            error = ""
        return SimpleNamespace(returncode=0, stdout="", stderr=error)

    monkeypatch.setattr(subprocess, "run", run)

    def convert(path):
        receipts = {}
        output = converter.convert_to_html(str(path), conversion_receipts=receipts)
        return output, receipts["html"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        good_result, bad_result = list(pool.map(convert, [source, bad]))
    assert good_result[0] and good_result[1].status == "accepted"
    assert bad_result[0] is None and bad_result[1].status == "refused"
    assert good_result[1].source_sha256 != bad_result[1].source_sha256
    assert all(not s.blocked for s in good_result[1].stages)


@pytest.mark.parametrize("final_pass,expected", [(False, "warning"), (True, "error")])
def test_mathml_generation_warning_must_resolve_on_final_pass(final_pass, expected):
    findings = classify(
        "WARNING: mathml missing for hash 6A08588F3199FB9A69F0428FB1D71E8E",
        "",
        exit_code=0,
        final_pass=final_pass,
    )
    assert [(item.code, item.severity) for item in findings] == [
        ("missing_mathml", expected)
    ]
