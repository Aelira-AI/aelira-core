"""Independent synthetic output mutations for the real-converter smoke's oracles."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/smoke_latex_compatibility.py"
SPEC = importlib.util.spec_from_file_location("compatibility_oracle_controls", SCRIPT)
oracles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(oracles)

M03 = "<math><msup><mi>x</mi><msub><mi>a</mi><mi>b</mi></msub></msup></math>"
M04 = "<math><msubsup><mi>x</mi><mi>b</mi><mi>a</mi></msubsup></math>"
M04_EQUIVALENT = "<math><msup><msub><mi>x</mi><mi>b</mi></msub><mi>a</mi></msup></math>"
M06 = """<math><mrow><mo>(</mo><mtable>
<mtr><mtd><mn>1</mn></mtd><mtd><mn>0</mn></mtd><mtd><mo>−</mo><mi>i</mi></mtd></mtr>
<mtr><mtd><mi>i</mi></mtd><mtd><mn>2</mn></mtd><mtd><mn>3</mn></mtd></mtr>
</mtable><mo>)</mo></mrow></math>"""
M14_XML = """<document xmlns="http://dlmf.nist.gov/LaTeXML">
<ref labelref="LABEL:eq:energy"/>
<equation labels="LABEL:eq:energy" xml:id="energy-row"/>
<equation labels="LABEL:eq:mass" xml:id="mass-row"/>
<ref labelref="LABEL:eq:mass"/>
</document>"""
M14 = """<a href="#energy-row">1</a><table>
<tr id="energy-row"><td><math><mi>E</mi></math></td><td><math><mrow><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math></td></tr>
<tr id="mass-row"><td><math><mfrac><mi>E</mi><msup><mi>c</mi><mn>2</mn></msup></mfrac></math></td><td><math><mo>=</mo><mi>m</mi></math></td></tr>
</table><a href="#mass-row">2</a>"""
M14_EQUIVALENT = """<a href="#energy-row">1</a><table>
<tr id="energy-row"><td><math><mrow><mi>E</mi><mo>=</mo><mi>m</mi><msup><mi>c</mi><mn>2</mn></msup></mrow></math></td></tr>
<tr id="mass-row"><td><math><mrow><mfrac><mi>E</mi><msup><mi>c</mi><mn>2</mn></msup></mfrac><mo>=</mo><mi>m</mi></mrow></math></td></tr>
</table><a href="#mass-row">2</a>"""
M10 = """<math><mrow><mo>⟨</mo><mi>ϕ</mi><mo>|</mo><mi>ψ</mi><mo>⟩</mo><mo>+</mo>
<mfrac><mrow><msup><mo>∂</mo><mn>2</mn></msup><mi>f</mi></mrow><msup><mrow><mo>∂</mo><mi>x</mi></mrow><mn>2</mn></msup></mfrac></mrow></math>"""
M10_XML = """<document xmlns="http://dlmf.nist.gov/LaTeXML"><Math><XMath><XMApp>
<XMTok meaning="plus"/>
<XMApp><XMTok meaning="inner-product"/><XMTok name="phi"/><XMTok name="psi"/></XMApp>
<XMApp><XMApp><XMTok meaning="partial-derivative"/><XMTok>x</XMTok><XMTok>2</XMTok></XMApp><XMTok>f</XMTok></XMApp>
</XMApp></XMath></Math></document>"""
M12 = """<math><mrow><mn>3.00</mn><mo>×</mo><msup><mn>10</mn><mn>8</mn></msup>
<mtext>m</mtext><mtext>/</mtext><mtext>s</mtext></mrow></math>"""


def long_expression():
    # Public synthetic M16 equation, rendered independently from the oracle.
    rows = []
    for start in (1, 5, 9, 13):
        terms = []
        for index in range(start, start + 4):
            terms.append(
                f"<mfrac><mrow><msub><mi>α</mi><mn>{index}</mn></msub>"
                f"<msup><mi>x</mi><mn>{index}</mn></msup><mo>+</mo>"
                f"<msub><mi>β</mi><mn>{index}</mn></msub>"
                f"<msub><mi>y</mi><mn>{index}</mn></msub></mrow>"
                f"<mrow><mn>1</mn><mo>+</mo><msub><mi>γ</mi><mn>{index}</mn></msub>"
                f"<msup><mi>z</mi><mn>{index + 1}</mn></msup></mrow></mfrac>"
            )
        rows.append("<mtr><mtd>" + "<mo>+</mo>".join(terms) + "</mtd></mtr>")
    rows.append(
        "<mtr><mtd><mo>+</mo><mfrac><mrow><mn>97</mn>"
        "<msub><mi>q</mi><mrow><mi>e</mi><mi>n</mi><mi>d</mi></mrow></msub></mrow>"
        "<mrow><mn>1</mn><mo>+</mo><msup><mi>z</mi><mn>2</mn></msup></mrow>"
        "</mfrac></mtd></mtr>"
    )
    return "<math><mtable>" + "".join(rows) + "</mtable></math>"


M16 = long_expression()


def inspect(tmp_path, case, html, xml="<document/>"):
    candidate = tmp_path / "candidate.html"
    intermediate = tmp_path / "candidate.xml"
    candidate.write_text("<html><body>" + html + "</body></html>", encoding="utf-8")
    intermediate.write_text(xml, encoding="utf-8")
    analyzer = oracles.analyze if case in {"M10", "M12"} else oracles.saved_structure
    return analyzer(case, candidate, intermediate)["checks"]


@pytest.mark.parametrize(
    "case,html,xml",
    [
        ("M03", M03, "<document/>"),
        ("M04", M04, "<document/>"),
        ("M04", M04_EQUIVALENT, "<document/>"),
        ("M06", M06, "<document/>"),
        ("M10", M10, M10_XML),
        ("M12", M12, "<document/>"),
        ("M14", M14, M14_XML),
        ("M14", M14_EQUIVALENT, M14_XML),
        ("M16", M16, "<document/>"),
    ],
)
def test_valid_structures_and_equivalent_grouping_pass(tmp_path, case, html, xml):
    assert all(inspect(tmp_path, case, html, xml).values())


@pytest.mark.parametrize(
    "case,positive,mutated,xml,failed_check",
    [
        (
            "M03",
            M03,
            M03.replace("msub", "msup"),
            "<document/>",
            "exponent_contains_subscript",
        ),
        ("M03", M03, M04, "<document/>", "exponent_contains_subscript"),
        ("M04", M04, M03, "<document/>", "scripts_share_base"),
        (
            "M04",
            M04,
            M04.replace("<mi>b</mi>", "<mi>c</mi>"),
            "<document/>",
            "scripts_share_base",
        ),
        (
            "M06",
            M06,
            M06.replace("<mn>0</mn>", "<mn>9</mn>"),
            "<document/>",
            "matrix_coordinates",
        ),
        (
            "M06",
            M06,
            M06.replace("<mo>−</mo>", "<mo>+</mo>"),
            "<document/>",
            "matrix_coordinates",
        ),
        (
            "M10",
            M10,
            M10.replace("<mn>2</mn>", "<mn>1</mn>"),
            M10_XML,
            "second_partial_derivative",
        ),
        ("M10", M10, M10.replace("<mi>ψ</mi>", "<mi>x</mi>"), M10_XML, "bra_ket"),
        (
            "M12",
            M12,
            M12.replace("<mn>10</mn><mn>8</mn>", "<mn>1</mn><mn>08</mn>"),
            "<document/>",
            "scientific_exponent",
        ),
        (
            "M12",
            M12,
            M12.replace("<mtext>/</mtext>", "<mtext>×</mtext>"),
            "<document/>",
            "per_second",
        ),
        (
            "M12",
            M12,
            M12.replace("<mtext>s</mtext>", "<mtext>kg</mtext>"),
            "<document/>",
            "per_second",
        ),
        (
            "M14",
            M14,
            M14.replace('href="#energy-row"', 'href="#mass-row"'),
            M14_XML,
            "exact_reference_targets",
        ),
        (
            "M14",
            M14,
            M14.replace('id="energy-row"', 'id="wrong-row"'),
            M14_XML,
            "aligned_row_order",
        ),
        (
            "M14",
            M14,
            M14.replace("<mi>E</mi>", "<mi>Q</mi>"),
            M14_XML,
            "aligned_row_order",
        ),
        (
            "M16",
            M16,
            M16.replace("<mn>97</mn>", "<mn>98</mn>"),
            "<document/>",
            "final_term",
        ),
        (
            "M16",
            M16,
            M16.replace(
                "<msub><mi>α</mi><mn>1</mn></msub>", "<msub><mi>α</mi><mn>2</mn></msub>"
            ),
            "<document/>",
            "term_order_and_scope",
        ),
    ],
)
def test_output_corruptions_fail_specific_oracles(
    tmp_path, case, positive, mutated, xml, failed_check
):
    assert all(inspect(tmp_path, case, positive, xml).values())
    assert not inspect(tmp_path, case, mutated, xml)[failed_check]


@pytest.mark.parametrize("operator", ["inner-product", "partial-derivative"])
def test_m10_visual_glyphs_do_not_replace_operator_semantics(tmp_path, operator):
    assert all(inspect(tmp_path, "M10", M10, M10_XML).values())
    checks = inspect(tmp_path, "M10", M10, M10_XML.replace(operator, "times"))
    assert checks["bra_ket"] and checks["second_partial_derivative"]
    assert not checks["operator_semantics"]


def test_m14_correct_links_do_not_replace_authored_label_identity(tmp_path):
    assert all(inspect(tmp_path, "M14", M14, M14_XML).values())
    checks = inspect(
        tmp_path,
        "M14",
        M14,
        M14_XML.replace('labels="LABEL:eq:energy"', 'labels="LABEL:eq:wrong"'),
    )
    assert checks["aligned_row_order"] and checks["exact_reference_targets"]
    assert not checks["label_identity"]
