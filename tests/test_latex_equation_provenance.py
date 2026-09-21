"""Content identity controls; a matching count/annotation is not semantic proof."""

import hashlib
from html import escape
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from src.education.latex_equation_provenance import (
    MAX_RECORDS,
    EquationRepresentationTrace,
    observe_representation,
    public_equation_trace,
    source_provenance,
)

FIXTURES = Path(__file__).parent / "fixtures" / "latex_validation"


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def observe(tmp_path, source, content, kind="html"):
    path = tmp_path / ("candidate." + kind)
    path.write_text(content)
    return observe_representation(source, path, kind)


def math(tex, body="<mi>x</mi>"):
    return (
        "<math><semantics>"
        + body
        + '<annotation encoding="application/x-tex">'
        + escape(tex)
        + "</annotation></semantics></math>"
    )


def test_original_utf8_spans_comments_and_duplicates():
    source = "é % $fake$\n" + r"\verb|$ignored$| $x^{a_b}$ $x^{a_b}$"
    result = source_provenance(source)
    assert result == source_provenance(source)
    assert len(result.expressions) == 2
    first, second = result.expressions
    assert first.expression_id != second.expression_id
    assert first.content_sha256 == second.content_sha256 == sha(r"x^{a_b}")
    data = source.encode()
    assert (
        data[first.source_span.start_byte : first.source_span.end_byte] == rb"$x^{a_b}$"
    )
    assert (
        data[first.content_span.start_byte : first.content_span.end_byte] == rb"x^{a_b}"
    )
    assert first.source_span.start_byte == len(
        source[: source.index(r"$x^{a_b}$")].encode()
    )


def test_align_rows_labels_and_references_are_separate_identities():
    source = (FIXTURES / "M14.tex").read_text()
    result = source_provenance(source)
    assert len(result.expressions) == 1
    expression = result.expressions[0]
    assert len(expression.rows) == 2
    labels = [r for r in result.relationships if r.kind == "label"]
    refs = [r for r in result.relationships if r.kind == "reference"]
    assert len(labels) == len(refs) == 2
    assert {r.row_id for r in labels} == {r.row_id for r in expression.rows}
    assert all(r.expression_id == expression.expression_id for r in labels)
    assert [r.target_label_ids for r in refs] == [[r.relationship_id] for r in labels]
    assert len({r.relationship_id for r in result.relationships}) == 4


def test_nested_matrix_rows_do_not_become_expression_rows():
    result = source_provenance((FIXTURES / "M06.tex").read_text())
    assert len(result.expressions) == 1
    assert result.expressions[0].rows == []
    aligned = source_provenance((FIXTURES / "M16.tex").read_text())
    assert len(aligned.expressions) == 1
    assert len(aligned.expressions[0].rows) == 5


def test_source_hashes_detect_script_scope_and_final_term_corruption():
    inside = source_provenance((FIXTURES / "M03.tex").read_text()).expressions[0]
    outside = source_provenance((FIXTURES / "M04.tex").read_text()).expressions[0]
    assert inside.content_sha256 != outside.content_sha256
    original = (FIXTURES / "M16.tex").read_text()
    edited = original.replace(r"97q_{\mathrm{end}}", r"7q_{\mathrm{end}}")
    before, after = [source_provenance(s).expressions[0] for s in (original, edited)]
    assert before.content_sha256 != after.content_sha256
    assert before.rows[-1].content_sha256 != after.rows[-1].content_sha256
    assert [r.content_sha256 for r in before.rows[:-1]] == [
        r.content_sha256 for r in after.rows[:-1]
    ]


def test_bounded_source_and_unterminated_or_macro_source(monkeypatch):
    monkeypatch.setattr("src.education.latex_equation_provenance.MAX_SOURCE_BYTES", 4)
    result = source_provenance("$1234$")
    assert result.issues == ["source_limit"] and not result.expressions
    monkeypatch.setattr(
        "src.education.latex_equation_provenance.MAX_SOURCE_BYTES", 100000
    )
    assert "unterminated_math" in source_provenance(r"\[x").issues
    assert "unsupported_source" in source_provenance(r"\newcommand{\x}{1} $\x$").issues
    result = source_provenance("$x$ " * (MAX_RECORDS + 1))
    assert len(result.expressions) == MAX_RECORDS and "record_limit" in result.issues


def test_escaped_dollars_labels_and_comments():
    result = source_provenance(
        r"\$price \\label{fake} % $ignored$" + "\n" + r"$x\%y$ \ref{real}"
    )
    assert len(result.expressions) == 1
    assert len(result.relationships) == 1
    assert result.relationships[0].target_sha256 == sha("real")


def test_math_inside_text_group_does_not_split_outer_expression():
    result = source_provenance(r"$x+\text{inside $y$}$ $z$")
    assert len(result.expressions) == 2
    assert result.expressions[0].content_sha256 == sha(r"x+\text{inside $y$}")


def test_unclosed_verbatim_is_not_scanned_as_authored_math():
    result = source_provenance(r"\begin{verbatim} $fake$ " * 500)
    assert result.expressions == []
    assert result.issues == ["unsupported_source"]


def test_literal_citation_lists_bind_bibliography_targets_without_raw_text():
    source = r"See \cite[compare][p. 2]{private:key, second} and \citep{second}; \citet{private:key}. \bibitem[First]{private:key} Secret author. \bibitem{second} Another author."
    result = source_provenance(source)
    citations = [r for r in result.relationships if r.kind == "citation"]
    bibliography = [r for r in result.relationships if r.kind == "bibliography"]
    assert len(citations) == 4 and len(bibliography) == 2
    assert all(r.target_status == "authored_target_match" for r in citations)
    assert all(len(r.target_bibliography_ids) == 1 for r in citations)
    assert citations[0].target_sha256 == sha("private:key")
    assert citations[0].command_sha256 == citations[1].command_sha256
    assert citations[0].relationship_id != citations[1].relationship_id
    data = source.encode()
    assert (
        data[citations[0].source_span.start_byte : citations[0].source_span.end_byte]
        == b"private:key"
    )
    public = result.model_dump_json()
    assert "Secret author" not in public and "private:key" not in public


def test_missing_external_and_duplicate_bibliography_targets_remain_uncertain():
    source = r"\cite{missing,duplicate} \bibliography{external-private-file} \bibitem{duplicate} First. \bibitem{duplicate} Second."
    result = source_provenance(source)
    missing, duplicate = [r for r in result.relationships if r.kind == "citation"]
    assert (
        missing.target_status == "unresolved" and missing.target_bibliography_ids == []
    )
    assert (
        duplicate.target_status == "ambiguous"
        and len(duplicate.target_bibliography_ids) == 2
    )
    assert "unsupported_source" in result.issues
    tampered = result.model_copy(
        update={
            "relationships": [
                missing.model_copy(update={"target_status": "authored_target_match"}),
                *result.relationships[1:],
            ]
        }
    )
    with pytest.raises(ValidationError):
        type(result).model_validate(tampered)


def test_citation_targets_are_literal_and_count_bounded():
    dynamic = source_provenance(r"\cite{\privatekey} \addbibresource{private.bib}")
    assert not dynamic.relationships and "unsupported_source" in dynamic.issues
    bounded = source_provenance(
        r"\cite{" + ",".join(f"key{i}" for i in range(40)) + "}"
    )
    assert len(bounded.relationships) == 32 and "record_limit" in bounded.issues
    assert all(r.target_status == "unresolved" for r in bounded.relationships)


def test_exact_annotation_mapping_and_public_privacy(tmp_path):
    source = r"Private Alice /Users/private $x^a_b$"
    result = observe(tmp_path, source, math("x^a_b"))
    assert result.nodes[0].association == "exact_annotation"
    assert not result.unmapped_source_ids
    assert result.nodes[0].source_ids == [result.source.expressions[0].expression_id]
    assert result.fidelity == result.nodes[0].fidelity == "not_assessed"
    public = str(public_equation_trace(result))
    for secret in ("Alice", "/Users/private", "x^a_b", str(tmp_path)):
        assert secret not in public


def test_duplicate_content_is_ambiguous_without_location_evidence(tmp_path):
    result = observe(tmp_path, "$x$ $x$", math("x") + math("x"))
    assert all(n.association == "ambiguous" for n in result.nodes)
    assert len(result.unmapped_source_ids) == 2
    chosen = result.nodes[0].source_ids[0]
    tampered = result.nodes[0].model_copy(
        update={"association": "exact_annotation", "source_ids": [chosen]}
    )
    with pytest.raises(ValidationError):
        public_equation_trace(result.model_copy(update={"nodes": [tampered]}))


def test_counts_do_not_associate_missing_or_wrong_annotations(tmp_path):
    result = observe(tmp_path, "$x$ $y$", "<math><mi>x</mi></math>" + math("z"))
    assert len(result.nodes) == len(result.source.expressions) == 2
    assert all(n.association == "unmapped" for n in result.nodes)
    assert len(result.unmapped_source_ids) == 2


def test_conflicting_annotations_cannot_claim_unique_source(tmp_path):
    result = observe(
        tmp_path,
        "$x$",
        '<math alttext="y"><annotation encoding="application/x-tex">x</annotation></math>',
    )
    assert result.nodes[0].association == "unmapped"


def test_rows_can_map_without_equating_align_to_node_count(tmp_path):
    source = r"\begin{align}x&=1\\y&=2\end{align}"
    result = observe(tmp_path, source, math("x&=1") + math("y&=2"))
    assert len(result.source.expressions) == 1 and len(result.nodes) == 2
    assert all(n.association == "exact_annotation" for n in result.nodes)
    assert result.unmapped_source_ids == [result.source.expressions[0].expression_id]


def test_intermediate_xmath_and_operator_scope_are_content_hashed(tmp_path):
    source = r"\[\pdv[2]{f}{x}\]"
    xml = '<document><Math tex="\\pdv[2]{f}{x}"><XMath><XMApp><XMTok meaning="derivative" role="OPERATOR"/><XMTok>f</XMTok><XMTok>x</XMTok></XMApp></XMath></Math></document>'
    before = observe(tmp_path, source, xml, "latexml")
    after = observe(
        tmp_path, source, xml.replace("derivative", "partial-diff"), "latexml"
    )
    assert (
        before.nodes[0].association == after.nodes[0].association == "exact_annotation"
    )
    assert (
        before.nodes[0].intermediate_semantics_sha256
        != after.nodes[0].intermediate_semantics_sha256
    )
    assert before.nodes[0].operator_scope_sha256 != after.nodes[0].operator_scope_sha256
    assert after.fidelity == "not_assessed"


@pytest.mark.parametrize(
    "body",
    [
        "<msup><mi>x</mi><msub><mi>a</mi><mi>b</mi></msub></msup>",
        "<msubsup><mi>x</mi><mi>b</mi><mi>a</mi></msubsup>",
    ],
)
def test_corrupt_presentation_with_intact_annotation_never_certifies(tmp_path, body):
    source = r"$x^{a_b}$"
    result = observe(tmp_path, source, math(r"x^{a_b}", body))
    baseline = observe(tmp_path, source, math(r"x^{a_b}", "<mi>corrupted</mi>"))
    assert (
        result.nodes[0].association
        == baseline.nodes[0].association
        == "exact_annotation"
    )
    assert result.nodes[0].structure_sha256 != baseline.nodes[0].structure_sha256
    assert result.nodes[0].script_attachment_sha256
    assert result.fidelity == baseline.fidelity == "not_assessed"


def test_pdf_and_docx_nodes_stay_unmapped(tmp_path):
    source = "$x$"
    pdf = observe(tmp_path, source, "%PDF-1.7", "pdf")
    assert pdf.status == "not_assessed" and pdf.unmapped_source_ids
    path = tmp_path / "candidate.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:document>',
        )
    docx = observe_representation(source, path, "docx")
    assert len(docx.nodes) == 1 and docx.nodes[0].association == "unmapped"
    assert docx.unmapped_source_ids and docx.fidelity == "not_assessed"


def test_malformed_missing_oversized_and_entity_inputs(tmp_path, monkeypatch):
    missing = observe_representation("$x$", tmp_path / "absent", "html")
    assert missing.issues == ["candidate_missing"]
    malformed = observe(tmp_path, "$x$", "<Math>", "latexml")
    assert malformed.issues == ["candidate_malformed"]
    entity = observe(
        tmp_path,
        "$x$",
        '<!DOCTYPE document [<!ENTITY secret SYSTEM "file:///etc/passwd">]><document><Math>&secret;</Math></document>',
        "latexml",
    )
    assert entity.issues == ["candidate_malformed"] and not entity.nodes
    monkeypatch.setattr(
        "src.education.latex_equation_provenance.MAX_CANDIDATE_BYTES", 3
    )
    limited = observe(tmp_path, "$x$", math("x"))
    assert limited.issues == ["candidate_limit"] and limited.candidate_sha256 is None


def test_latexml_inert_external_doctype_keeps_xmath_without_fetching(
    tmp_path, monkeypatch
):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("XML observation must not fetch external declarations")

    monkeypatch.setattr(socket, "socket", no_network)
    xml = '<?xml version="1.0"?><!DOCTYPE document PUBLIC "-//NIST LaTeXML//DTD LaTeXML article//EN" "http://dlmf.nist.gov/LaTeXML/LaTeXML.dtd"><document><Math tex="x"><XMath><XMTok>x</XMTok></XMath></Math></document>'
    result = observe(tmp_path, "$x$", xml, "latexml")
    assert result.status == "observed" and result.issues == []
    assert result.nodes[0].association == "exact_annotation"
    assert result.nodes[0].intermediate_semantics_sha256


@pytest.mark.parametrize(
    "declaration",
    [
        '<!ENTITY secret "private value">',
        '<!ENTITY secret SYSTEM "file:///etc/passwd">',
        '<!ENTITY % external SYSTEM "http://example.invalid/external.dtd"> %external;',
    ],
)
def test_latexml_doctype_never_enables_entities(tmp_path, declaration):
    result = observe(
        tmp_path,
        "$x$",
        "<!DOCTYPE document ["
        + declaration
        + ']><document><Math tex="x"><XMath/></Math></document>',
        "latexml",
    )
    assert result.status == "not_assessed" and result.issues == ["candidate_malformed"]
    assert result.nodes == []


def test_nested_html_math_rejected_before_subtree_serialization(tmp_path, monkeypatch):
    from bs4.element import Tag

    def no_serialization(*args, **kwargs):
        raise AssertionError("Nested math must be rejected before serialization")

    monkeypatch.setattr(Tag, "__str__", no_serialization)
    result = observe(tmp_path, "$x$", "<math>" * 120 + "x" * 10000 + "</math>" * 120)
    assert result.issues == ["candidate_malformed"] and result.nodes == []


def test_html_tree_limit_is_checked_before_serialization(tmp_path, monkeypatch):
    from bs4.element import Tag

    monkeypatch.setattr("src.education.latex_equation_provenance.MAX_TREE_NODES", 3)

    def no_serialization(*args, **kwargs):
        raise AssertionError("Excessive tree must be rejected before serialization")

    monkeypatch.setattr(Tag, "__str__", no_serialization)
    result = observe(tmp_path, "$x$", "<math>" + "<mi>x</mi>" * 4 + "</math>")
    assert result.issues == ["tree_limit"] and result.nodes == []


def test_public_boundary_rejects_unknown_data_fidelity_and_forged_bindings(tmp_path):
    result = observe(tmp_path, "$x$", math("x"))
    for update in (
        {"fidelity": "passed"},
        {"private_path": "/private"},
        {"source_sha256": "0" * 64},
        {"unmapped_source_ids": ["0" * 64]},
    ):
        with pytest.raises(ValidationError):
            public_equation_trace({**result.model_dump(), **update})
    bad_node = result.nodes[0].model_copy(update={"annotation_sha256": ["0" * 64]})
    with pytest.raises(ValidationError):
        public_equation_trace(result.model_copy(update={"nodes": [bad_node]}))
    with pytest.raises(ValidationError):
        EquationRepresentationTrace.model_validate(
            result.model_copy(update={"fidelity": "passed"})
        )
