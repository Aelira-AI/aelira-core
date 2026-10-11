"""Actual HTML route controls for literal siunitx option fidelity."""

import hashlib
from pathlib import Path
import shutil

import pytest
from bs4 import BeautifulSoup

from src.education.latex_siunitx_options import inspect_siunitx
from src.education.remediation.latex_converter import LaTeXConverter


def source_file(tmp_path: Path, expression: str) -> Path:
    source = tmp_path / "case.tex"
    source.write_text(
        "\\documentclass{article}\n\\usepackage{siunitx}\n"
        "\\begin{document}\n$" + expression + "$\n\\end{document}\n",
        encoding="utf-8",
    )
    return source


def converter(tmp_path: Path) -> LaTeXConverter:
    instance = LaTeXConverter()
    instance.ALLOWED_DIRS = [str(tmp_path)]
    return instance


@pytest.mark.parametrize(
    "expression,code",
    [
        (
            r"\qty[round-mode=places,round-precision=1]{1.234}{\metre}",
            "siunitx_options_unverified",
        ),
        (
            r"\qty[output-decimal-marker={,}]{1.23}{\metre}",
            "siunitx_options_unverified",
        ),
        (r"\qtyrange[range-units=single]{1}{2}{\metre}", "siunitx_options_unverified"),
        (
            r"\sisetup{round-mode=places,round-precision=1}\qty{1.234}{\metre}",
            "siunitx_options_unverified",
        ),
        (r"\qty[unknown-option=true]{1}{\metre}", "siunitx_options_unverified"),
        (r"\SI{3}[pre]{\metre}", "siunitx_syntax_ambiguous"),
        (r"\qty{not-a-number}{\metre}", "siunitx_number_unverified"),
        (r"\qty{\value}{\metre}", "siunitx_number_unverified"),
        (
            r"\ang[round-mode=places,round-precision=1]{1.234}",
            "siunitx_options_unverified",
        ),
        (r"\ang{1;2;3;4}", "siunitx_number_unverified"),
        (r"\ang{not-an-angle}", "siunitx_number_unverified"),
    ],
)
def test_actual_route_refuses_unverified_siunitx(
    tmp_path, monkeypatch, expression, code
):
    source = source_file(tmp_path, expression)
    instance = converter(tmp_path)
    instance.pandoc_available = True
    called = []

    def unreachable(*args, **kwargs):
        called.append(args)
        raise AssertionError("Unsupported source reached converter")

    monkeypatch.setattr(instance, "_convert_with_pandoc", unreachable)
    monkeypatch.setattr(instance, "_convert_with_latexml", unreachable)
    receipts = {}
    assert instance.convert_to_html(str(source), conversion_receipts=receipts) is None
    receipt = receipts["html"]
    assert receipt.status == "refused"
    assert receipt.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    stage = next(
        stage
        for stage in receipt.stages
        if any(d.code == code for d in stage.diagnostics)
    )
    assert stage.input_sha256 == receipt.source_sha256
    assert stage.diagnostics[0].source_line == 4
    assert called == []


@pytest.mark.parametrize(
    "expression",
    [
        r"\qty{3.00e8}{\metre\per\second}",
        r"\qty{1.23(4)e5}{\milli\metre\per\second\squared}",
        r"\unit{\metre\per\second}",
        r"\qtyrange{1}{2}{\metre}",
        r"\ang{1.234}",
        r"\ang{1;2;3}",
    ],
)
@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc unavailable")
def test_actual_pandoc_route_keeps_plain_siunitx(tmp_path, expression):
    source = source_file(tmp_path, expression)
    instance = converter(tmp_path)
    receipt = {}
    output = instance.convert_to_html(str(source), conversion_receipts=receipt)
    assert output and Path(output).is_file()
    assert receipt["html"].status == "accepted"
    assert receipt["html"].decision.selected_route == "pandoc"
    assert "<math" in Path(output).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "expression,expected",
    [
        (r"\ang{1.234}", ["1.234", "°"]),
        (r"\ang{1;2;3}", ["1", "°", "2", "′", "3", "″"]),
        (r"\ang{;2;3}", ["2", "′", "3", "″"]),
        (r"\ang{1;;3}", ["1", "°", "3", "″"]),
    ],
)
@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc unavailable")
def test_actual_pandoc_route_preserves_plain_angle_tokens(
    tmp_path, expression, expected
):
    source = source_file(tmp_path, expression)
    receipt = {}
    output = converter(tmp_path).convert_to_html(
        str(source), conversion_receipts=receipt
    )
    assert output and receipt["html"].status == "accepted"
    math = BeautifulSoup(Path(output).read_text(encoding="utf-8"), "html.parser").find(
        "math"
    )
    assert math is not None
    assert [item.get_text() for item in math.find_all(["mn", "mtext"])] == expected
    if expression == r"\ang{1.234}":
        assert receipt["html"].source_sha256 == (
            "2ffa2f4a1bc9d41e2ace17c186acec187bce9125a6c6248c514eb171857d269e"
        )


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"% \qty[bad]{1}{\metre}" + "\n" + r"\qty{2}{\metre}", None),
        (r"\verb|\qty[bad]{1}{\metre}| \qty{2}{\metre}", None),
        (r"\lstinline[language=TeX]|\qty[bad]{1}{\metre}| \qty{2}{\metre}", None),
        (r"\mintinline{latex}|\qty[bad]{1}{\metre}| \qty{2}{\metre}", None),
        (r"\begin{verbatim}\qty[bad]{1}{\metre}\end{verbatim}\qty{2}{\metre}", None),
        (r"\newcommand{\unused}{\qty[bad]{1}{\metre}} \qty{2}{\metre}", None),
        (
            r"\newcommand{\wrapped}{\qty[bad]{1}{\metre}} \wrapped",
            "siunitx_syntax_ambiguous",
        ),
        (
            r"\newcommand{\wrapped}{\qtyrange{1}{2}{\metre}} \wrapped",
            "siunitx_syntax_ambiguous",
        ),
        (r"\newcommand\unused{plain text} \qty{2}{\metre}", None),
        (r"\newcommand \qty{2}{\metre}", None),
        (r"\renewcommand{\qty}[2]{#1}\qty{2}{\metre}", "siunitx_syntax_ambiguous"),
        (r"\qty(\frac{1}{2})", None),
        (r"\qty{2}{\metre} % \sisetup{round-mode=places}", None),
        (r"\usepackage[round-mode=places]{siunitx}", "siunitx_options_unverified"),
    ],
)
def test_scanner_contexts_and_definitions(source, expected):
    found = inspect_siunitx(r"\usepackage{siunitx}" + "\n" + source)
    assert (found[0] if found else None) == expected


def test_physics_qty_without_siunitx_is_outside_this_gate():
    assert inspect_siunitx(r"\usepackage{physics}\qty{\frac{1}{2}}") is None


@pytest.mark.parametrize(
    "source,code",
    [
        (
            r"\usepackage{siunitx}"
            r"\newcommand{\first}{\qty[round-mode=places]{1.234}{\metre}}"
            r"\newcommand{\second}{\first}\second",
            "siunitx_syntax_ambiguous",
        ),
        (
            r"\newcommand{\load}{\usepackage{siunitx}}"
            r"\load\qty[round-mode=places]{1.234}{\metre}",
            "siunitx_syntax_ambiguous",
        ),
        (
            r"\usepackage{siunitx}" r"\unit{\num[round-mode=places]{1.234}\metre}",
            "siunitx_options_unverified",
        ),
        (
            r"\newcommand{\pkg}{siunitx}\usepackage{\pkg}"
            r"\qty[round-mode=places]{1.234}{\metre}",
            "siunitx_syntax_ambiguous",
        ),
        (
            r"\PassOptionsToPackage{round-mode=places}{siunitx}"
            r"\usepackage{siunitx}\qty{1.234}{\metre}",
            "siunitx_options_unverified",
        ),
    ],
)
def test_invoked_indirection_and_nested_options_reach_actual_route(
    tmp_path, source, code
):
    path = tmp_path / "case.tex"
    path.write_text(
        r"\documentclass{article}" + "\n" + source + "\n",
        encoding="utf-8",
    )
    receipt = {}
    assert (
        converter(tmp_path).convert_to_html(str(path), conversion_receipts=receipt)
        is None
    )
    report = receipt["html"]
    assert report.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report.status == "refused"
    assert any(
        stage.input_sha256 == report.source_sha256
        and any(d.code == code for d in stage.diagnostics)
        for stage in report.stages
    )


@pytest.mark.parametrize(
    "packages",
    [
        r"\usepackage{physics}\usepackage{siunitx}",
        r"\usepackage{siunitx}\usepackage{physics}",
    ],
)
def test_physics_qty_mixed_package_orders_keep_physics_dispatch(tmp_path, packages):
    source = (
        r"\documentclass{article}" + "\n" + packages + "\n"
        r"\begin{document}$\qty{x}$\end{document}"
    )
    assert inspect_siunitx(source) is None
    path = tmp_path / "case.tex"
    path.write_text(source, encoding="utf-8")
    receipt = {}
    converter(tmp_path).convert_to_html(str(path), conversion_receipts=receipt)
    assert not any(
        d.code.startswith("siunitx_")
        for stage in receipt["html"].stages
        for d in stage.diagnostics
    )


def test_unused_dynamic_package_name_does_not_trigger_siunitx_gate():
    assert (
        inspect_siunitx(r"\newcommand{\pkg}{physics}\usepackage{\pkg} Plain text.")
        is None
    )


def test_large_ordinary_source_stays_convertible_by_source_gate():
    source = (
        r"\documentclass{article}\usepackage{siunitx}\begin{document}"
        + "Ordinary accessible text. " * 60000
        + r"$\qty{3.00e8}{\metre\per\second}$\end{document}"
    )
    assert len(source.encode("utf-8")) > 1024 * 1024
    assert inspect_siunitx(source) is None


@pytest.mark.parametrize(
    "source",
    [
        r"\usepackage{siunitx}" + r"\unit{" * 250 + r"\metre" + "}" * 250,
        r"\newcommand{\noop}{" + r"\relax" * 1200 + "}" + r"\noop" * 20,
    ],
)
def test_cumulative_parse_work_refuses_nested_or_repeated_source(source):
    found = inspect_siunitx(source)
    assert found is not None and found[0] == "siunitx_source_unbounded"
