"""Synthetic PDF and CLI-report contracts; these do not stand in for veraPDF."""

import hashlib
import json

import pikepdf
import pytest

from scripts import latex_profile_contract as contract

MATH = b'<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'


def make_pdf(path, *, math=MATH, fake=False, cycle=False, namespace_cycle=False):
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.Root.Lang = "en-AU"
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
    pdf.docinfo.Title = "Research specimen"
    pdf.docinfo.Author = "Test author"
    ns = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Namespace, NS=contract.MATHML_NS)
    )
    if namespace_cycle:
        ns.RoleMapNS = pikepdf.Dictionary(loop=pikepdf.Array([pikepdf.Name.mi, ns]))
    spec = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Filespec,
            F="MathML.xml",
            AFRelationship=pikepdf.Name.Supplement,
        )
    )
    if math is not None:
        stream = pdf.make_stream(math)
        stream.Subtype = pikepdf.Name("/application/mathml+xml")
        spec.EF = pikepdf.Dictionary(F=stream)
    formula = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem,
            S=pikepdf.Name.Formula,
            AF=pikepdf.Array([spec]),
        )
    )
    math_node = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructElem,
            S=pikepdf.Name.MathML if fake else pikepdf.Name.mi,
        )
    )
    if not fake:
        math_node.NS = ns
    figure = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.Figure, Alt="A plot"))
    th = pdf.make_indirect(
        pikepdf.Dictionary(
            S=pikepdf.Name.TH,
            A=pikepdf.Dictionary(O=pikepdf.Name.Table, Scope=pikepdf.Name.Column),
        )
    )
    td = pdf.make_indirect(pikepdf.Dictionary(S=pikepdf.Name.TD))
    table = pdf.make_indirect(
        pikepdf.Dictionary(S=pikepdf.Name.Table, K=pikepdf.Array([th, td]))
    )
    if cycle:
        formula.K = formula
    root = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.StructTreeRoot,
            K=pikepdf.Array([formula, math_node, figure, table]),
            Namespaces=pikepdf.Array([ns]),
        )
    )
    pdf.Root.StructTreeRoot = root
    pdf.save(path, min_version="2.0")
    pdf.close()


def test_real_stream_namespace_and_saved_byte_observations(tmp_path):
    path = tmp_path / "actual.pdf"
    make_pdf(path)
    observed = contract.inspect_pdf(path)
    assert observed["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert observed["size_bytes"] == path.stat().st_size
    assert observed["pdf_version"] == "2.0"
    assert observed["language"] == "en-AU"
    assert observed["metadata"]["document_info"] == {
        "title": "Research specimen",
        "author": "Test author",
    }
    assert observed["formula_nodes"][0]["role"] == "/Formula"
    math = observed["associated_files"][0]
    assert math["sha256"] == hashlib.sha256(MATH).hexdigest()
    assert math["xml"]["has_mathml_content"] is True
    assert math["xml"]["element_count"] == 2
    assert observed["mathml_structure_nodes"][0]["role"] == "/mi"
    assert observed["mathml_structure_nodes"][0]["namespace"] == contract.MATHML_NS
    assert observed["figures"][0]["alt"] == "A plot"
    assert [n["role"] for n in observed["tables"]] == ["/Table", "/TH", "/TD"]
    assert observed["tables"][1]["attributes"]["/Scope"] == "/Column"
    assert observed["limits"]["events"] == []
    assert contract.inspect_pdf(path) == observed
    json.dumps(observed)


@pytest.mark.parametrize(
    "math, expected_error",
    [
        (None, "missing_embedded_stream"),
        (b"", "invalid_flate_stream"),
        (b"<math/>", None),
        (b'<math xmlns="http://www.w3.org/1998/Math/MathML"/>', None),
        (
            b'<!DOCTYPE math [<!ENTITY x SYSTEM "file:///etc/passwd">]><math>&x;</math>',
            "Unsafe or malformed XML",
        ),
    ],
)
def test_names_and_empty_xml_are_not_mathml_content(tmp_path, math, expected_error):
    path = tmp_path / "fake.pdf"
    make_pdf(path, fake=True, math=math)
    observed = contract.inspect_pdf(path)
    assert observed["mathml_structure_nodes"] == []
    af = observed["associated_files"][0]
    if expected_error:
        assert af["error"] == expected_error
    else:
        assert af["xml"]["has_mathml_content"] is False


def test_structure_and_namespace_cycles_are_explicit(tmp_path):
    path = tmp_path / "cycles.pdf"
    make_pdf(path, cycle=True, namespace_cycle=True)
    observed = contract.inspect_pdf(path)
    assert {e["kind"] for e in observed["limits"]["events"]} == {
        "cycle",
        "structure_cycle",
    }
    assert observed["structure"]["node_count"] == 6
    json.dumps(observed)


def test_decompressed_stream_and_traversal_budgets(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    make_pdf(path, math=b"x" * (contract.MAX_XML_BYTES + 1))
    observed = contract.inspect_pdf(path)
    assert observed["associated_files"][0]["error"] == "decoded_stream_limit"
    assert observed["limits"]["truncated"] is True
    monkeypatch.setattr(contract, "MAX_NODES", 2)
    assert contract.inspect_pdf(path)["limits"]["truncated"] is True


def report(path, size=12, *, profile="ua1", failed=False):
    rule = (
        (
            '<rule specification="ISO_14289_1" clause="7.1" testNumber="1" '
            'status="failed"/>'
        )
        if failed
        else ""
    )
    return f"""<report><buildInformation>
    <releaseDetails id="core" version="1.30.2"/>
    <releaseDetails id="validation-model" version="1.30.2"/>
    </buildInformation><jobs><job><item size="{size}"><name>{path}</name></item>
    <validationReport jobEndStatus="normal" profileName="PDF/UA-{profile[-1]} validation profile"
      isCompliant="{str(not failed).lower()}">
    <details passedRules="100" failedRules="{int(failed)}" passedChecks="300"
      failedChecks="{int(failed)}">{rule}</details></validationReport></job></jobs>
    <batchSummary totalJobs="1" failedToParse="0" encrypted="0" outOfMemory="0" veraExceptions="0">
    <validationReports compliant="{int(not failed)}" nonCompliant="{int(failed)}" failedJobs="0">1</validationReports>
    </batchSummary></report>""".encode()


@pytest.mark.parametrize("profile", ["ua1", "ua2"])
@pytest.mark.parametrize("failed", [False, True])
def test_exact_validator_profile_and_failure_identities(tmp_path, profile, failed):
    path = tmp_path / "candidate.pdf"
    raw = report(path, profile=profile, failed=failed)
    observed = contract.parse_verapdf_report(raw, profile, path, 12)
    assert observed["status"] == ("failed" if failed else "passed")
    assert observed["validator_versions"]["core"] == "1.30.2"
    assert observed["counts"]["failedRules"] == int(failed)
    assert len(observed["failed_rules"]) == int(failed)
    if failed:
        assert observed["failed_rules"][0] == {
            "specification": "ISO_14289_1",
            "clause": "7.1",
            "testNumber": "1",
        }


@pytest.mark.parametrize(
    "old,new",
    [
        (b"PDF/UA-1", b"PDF/UA-2"),
        (b'size="12"', b'size="13"'),
        (b"candidate.pdf", b"other.pdf"),
        (b'version="1.30.2"', b'version=""'),
        (b'id="core"', b'id="something"'),
        (b'isCompliant="true"', b'isCompliant="yes"'),
        (b'isCompliant="true"', b'isCompliant="false"'),
        (b'jobEndStatus="normal"', b'jobEndStatus="failed"'),
        (b'jobEndStatus="normal"', b""),
        (b'failedRules="0"', b'failedRules="1"'),
        (b'failedChecks="0"', b'failedChecks="1"'),
        (b'passedChecks="300"', b'passedChecks="0"'),
        (b'passedChecks="300"', b'passedChecks="-1"'),
        (b'totalJobs="1"', b'totalJobs="2"'),
        (b'failedToParse="0"', b'failedToParse="1"'),
        (b'encrypted="0"', b'encrypted="1"'),
        (b'outOfMemory="0"', b'outOfMemory="1"'),
        (b'veraExceptions="0"', b'veraExceptions="1"'),
        (b'compliant="1"', b'compliant="0"'),
        (b'failedJobs="0"', b'failedJobs="1"'),
        (b">1</validationReports>", b">2</validationReports>"),
        (b"</jobs>", b"<job/></jobs>"),
        (b"</job>", b"<error>failed</error></job>"),
        (b"<report>", b"<!DOCTYPE report><report>"),
    ],
)
def test_mutated_validator_receipts_are_rejected(tmp_path, old, new):
    path = tmp_path / "candidate.pdf"
    with pytest.raises(ValueError):
        contract.parse_verapdf_report(report(path).replace(old, new), "ua1", path, 12)


@pytest.mark.parametrize("raw", [b"", b"garbage", b"<report>", b"<report/>"])
def test_empty_malformed_missing_report_rejected(tmp_path, raw):
    with pytest.raises(ValueError):
        contract.parse_verapdf_report(raw, "ua1", tmp_path / "candidate.pdf", 12)


def test_exact_tagged_profile_name_is_accepted(tmp_path):
    path = tmp_path / "candidate.pdf"
    raw = report(path, profile="ua2").replace(
        b"PDF/UA-2 validation", b"PDF/UA-2 + Tagged PDF validation"
    )
    assert contract.parse_verapdf_report(raw, "ua2", path, 12)["status"] == "passed"
    with pytest.raises(ValueError):
        contract.parse_verapdf_report(
            raw.replace(b" + Tagged PDF", b" + Unknown"), "ua2", path, 12
        )


def test_links_preserve_destinations_and_object_identity(tmp_path):
    path = tmp_path / "links.pdf"
    make_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        annotation = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name.Link,
                Rect=pikepdf.Array([0, 0, 10, 10]),
                Dest=pikepdf.Array([page.obj, pikepdf.Name.Fit]),
                StructParent=2,
            )
        )
        page.Annots = pikepdf.Array([annotation])
        pdf.save(path)
    observed = contract.inspect_pdf(path)
    link = observed["links"][0]
    assert link["destination"] == [link["page_id"], "/Fit"]
    assert link["struct_parent"] == 2


def test_xmp_values_are_observations_and_unsafe_xmp_is_rejected(tmp_path):
    path = tmp_path / "xmp.pdf"
    make_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        metadata = pdf.make_stream(b"""<x:xmpmeta xmlns:x="adobe:ns:meta/"
          xmlns:dc="http://purl.org/dc/elements/1.1/"
          xmlns:ua="http://www.aiim.org/pdfua/ns/id/">
          <dc:title>XMP title</dc:title><dc:creator>XMP author</dc:creator>
          <ua:part>2</ua:part></x:xmpmeta>""")
        metadata.Type = pikepdf.Name.Metadata
        metadata.Subtype = pikepdf.Name.XML
        pdf.Root.Metadata = metadata
        pdf.save(path, fix_metadata_version=False)
    observed = contract.inspect_pdf(path)
    assert observed["metadata"]["xmp"]["values"] == {
        "title": ["XMP title"],
        "creator": ["XMP author"],
    }
    assert observed["metadata"]["xmp"]["declared_pdfua"]["part"] == ["2"]
    assert "status" not in observed
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.Metadata = pdf.make_stream(b"<!DOCTYPE x><x/>")
        pdf.save(path, fix_metadata_version=False)
    assert (
        contract.inspect_pdf(path)["metadata"]["xmp"]["error"]
        == "Unsafe or malformed XML"
    )


def test_failed_rule_identity_and_count_must_both_be_present(tmp_path):
    path = tmp_path / "candidate.pdf"
    raw = report(path, failed=True)
    for changed in (
        raw.replace(b'clause="7.1"', b""),
        raw.replace(b'failedRules="1"', b'failedRules="2"'),
        raw.replace(b'status="failed"', b'status="passed"'),
    ):
        with pytest.raises(ValueError):
            contract.parse_verapdf_report(changed, "ua1", path, 12)


def test_pdf_and_report_byte_limits(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    make_pdf(path)
    monkeypatch.setattr(contract, "MAX_PDF_BYTES", 16)
    with pytest.raises(ValueError, match="PDF exceeds"):
        contract.inspect_pdf(path)
    monkeypatch.setattr(contract, "MAX_REPORT_BYTES", 16)
    with pytest.raises(ValueError, match="byte limit"):
        contract.parse_verapdf_report(report(path), "ua1", path, 12)
