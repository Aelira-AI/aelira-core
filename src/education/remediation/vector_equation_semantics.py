"""Atomic recognition and original-operator association for vector equations."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
import pikepdf
from PIL import Image
from pikepdf import Array, Dictionary, Name, Operator

from src.education.canonical_json import canonical_sha256
from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.remediation.content_tagger_v2 import (
    _number_tree_entries,
    _page_render_signature,
    _set_number_tree_value,
    preflight_scanned_region_render_budget,
)
from src.education.remediation.equation_image_source import _deterministic_jpeg
from src.education.remediation.math_fixer import generate_equation_alt_text
from src.education.remediation.pdf_structure import PDFStructureTree
from src.education.vector_equation_cluster import (
    MAX_VECTOR_OPERATOR_SPANS,
    MAX_VECTOR_RASTER_BYTES,
    VectorEquationClusterV1,
    VectorObjectIdentityV1,
    VectorOperatorSpanV1,
    VectorResourceIdentityV1,
)
from src.education.vector_equation_semantics import (
    VectorEquationSemanticContractV1,
    VectorEquationSemanticPlanV1,
    VectorFormulaSavedEvidenceV1,
    VectorMarkedSpanSavedV1,
    build_vector_equation_semantic_contract,
    build_vector_equation_semantic_plan,
)
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)


class VectorEquationSemanticRejected(ValueError):
    """The vector equation could not be associated without ambiguity."""


_MAX_TRANSACTION_BYTES = 512 * 1024 * 1024
_MAX_CONTENT_STREAM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedVectorEquationRaster:
    """Provider-safe JPEG normalized from the exact #229 raster evidence."""

    jpeg_bytes: bytes
    mime_type: str
    normalized_sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class VectorEquationAssociation:
    """Pre-save identities needed to reverse-verify one Formula association."""

    page_number: int
    struct_parent: int
    mcids: tuple[int, ...]
    page_stream_indices: tuple[int, ...]
    stream_semantic_sha256: tuple[str, ...]
    resource_semantic_sha256: tuple[str, ...]
    render_signatures: tuple[tuple[int, int, int, int, int, str], ...]
    page_text_sha256: str


def _validated_raster(
    cluster: VectorEquationClusterV1,
) -> ValidatedVectorEquationRaster:
    try:
        payload = cluster.raster.png_bytes
        if len(payload) > MAX_VECTOR_RASTER_BYTES:
            raise ValueError
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if image.size != (cluster.raster.width, cluster.raster.height):
                raise ValueError
            jpeg = _deterministic_jpeg(image)
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_raster_binding_failed"
        ) from exc
    return ValidatedVectorEquationRaster(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
        width=cluster.raster.width,
        height=cluster.raster.height,
    )


def plan_vector_equation_semantics(
    source_path: str | Path,
    cluster: Any,
    recognizer: Any,
    verifier: Any,
) -> VectorEquationSemanticPlanV1:
    """Recognize one current exact cluster and bind independent verifier evidence."""

    try:
        checked = VectorEquationClusterV1.model_validate(cluster)
    except (TypeError, ValueError) as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_contract_invalid"
        ) from exc
    path = Path(source_path)
    try:
        if path.stat().st_size <= 0 or path.stat().st_size > _MAX_TRANSACTION_BYTES:
            raise VectorEquationSemanticRejected("vector_equation_source_byte_limit")
    except OSError as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_source_unavailable"
        ) from exc
    if not VectorEquationClusterDetector().revalidate(path, checked):
        raise VectorEquationSemanticRejected("vector_equation_source_changed")
    if recognizer is None:
        raise VectorEquationSemanticRejected("vector_equation_recognizer_unavailable")
    if verifier is None:
        raise VectorEquationSemanticRejected("vector_equation_verifier_unavailable")

    raster = _validated_raster(checked)
    try:
        recognition = recognizer.recognize(raster)
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_recognition_failed"
        ) from exc
    if (
        getattr(recognition, "classification", None) != "printed_equation"
        or not isinstance(getattr(recognition, "latex", None), str)
        or not recognition.latex
    ):
        raise VectorEquationSemanticRejected("vector_equation_recognition_failed")
    try:
        evidence = verifier.verify(raster, recognition.latex)
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_verification_failed"
        ) from exc
    if not getattr(evidence, "passed", False):
        raise VectorEquationSemanticRejected("vector_equation_verification_failed")
    try:
        converted = verifier.converter(recognition.latex)
        mathml = verifier.canonicalize_mathml(converted)
        mathml_sha256 = hashlib.sha256(mathml.encode("utf-8")).hexdigest()
        bounded_evidence = PrintedEquationRoundtripEvidenceV1(
            evidence_kind="printed_equation_roundtrip_v1",
            **asdict(evidence),
        )
        if (
            bounded_evidence.source_sha256 != raster.normalized_sha256
            or bounded_evidence.mathml_sha256 != mathml_sha256
        ):
            raise ValueError
        semantic = MathMLExpressionV1(
            semantic_kind="mathml_expression_v1",
            mathml=mathml,
            alt_text=generate_equation_alt_text(recognition.latex),
            mathml_sha256=mathml_sha256,
        )
        provider = recognition.provider
        model = recognition.model
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError
        return build_vector_equation_semantic_plan(
            cluster_sha256=checked.cluster_sha256,
            normalized_source_sha256=raster.normalized_sha256,
            semantic_output=semantic,
            verification_evidence=bounded_evidence,
            provider=provider,
            model=model,
        )
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_verification_mismatch"
        ) from exc


def _instructions_identity(instructions: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "operator": str(instruction.operator),
            "operands": [str(operand) for operand in instruction.operands],
        }
        for instruction in instructions
    ]


def _content_streams(page: Any) -> list[Any]:
    contents = page.obj.get(Name.Contents)
    if contents is None:
        raise VectorEquationSemanticRejected("vector_equation_content_missing")
    return list(contents) if isinstance(contents, Array) else [contents]


def _validate_stream_identity(stream: Any, expected: VectorObjectIdentityV1) -> None:
    try:
        raw = stream.read_raw_bytes()
        actual_objgen = tuple(stream.objgen)
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_stream_unavailable"
        ) from exc
    if (
        actual_objgen != (expected.object_number, expected.generation)
        or len(raw) > _MAX_CONTENT_STREAM_BYTES
        or hashlib.sha256(raw).hexdigest() != expected.passive_sha256
    ):
        raise VectorEquationSemanticRejected("vector_equation_stream_changed")


def _resource_semantic_sha256(value: Any) -> str:
    if not isinstance(value, pikepdf.Stream):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    if str(value.get(Name.Subtype, "")) == "/Form":
        return canonical_sha256(
            _instructions_identity(list(pikepdf.parse_content_stream(value)))
        )
    return hashlib.sha256(value.read_bytes()).hexdigest()


def _resolve_resources(
    pdf: Any,
    expected: tuple[VectorResourceIdentityV1, ...],
    *,
    page: Any,
    require_passive: bool,
    expected_semantic: tuple[str, ...] | None = None,
) -> tuple[tuple[VectorResourceIdentityV1, ...], tuple[str, ...]]:
    resolved: list[VectorResourceIdentityV1] = []
    semantic: list[str] = []
    for index, resource in enumerate(expected):
        identity = resource.object_identity
        try:
            if resource.resource_kind == "xobject":
                value = page.obj
                names = re.findall(r"/[^/]+", resource.resource_name)
                if not names:
                    raise ValueError
                for resource_name in names:
                    resources = value.get(Name.Resources)
                    xobjects = (
                        resources.get(Name.XObject) if resources is not None else None
                    )
                    if xobjects is None:
                        raise ValueError
                    value = xobjects.get(Name(resource_name))
                    if value is None:
                        raise ValueError
            else:
                value = pdf.get_object(identity.object_number, identity.generation)
            if hasattr(value, "read_raw_bytes"):
                passive = str(value).encode("utf-8") + b"\x00" + value.read_raw_bytes()
            else:
                passive = str(value).encode("utf-8")
        except Exception as exc:
            raise VectorEquationSemanticRejected(
                "vector_equation_resource_unavailable"
            ) from exc
        if require_passive and (
            tuple(value.objgen) != (identity.object_number, identity.generation)
            or hashlib.sha256(passive).hexdigest() != identity.passive_sha256
        ):
            raise VectorEquationSemanticRejected("vector_equation_resource_changed")
        semantic_sha256 = _resource_semantic_sha256(value)
        if expected_semantic is not None and (
            index >= len(expected_semantic)
            or semantic_sha256 != expected_semantic[index]
        ):
            raise VectorEquationSemanticRejected("vector_equation_resource_changed")
        resolved.append(resource)
        semantic.append(semantic_sha256)
    if expected_semantic is not None and len(expected_semantic) != len(semantic):
        raise VectorEquationSemanticRejected("vector_equation_resource_changed")
    return tuple(resolved), tuple(semantic)


def _span_stream_index(
    cluster: VectorEquationClusterV1, span: VectorOperatorSpanV1
) -> int:
    matches = [
        index
        for index, stream in enumerate(cluster.content_streams)
        if stream == span.stream
    ]
    if len(matches) != 1:
        raise VectorEquationSemanticRejected("vector_equation_span_stream_ambiguous")
    return matches[0]


def _validate_marked_content_seams(
    instructions: list[Any], spans: list[VectorOperatorSpanV1]
) -> None:
    depth = 0
    starts = {span.first_operator for span in spans}
    ends = {span.last_operator for span in spans}
    for index, instruction in enumerate(instructions):
        operator = str(instruction.operator)
        if index in starts and depth:
            raise VectorEquationSemanticRejected(
                "vector_equation_existing_ownership_ambiguous"
            )
        if operator in {"BMC", "BDC"}:
            depth += 1
        elif operator == "EMC":
            depth -= 1
            if depth < 0:
                raise VectorEquationSemanticRejected(
                    "vector_equation_marked_content_invalid"
                )
        if index in ends and depth:
            raise VectorEquationSemanticRejected(
                "vector_equation_existing_ownership_ambiguous"
            )
    if depth:
        raise VectorEquationSemanticRejected("vector_equation_marked_content_invalid")


def _used_mcids(instructions_by_stream: list[list[Any]]) -> set[int]:
    used: set[int] = set()
    for instructions in instructions_by_stream:
        for instruction in instructions:
            if str(instruction.operator) != "BDC" or len(instruction.operands) != 2:
                continue
            try:
                used.add(int(instruction.operands[1][Name.MCID]))
            except Exception:
                continue
    return used


def _structure_parent(pdf: Any) -> Any:
    root = pdf.Root[Name.StructTreeRoot]
    kids = root.get(Name.K)
    candidates = list(kids) if isinstance(kids, Array) else ([kids] if kids else [])
    if (
        len(candidates) == 1
        and hasattr(candidates[0], "keys")
        and str(candidates[0].get(Name.S, "")) == "/Document"
    ):
        return candidates[0]
    return root


def _append_formula(pdf: Any, formula: Any) -> None:
    parent = _structure_parent(pdf)
    formula[Name.P] = parent
    children = parent.get(Name.K)
    if children is None:
        parent[Name.K] = Array([formula])
    elif isinstance(children, Array):
        children.append(formula)
    else:
        parent[Name.K] = Array([children, formula])


def associate_vector_equation_formula(
    pdf: Any,
    fitz_doc: Any,
    cluster: Any,
    semantic_plan: Any,
) -> VectorEquationAssociation:
    """Wrap exact original spans and connect all MCRs to one Formula owner."""

    try:
        checked = VectorEquationClusterV1.model_validate(cluster)
        plan = VectorEquationSemanticPlanV1.model_validate(semantic_plan)
    except (TypeError, ValueError) as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_association_contract_invalid"
        ) from exc
    if plan.cluster_sha256 != checked.cluster_sha256:
        raise VectorEquationSemanticRejected("vector_equation_plan_source_mismatch")
    if checked.page_number > len(pdf.pages) or checked.page_number > len(fitz_doc):
        raise VectorEquationSemanticRejected("vector_equation_page_missing")
    if len(checked.operator_spans) > MAX_VECTOR_OPERATOR_SPANS:
        raise VectorEquationSemanticRejected("vector_equation_operator_budget")

    preflight_scanned_region_render_budget(fitz_doc, (checked.page_number,))
    before_render = tuple(
        _page_render_signature(fitz_doc, checked.page_number, dpi) for dpi in (72, 144)
    )
    page_text_sha256 = hashlib.sha256(
        fitz_doc[checked.page_number - 1].get_text("text").encode("utf-8")
    ).hexdigest()
    page = pdf.pages[checked.page_number - 1]
    streams = _content_streams(page)
    page_stream_indices: list[int] = []
    for expected in checked.content_streams:
        matches = [
            index
            for index, stream in enumerate(streams)
            if tuple(stream.objgen) == (expected.object_number, expected.generation)
        ]
        if len(matches) != 1:
            raise VectorEquationSemanticRejected("vector_equation_stream_changed")
        _validate_stream_identity(streams[matches[0]], expected)
        page_stream_indices.append(matches[0])
    instructions_by_stream: list[list[Any]] = []
    stream_semantic_sha256: list[str] = []
    spans_by_stream: dict[int, list[tuple[int, VectorOperatorSpanV1]]] = {}
    for ordinal, span in enumerate(checked.operator_spans):
        index = page_stream_indices[_span_stream_index(checked, span)]
        spans_by_stream.setdefault(index, []).append((ordinal, span))
    for index, stream in enumerate(streams):
        instructions = list(pikepdf.parse_content_stream(stream))
        if (
            len(pikepdf.unparse_content_stream(instructions))
            > _MAX_CONTENT_STREAM_BYTES
        ):
            raise VectorEquationSemanticRejected("vector_equation_stream_byte_limit")
        selected = [span for _, span in spans_by_stream.get(index, [])]
        _validate_marked_content_seams(instructions, selected)
        encoded = _instructions_identity(instructions)
        for span in selected:
            if span.last_operator >= len(instructions):
                raise VectorEquationSemanticRejected("vector_equation_span_changed")
            if (
                canonical_sha256(encoded[span.first_operator : span.last_operator + 1])
                != span.operators_sha256
                or canonical_sha256(encoded[: span.last_operator + 1])
                != span.graphics_state_sha256
            ):
                raise VectorEquationSemanticRejected("vector_equation_span_changed")
        instructions_by_stream.append(instructions)
        stream_semantic_sha256.append(canonical_sha256(encoded))
    _, resource_semantic = _resolve_resources(
        pdf, checked.resources, page=page, require_passive=True
    )

    PDFStructureTree(pdf)
    struct_root = pdf.Root[Name.StructTreeRoot]
    parent_tree, entries = _number_tree_entries(struct_root)
    existing_struct_parent = page.obj.get(Name.StructParents)
    if existing_struct_parent is None:
        used_keys = {key for key, _ in entries}
        struct_parent = 0
        while struct_parent in used_keys:
            struct_parent += 1
    else:
        struct_parent = int(existing_struct_parent)
    page_entry = next((value for key, value in entries if key == struct_parent), None)
    if page_entry is not None and not isinstance(page_entry, Array):
        raise VectorEquationSemanticRejected("vector_equation_parent_tree_collision")
    page_array = list(page_entry) if page_entry is not None else []
    used = _used_mcids(instructions_by_stream)
    next_mcid = max(used | {len(page_array) - 1}, default=-1) + 1
    mcids = tuple(range(next_mcid, next_mcid + len(checked.operator_spans)))
    if any(mcid < len(page_array) and page_array[mcid] is not None for mcid in mcids):
        raise VectorEquationSemanticRejected("vector_equation_mcid_collision")

    formula = PDFStructureTree(pdf).create_formula_element(
        page_num=checked.page_number,
        alt_text=plan.semantic_output.alt_text,
        mathml_string=plan.semantic_output.mathml,
        bbox=checked.pdf_bbox,
    )
    mcrs = Array(
        [
            Dictionary({"/Type": Name("/MCR"), "/MCID": mcid, "/Pg": page.obj})
            for mcid in mcids
        ]
    )
    formula[Name.K] = mcrs
    _append_formula(pdf, formula)
    for mcid in mcids:
        while len(page_array) <= mcid:
            page_array.append(None)
        page_array[mcid] = formula
    _set_number_tree_value(
        parent_tree, struct_parent, pdf.make_indirect(Array(page_array))
    )
    page.obj[Name.StructParents] = struct_parent

    for stream_index, selected in spans_by_stream.items():
        instructions = instructions_by_stream[stream_index]
        for ordinal, span in sorted(
            selected, key=lambda item: item[1].first_operator, reverse=True
        ):
            mcid = mcids[ordinal]
            instructions.insert(
                span.last_operator + 1,
                pikepdf.ContentStreamInstruction([], Operator("EMC")),
            )
            instructions.insert(
                span.first_operator,
                pikepdf.ContentStreamInstruction(
                    [Name("/Formula"), Dictionary({"/MCID": mcid})],
                    Operator("BDC"),
                ),
            )
        streams[stream_index].write(pikepdf.unparse_content_stream(instructions))

    return VectorEquationAssociation(
        page_number=checked.page_number,
        struct_parent=struct_parent,
        mcids=mcids,
        page_stream_indices=tuple(page_stream_indices),
        stream_semantic_sha256=tuple(stream_semantic_sha256),
        resource_semantic_sha256=resource_semantic,
        render_signatures=before_render,
        page_text_sha256=page_text_sha256,
    )


def _formula_for_mcids(pdf: Any, page: Any, struct_parent: int, mcids: tuple[int, ...]):
    root = pdf.Root.get(Name.StructTreeRoot)
    if root is None:
        raise VectorEquationSemanticRejected("vector_equation_structure_missing")
    _, entries = _number_tree_entries(root)
    page_entry = next((value for key, value in entries if key == struct_parent), None)
    if not isinstance(page_entry, Array):
        raise VectorEquationSemanticRejected("vector_equation_parent_tree_missing")
    formulas = []
    for mcid in mcids:
        if mcid >= len(page_entry) or page_entry[mcid] is None:
            raise VectorEquationSemanticRejected("vector_equation_parent_tree_missing")
        formulas.append(page_entry[mcid])
    if not formulas or any(
        tuple(item.objgen) != tuple(formulas[0].objgen) for item in formulas
    ):
        raise VectorEquationSemanticRejected(
            "vector_equation_formula_ownership_changed"
        )
    formula = formulas[0]
    if (
        str(formula.get(Name.S, "")) != "/Formula"
        or formula.get(Name.Pg) is None
        or tuple(formula[Name.Pg].objgen) != tuple(page.obj.objgen)
    ):
        raise VectorEquationSemanticRejected(
            "vector_equation_formula_ownership_changed"
        )
    return formula


def _mathml_bytes(formula: Any) -> bytes:
    try:
        files = formula[Name("/AF")]
        if not isinstance(files, Array) or len(files) != 1:
            raise ValueError
        stream = files[0][Name("/EF")][Name.F]
        return stream.read_bytes()
    except Exception as exc:
        raise VectorEquationSemanticRejected("vector_equation_mathml_changed") from exc


def _unwrapped_stream(
    instructions: list[Any], expected_mcids: set[int]
) -> tuple[list[Any], dict[int, list[Any]]]:
    unwrapped: list[Any] = []
    payloads: dict[int, list[Any]] = {}
    active: int | None = None
    current: list[Any] = []
    for instruction in instructions:
        operator = str(instruction.operator)
        if operator == "BDC" and len(instruction.operands) == 2:
            try:
                tag = str(instruction.operands[0])
                mcid = int(instruction.operands[1][Name.MCID])
            except Exception:
                tag = ""
                mcid = -1
            if tag == "/Formula" and mcid in expected_mcids:
                if active is not None or mcid in payloads:
                    raise VectorEquationSemanticRejected(
                        "vector_equation_wrapper_changed"
                    )
                active = mcid
                current = []
                continue
        if operator == "EMC" and active is not None:
            payloads[active] = current
            active = None
            current = []
            continue
        if active is not None:
            current.append(instruction)
        unwrapped.append(instruction)
    if active is not None or set(payloads) != expected_mcids:
        raise VectorEquationSemanticRejected("vector_equation_wrapper_changed")
    return unwrapped, payloads


def verify_vector_equation_formula(
    path: str | Path,
    cluster: Any,
    semantic_plan: Any,
    association: VectorEquationAssociation,
) -> VectorFormulaSavedEvidenceV1:
    """Reopen a saved candidate and prove source, structure, semantics, and pixels."""

    try:
        checked = VectorEquationClusterV1.model_validate(cluster)
        plan = VectorEquationSemanticPlanV1.model_validate(semantic_plan)
    except (TypeError, ValueError) as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_saved_contract_invalid"
        ) from exc
    source_path = Path(path)
    try:
        saved_bytes = source_path.read_bytes()
    except OSError as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_saved_file_unavailable"
        ) from exc
    if not saved_bytes or len(saved_bytes) > _MAX_TRANSACTION_BYTES:
        raise VectorEquationSemanticRejected("vector_equation_saved_byte_limit")
    if (
        association.page_number != checked.page_number
        or len(association.mcids) != len(checked.operator_spans)
        or plan.cluster_sha256 != checked.cluster_sha256
    ):
        raise VectorEquationSemanticRejected("vector_equation_saved_plan_mismatch")

    try:
        with fitz.open(source_path) as fitz_doc, pikepdf.open(source_path) as pdf:
            preflight_scanned_region_render_budget(fitz_doc, (checked.page_number,))
            after_render = tuple(
                _page_render_signature(fitz_doc, checked.page_number, dpi)
                for dpi in (72, 144)
            )
            if after_render != association.render_signatures:
                raise VectorEquationSemanticRejected("vector_equation_visual_changed")
            page_text_sha256 = hashlib.sha256(
                fitz_doc[checked.page_number - 1].get_text("text").encode("utf-8")
            ).hexdigest()
            if page_text_sha256 != association.page_text_sha256:
                raise VectorEquationSemanticRejected(
                    "vector_equation_page_text_changed"
                )
            page = pdf.pages[checked.page_number - 1]
            resources, _ = _resolve_resources(
                pdf,
                checked.resources,
                page=page,
                require_passive=False,
                expected_semantic=association.resource_semantic_sha256,
            )
            if int(page.obj.get(Name.StructParents, -1)) != association.struct_parent:
                raise VectorEquationSemanticRejected(
                    "vector_equation_struct_parent_changed"
                )
            formula = _formula_for_mcids(
                pdf, page, association.struct_parent, association.mcids
            )
            bbox = tuple(float(value) for value in formula[Name.A][Name("/BBox")])
            if len(bbox) != 4 or any(
                abs(current - expected) > 1e-6
                for current, expected in zip(bbox, checked.pdf_bbox)
            ):
                raise VectorEquationSemanticRejected("vector_equation_bbox_changed")
            try:
                mcrs = formula[Name.K]
                if not isinstance(mcrs, Array) or len(mcrs) != len(association.mcids):
                    raise ValueError
                saved_mcids = tuple(int(mcr[Name.MCID]) for mcr in mcrs)
                if saved_mcids != association.mcids or any(
                    tuple(mcr[Name.Pg].objgen) != tuple(page.obj.objgen) for mcr in mcrs
                ):
                    raise ValueError
            except Exception as exc:
                raise VectorEquationSemanticRejected(
                    "vector_equation_mcr_changed"
                ) from exc
            alt_text = str(formula[Name.Alt])
            mathml = _mathml_bytes(formula)
            if (
                hashlib.sha256(alt_text.encode("utf-8")).hexdigest()
                != hashlib.sha256(
                    plan.semantic_output.alt_text.encode("utf-8")
                ).hexdigest()
                or hashlib.sha256(mathml).hexdigest()
                != plan.semantic_output.mathml_sha256
            ):
                raise VectorEquationSemanticRejected(
                    "vector_equation_saved_semantics_changed"
                )

            streams = _content_streams(page)
            if len(streams) != len(association.stream_semantic_sha256):
                raise VectorEquationSemanticRejected(
                    "vector_equation_saved_stream_count_changed"
                )
            span_payloads: dict[int, list[Any]] = {}
            unwrapped_by_stream: list[list[Any]] = []
            for stream_index, stream in enumerate(streams):
                instructions = list(pikepdf.parse_content_stream(stream))
                ordinals = [
                    ordinal
                    for ordinal, span in enumerate(checked.operator_spans)
                    if association.page_stream_indices[
                        _span_stream_index(checked, span)
                    ]
                    == stream_index
                ]
                expected_mcids = {association.mcids[ordinal] for ordinal in ordinals}
                unwrapped, payloads = _unwrapped_stream(instructions, expected_mcids)
                if (
                    canonical_sha256(_instructions_identity(unwrapped))
                    != association.stream_semantic_sha256[stream_index]
                ):
                    raise VectorEquationSemanticRejected(
                        "vector_equation_saved_stream_changed"
                    )
                unwrapped_by_stream.append(unwrapped)
                span_payloads.update(payloads)

            saved_spans: list[VectorMarkedSpanSavedV1] = []
            for ordinal, (span, mcid) in enumerate(
                zip(checked.operator_spans, association.mcids)
            ):
                stream_index = association.page_stream_indices[
                    _span_stream_index(checked, span)
                ]
                payload = span_payloads.get(mcid)
                unwrapped = unwrapped_by_stream[stream_index]
                if payload is None or (
                    canonical_sha256(_instructions_identity(payload))
                    != span.operators_sha256
                    or canonical_sha256(
                        _instructions_identity(unwrapped[: span.last_operator + 1])
                    )
                    != span.graphics_state_sha256
                ):
                    raise VectorEquationSemanticRejected(
                        "vector_equation_saved_operators_changed"
                    )
                stream = streams[stream_index]
                saved_spans.append(
                    VectorMarkedSpanSavedV1(
                        span_kind="vector_marked_span_saved_v1",
                        ordinal=ordinal,
                        content_stream_index=stream_index,
                        stream_object_number=int(stream.objgen[0]),
                        stream_generation=int(stream.objgen[1]),
                        mcid=mcid,
                        first_operator=span.first_operator,
                        last_operator=span.last_operator,
                        operator_count=span.operator_count,
                        operators_sha256=span.operators_sha256,
                        graphics_state_sha256=span.graphics_state_sha256,
                        unwrapped_stream_sha256=association.stream_semantic_sha256[
                            stream_index
                        ],
                    )
                )
            return VectorFormulaSavedEvidenceV1(
                evidence_kind="vector_formula_saved_v1",
                passed=True,
                saved_file_sha256=hashlib.sha256(saved_bytes).hexdigest(),
                page_number=checked.page_number,
                struct_parent=association.struct_parent,
                formula_object_number=int(formula.objgen[0]),
                formula_generation=int(formula.objgen[1]),
                marked_spans=tuple(saved_spans),
                formula_bbox=bbox,
                mathml_sha256=plan.semantic_output.mathml_sha256,
                alt_text_sha256=hashlib.sha256(
                    plan.semantic_output.alt_text.encode("utf-8")
                ).hexdigest(),
                resource_identities=resources,
                render_signatures=after_render,
                page_text_sha256=page_text_sha256,
            )
    except VectorEquationSemanticRejected:
        raise
    except Exception as exc:
        raise VectorEquationSemanticRejected(
            "vector_equation_saved_verification_failed"
        ) from exc


def verify_vector_equation_formula_association(
    path: str | Path,
    cluster: Any,
    semantic_plan: Any,
    association: VectorEquationAssociation,
) -> bool:
    try:
        verify_vector_equation_formula(path, cluster, semantic_plan, association)
        return True
    except (OSError, TypeError, ValueError):
        return False


def remediate_vector_equation_pdf(
    source_path: str | Path,
    output_path: str | Path,
    cluster: Any,
    recognizer: Any,
    verifier: Any,
) -> VectorEquationSemanticContractV1:
    """Write one disposable candidate, reverse-verify it, then replace output."""

    source = Path(source_path)
    output = Path(output_path)
    plan = plan_vector_equation_semantics(source, cluster, recognizer, verifier)
    checked = VectorEquationClusterV1.model_validate(cluster)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{output.name}.vector-", suffix=".pdf", dir=output.parent
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    try:
        with fitz.open(source) as fitz_doc, pikepdf.open(source) as pdf:
            association = associate_vector_equation_formula(
                pdf, fitz_doc, checked, plan
            )
            pdf.save(candidate)
        saved = verify_vector_equation_formula(candidate, checked, plan, association)
        contract = build_vector_equation_semantic_contract(
            cluster=checked,
            semantic_plan=plan,
            saved_evidence=saved,
        )
        os.replace(candidate, output)
        return contract
    except Exception:
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
        raise


__all__ = [
    "ValidatedVectorEquationRaster",
    "VectorEquationAssociation",
    "VectorEquationSemanticRejected",
    "associate_vector_equation_formula",
    "plan_vector_equation_semantics",
    "remediate_vector_equation_pdf",
    "verify_vector_equation_formula",
    "verify_vector_equation_formula_association",
]
