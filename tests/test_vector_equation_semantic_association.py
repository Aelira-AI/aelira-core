"""Atomic semantic ownership tests for exact PDF vector equation clusters."""

import hashlib
import shutil
from dataclasses import replace

import fitz
import pikepdf
import pytest
from pikepdf import Array, Name, Operator

from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.remediation.vector_equation_semantics import (
    VectorEquationSemanticRejected,
    associate_vector_equation_formula,
    plan_vector_equation_semantics,
    remediate_vector_equation_pdf,
    verify_vector_equation_formula,
    verify_vector_equation_formula_association,
)
from src.education.vector_equation_semantics import (
    VectorEquationSemanticContractV1,
)


def _write_vector_pdf(path, *, decoration=False) -> None:
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    page.insert_text((36, 28), "Equation 1", fontsize=10)
    equation = page.new_shape()
    for start, end in (
        ((50, 80), (62, 60)),
        ((50, 60), (62, 80)),
        ((75, 66), (95, 66)),
        ((75, 73), (95, 73)),
        ((108, 60), (118, 70)),
        ((128, 60), (118, 70)),
        ((118, 70), (118, 82)),
    ):
        equation.draw_line(start, end)
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()
    if decoration:
        art = page.new_shape()
        art.draw_rect(fitz.Rect(170, 90, 220, 140))
        art.finish(color=(0, 0, 0), width=1)
        art.commit()
    document.save(path)
    document.close()


class _Recognizer:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, raster):
        from src.education.remediation.equation_recognizer import EquationRecognition

        self.calls += 1
        assert raster.mime_type == "image/jpeg"
        assert raster.jpeg_bytes.startswith(b"\xff\xd8")
        return EquationRecognition(
            classification="printed_equation",
            latex="x = y",
            provider="fixture-provider",
            model="fixture-model",
        )


class _Verifier:
    def verify(self, raster, latex):
        from src.education.remediation.equation_verifier import (
            EquationVerificationEvidence,
        )

        assert latex == "x = y"
        return EquationVerificationEvidence(
            passed=True,
            source_sha256=raster.normalized_sha256,
            rendered_sha256="1" * 64,
            mathml_sha256=hashlib.sha256(
                b"<math><mi>x</mi><mo>=</mo><mi>y</mi></math>"
            ).hexdigest(),
            renderer_version="fixture-renderer-v1",
            comparator_version="fixture-comparator-v1",
            font_sha256="2" * 64,
            threshold_version="fixture-threshold-v1",
            ink_iou=1.0,
            pixel_similarity=1.0,
            required_ink_iou=0.9,
            required_pixel_similarity=0.98,
        )

    def canonicalize_mathml(self, _mathml):
        return "<math><mi>x</mi><mo>=</mo><mi>y</mi></math>"

    converter = staticmethod(
        lambda _latex: "<math><mi>x</mi><mo>=</mo><mi>y</mi></math>"
    )


def test_revalidation_raster_binding_and_frozen_contract_are_fail_closed(tmp_path):
    source = tmp_path / "source.pdf"
    changed = tmp_path / "changed.pdf"
    _write_vector_pdf(source)
    _write_vector_pdf(changed, decoration=True)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    recognizer = _Recognizer()

    owner = plan_vector_equation_semantics(source, cluster, recognizer, _Verifier())

    assert recognizer.calls == 1
    assert owner.cluster_sha256 == cluster.cluster_sha256
    assert (
        owner.semantic_output.mathml_sha256 == owner.verification_evidence.mathml_sha256
    )
    assert owner.review_required is True
    assert owner.publication_authorized is False
    with pytest.raises(Exception):
        owner.provider = "changed"
    with pytest.raises(Exception):
        VectorEquationSemanticContractV1.model_validate(
            {**owner.model_dump(mode="json"), "unknown": True}
        )

    stale_calls = _Recognizer()
    with pytest.raises(ValueError, match="vector_equation_source_changed"):
        plan_vector_equation_semantics(changed, cluster, stale_calls, _Verifier())
    assert stale_calls.calls == 0


def test_verifier_disagreement_rejects_before_association(tmp_path):
    source = tmp_path / "source.pdf"
    _write_vector_pdf(source)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]

    class RejectingVerifier(_Verifier):
        def verify(self, raster, latex):
            return replace(
                super().verify(raster, latex),
                passed=False,
                ink_iou=0.0,
            )

    with pytest.raises(ValueError, match="vector_equation_verification_failed"):
        plan_vector_equation_semantics(
            source, cluster, _Recognizer(), RejectingVerifier()
        )


def test_original_vector_operators_become_one_saved_formula_without_pixel_delta(
    tmp_path,
):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_vector_pdf(source, decoration=True)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    with fitz.open(source) as document:
        before = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples

    contract = remediate_vector_equation_pdf(
        source, output, cluster, _Recognizer(), _Verifier()
    )

    assert output.exists()
    assert contract.cluster == cluster
    assert contract.review_required is True
    assert contract.publication_authorized is False
    assert contract.authorizes_artifact_availability() is False
    assert len(contract.saved_evidence.marked_spans) == len(cluster.operator_spans)
    assert contract.saved_evidence.resource_identities == cluster.resources
    with fitz.open(output) as document:
        after = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples
        assert after == before
        assert document[0].get_text("text").strip() == "Equation 1"
    with pikepdf.open(output) as pdf:
        page = pdf.pages[0]
        instructions = list(pikepdf.parse_content_stream(page))
        formulas = [
            instruction
            for instruction in instructions
            if str(instruction.operator) == "BDC"
            and str(instruction.operands[0]) == "/Formula"
        ]
        assert len(formulas) == len(cluster.operator_spans)
        root = pdf.Root[Name.StructTreeRoot]
        nums = root[Name.ParentTree][Name.Nums]
        page_array = next(
            nums[index + 1]
            for index in range(0, len(nums), 2)
            if int(nums[index]) == contract.saved_evidence.struct_parent
        )
        formula = page_array[contract.saved_evidence.marked_spans[0].mcid]
        assert str(formula[Name.S]) == "/Formula"
        assert tuple(
            float(value) for value in formula[Name.A][Name("/BBox")]
        ) == pytest.approx(cluster.pdf_bbox)
        assert isinstance(formula[Name.K], Array)
        assert len(formula[Name.K]) == len(cluster.operator_spans)

    roundtrip = type(contract).model_validate_json(contract.model_dump_json())
    assert roundtrip == contract
    forged = contract.model_dump(mode="json")
    forged["saved_evidence"]["formula_bbox"][0] += 1.0
    with pytest.raises(Exception):
        type(contract).model_validate(forged)


def test_retry_and_late_failure_preserve_prior_destination(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_vector_pdf(source)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    remediate_vector_equation_pdf(source, output, cluster, _Recognizer(), _Verifier())
    approved = output.read_bytes()

    with pytest.raises(VectorEquationSemanticRejected, match="source_changed"):
        remediate_vector_equation_pdf(
            output, output, cluster, _Recognizer(), _Verifier()
        )
    assert output.read_bytes() == approved

    from src.education.remediation import vector_equation_semantics as module

    monkeypatch.setattr(
        module,
        "verify_vector_equation_formula",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            VectorEquationSemanticRejected("late_saved_failure")
        ),
    )
    with pytest.raises(VectorEquationSemanticRejected, match="late_saved_failure"):
        remediate_vector_equation_pdf(
            source, output, cluster, _Recognizer(), _Verifier()
        )
    assert output.read_bytes() == approved
    assert list(tmp_path.glob(".output.pdf.vector-*.pdf")) == []


def _associated_candidate(tmp_path):
    source = tmp_path / "source.pdf"
    associated = tmp_path / "associated.pdf"
    _write_vector_pdf(source, decoration=True)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    plan = plan_vector_equation_semantics(source, cluster, _Recognizer(), _Verifier())
    with fitz.open(source) as fitz_doc, pikepdf.open(source) as pdf:
        association = associate_vector_equation_formula(pdf, fitz_doc, cluster, plan)
        pdf.save(associated)
    evidence = verify_vector_equation_formula(associated, cluster, plan, association)
    return associated, cluster, plan, association, evidence


def _page_stream(page, index):
    contents = page.obj[Name.Contents]
    streams = list(contents) if isinstance(contents, Array) else [contents]
    return streams[index]


def _saved_formula(pdf, evidence):
    root = pdf.Root[Name.StructTreeRoot]
    nums = root[Name.ParentTree][Name.Nums]
    page_array = next(
        nums[index + 1]
        for index in range(0, len(nums), 2)
        if int(nums[index]) == evidence.struct_parent
    )
    return page_array[evidence.marked_spans[0].mcid], page_array


@pytest.mark.parametrize(
    "mutation",
    [
        "operator",
        "wrapper_mcid",
        "formula_bbox",
        "parent_tree",
        "mathml",
        "alt_text",
        "output_pixels",
    ],
)
def test_saved_vector_tampering_rejects_complete_proof(tmp_path, mutation):
    associated, cluster, plan, association, evidence = _associated_candidate(tmp_path)
    tampered = tmp_path / f"tampered-{mutation}.pdf"
    shutil.copyfile(associated, tampered)

    with pikepdf.open(tampered, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        formula, page_array = _saved_formula(pdf, evidence)
        stream = _page_stream(page, evidence.marked_spans[0].content_stream_index)
        instructions = list(pikepdf.parse_content_stream(stream))
        wrapper_index = next(
            index
            for index, instruction in enumerate(instructions)
            if str(instruction.operator) == "BDC"
            and str(instruction.operands[0]) == "/Formula"
        )
        if mutation == "operator":
            target = instructions[wrapper_index + 1]
            operands = list(target.operands)
            operands[0] = float(operands[0]) + 1.0
            instructions[wrapper_index + 1] = pikepdf.ContentStreamInstruction(
                operands, target.operator
            )
            stream.write(pikepdf.unparse_content_stream(instructions))
        elif mutation == "wrapper_mcid":
            properties = instructions[wrapper_index].operands[1]
            properties[Name.MCID] = evidence.marked_spans[0].mcid + 100
            stream.write(pikepdf.unparse_content_stream(instructions))
        elif mutation == "formula_bbox":
            formula[Name.A][Name("/BBox")][0] = (
                float(formula[Name.A][Name("/BBox")][0]) + 1.0
            )
        elif mutation == "parent_tree":
            page_array[evidence.marked_spans[0].mcid] = None
        elif mutation == "mathml":
            formula[Name("/AF")][0][Name("/EF")][Name.F].write(
                b"<math><mn>999</mn></math>"
            )
        elif mutation == "alt_text":
            formula[Name.Alt] = "different equation"
        elif mutation == "output_pixels":
            instructions.extend(
                [
                    pikepdf.ContentStreamInstruction([], Operator("q")),
                    pikepdf.ContentStreamInstruction([1, 0, 0], Operator("rg")),
                    pikepdf.ContentStreamInstruction(
                        [180, 110, 20, 20], Operator("re")
                    ),
                    pikepdf.ContentStreamInstruction([], Operator("f")),
                    pikepdf.ContentStreamInstruction([], Operator("Q")),
                ]
            )
            stream.write(pikepdf.unparse_content_stream(instructions))
        pdf.save(tampered)

    assert not verify_vector_equation_formula_association(
        tampered, cluster, plan, association
    )


def _write_shared_form_pdf(path) -> None:
    source = fitz.open()
    source_page = source.new_page(width=100, height=60)
    equation = source_page.new_shape()
    for start, end in (
        ((10, 36), (22, 16)),
        ((10, 16), (22, 36)),
        ((35, 22), (55, 22)),
        ((35, 29), (55, 29)),
        ((68, 16), (78, 26)),
        ((88, 16), (78, 26)),
    ):
        equation.draw_line(start, end)
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()

    target = fitz.open()
    page = target.new_page(width=300, height=200)
    page.insert_text((24, 24), "Equation 1", fontsize=10)
    page.insert_text((164, 24), "Equation 2", fontsize=10)
    page.show_pdf_page(fitz.Rect(20, 40, 120, 100), source, 0)
    page.show_pdf_page(fitz.Rect(160, 40, 260, 100), source, 0)
    target.save(path)
    target.close()
    source.close()


def test_shared_transformed_form_resource_and_other_occurrence_are_unchanged(tmp_path):
    source = tmp_path / "shared.pdf"
    output = tmp_path / "shared-output.pdf"
    _write_shared_form_pdf(source)
    clusters = VectorEquationClusterDetector().find_clusters(source)
    assert len(clusters) == 2
    with fitz.open(source) as document:
        before = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples

    contract = remediate_vector_equation_pdf(
        source, output, clusters[0], _Recognizer(), _Verifier()
    )

    assert contract.saved_evidence.resource_identities == clusters[0].resources
    with fitz.open(output) as document:
        after = document[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples
        assert after == before
    with pikepdf.open(output) as pdf:
        page = pdf.pages[0]
        instructions = list(pikepdf.parse_content_stream(page))
        assert sum(str(item.operator) == "Do" for item in instructions) == 2


def test_shared_form_resource_tampering_rejects_saved_proof(tmp_path):
    source = tmp_path / "shared.pdf"
    associated = tmp_path / "shared-associated.pdf"
    tampered = tmp_path / "shared-tampered.pdf"
    _write_shared_form_pdf(source)
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    plan = plan_vector_equation_semantics(source, cluster, _Recognizer(), _Verifier())
    with fitz.open(source) as fitz_doc, pikepdf.open(source) as pdf:
        association = associate_vector_equation_formula(pdf, fitz_doc, cluster, plan)
        pdf.save(associated)
    assert verify_vector_equation_formula_association(
        associated, cluster, plan, association
    )
    shutil.copyfile(associated, tampered)

    with pikepdf.open(tampered, allow_overwriting_input=True) as pdf:
        resource = pdf.pages[0].obj[Name.Resources][Name.XObject][Name("/fzFrm0")]
        resource = resource[Name.Resources][Name.XObject][Name("/fullpage")]
        instructions = list(pikepdf.parse_content_stream(resource))
        target_index = next(
            index
            for index, instruction in enumerate(instructions)
            if str(instruction.operator) in {"m", "l", "c", "re"}
        )
        target = instructions[target_index]
        operands = list(target.operands)
        operands[-1] = float(operands[-1]) + 1.0
        instructions[target_index] = pikepdf.ContentStreamInstruction(
            operands, target.operator
        )
        resource.write(pikepdf.unparse_content_stream(instructions))
        pdf.save(tampered)

    assert not verify_vector_equation_formula_association(
        tampered, cluster, plan, association
    )


def test_source_budget_rejects_before_provider_or_output_mutation(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    _write_vector_pdf(source)
    output.write_bytes(b"prior")
    cluster = VectorEquationClusterDetector().find_clusters(source)[0]
    recognizer = _Recognizer()
    from src.education.remediation import vector_equation_semantics as module

    monkeypatch.setattr(module, "_MAX_TRANSACTION_BYTES", 1)
    with pytest.raises(VectorEquationSemanticRejected, match="source_byte_limit"):
        remediate_vector_equation_pdf(source, output, cluster, recognizer, _Verifier())
    assert recognizer.calls == 0
    assert output.read_bytes() == b"prior"


def test_existing_semantic_owner_around_vector_span_is_ambiguous(tmp_path):
    raw = tmp_path / "raw.pdf"
    marked = tmp_path / "marked.pdf"
    _write_vector_pdf(raw)
    with pikepdf.open(raw) as pdf:
        page = pdf.pages[0]
        contents = page.obj[Name.Contents]
        streams = list(contents) if isinstance(contents, Array) else [contents]
        target = next(
            stream
            for stream in streams
            if any(
                str(item.operator) in {"S", "s", "f", "F", "f*", "B", "B*"}
                for item in pikepdf.parse_content_stream(stream)
            )
        )
        instructions = list(pikepdf.parse_content_stream(target))
        instructions.insert(
            0,
            pikepdf.ContentStreamInstruction([Name("/Artifact")], Operator("BMC")),
        )
        instructions.append(pikepdf.ContentStreamInstruction([], Operator("EMC")))
        target.write(pikepdf.unparse_content_stream(instructions))
        pdf.save(marked)
    cluster = VectorEquationClusterDetector().find_clusters(marked)[0]
    plan = plan_vector_equation_semantics(marked, cluster, _Recognizer(), _Verifier())

    with fitz.open(marked) as fitz_doc, pikepdf.open(marked) as pdf:
        page = pdf.pages[0]
        contents = page.obj[Name.Contents]
        streams = list(contents) if isinstance(contents, Array) else [contents]
        before = tuple(
            pikepdf.unparse_content_stream(list(pikepdf.parse_content_stream(stream)))
            for stream in streams
        )
        with pytest.raises(
            VectorEquationSemanticRejected,
            match="existing_ownership_ambiguous",
        ):
            associate_vector_equation_formula(pdf, fitz_doc, cluster, plan)
        after = tuple(
            pikepdf.unparse_content_stream(list(pikepdf.parse_content_stream(stream)))
            for stream in streams
        )
        assert after == before
