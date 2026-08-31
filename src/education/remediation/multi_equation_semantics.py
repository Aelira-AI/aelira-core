"""Fail-closed semantic planning for complete multi-equation raster groups."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional

import fitz
import pikepdf
from PIL import Image
from pikepdf import Array, Dictionary, Name, Operator

from src.education.multi_equation_region import (
    MultiEquationRegionGroupV1,
    child_pixel_payload_sha256,
)
from src.education.multi_equation_semantics import (
    MultiEquationSavedEvidenceV1,
    MultiEquationSavedOwnerV1,
    MultiEquationSemanticContractV1,
    MultiEquationSemanticOwnerV1,
    _formula_bbox_for_owner,
    build_multi_equation_semantic_contract,
    build_multi_equation_semantic_owner,
    validate_multi_equation_semantic_plan,
)
from src.education.pdf_checks.multi_equation_region_detector import (
    MultiEquationRegionDetector,
)
from src.education.remediation.equation_image_source import (
    ValidatedEquationRaster,
    _deterministic_jpeg,
)
from src.education.remediation.equation_recognizer import EquationRecognizer
from src.education.remediation.equation_verifier import EquationVerifier
from src.education.remediation.math_fixer import generate_equation_alt_text
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)


class MultiEquationSemanticRejected(ValueError):
    """A complete semantic plan could not be proved without partial output."""


_MAX_TRANSACTION_BYTES = 512 * 1024 * 1024
_MAX_PROVIDER_CALLS_PER_GROUP = 8
_MAX_SEMANTIC_BYTES_PER_GROUP = 512 * 1024


@dataclass(frozen=True)
class ValidatedMultiEquationRaster:
    """Provider-safe normalized raster for one exact whole-system union."""

    jpeg_bytes: bytes
    mime_type: str
    normalized_sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class MultiEquationOwnerAssociation:
    """In-memory identity for one Formula created by a group transaction."""

    ordinal: int
    region_ids: tuple[str, ...]
    mcid: int
    mathml_sha256: str
    alt_text_sha256: str
    formula_bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class MultiEquationAssociation:
    """Bounded evidence required to reverse-verify a serialized group."""

    page_number: int
    image_xref: int
    resource_name: str
    struct_parent: int
    owners: tuple[MultiEquationOwnerAssociation, ...]
    render_signatures: tuple[tuple[int, int, int, int, int, str], ...]
    ocr_resource_name: str = ""
    ocr_struct_parent: int = -1
    ocr_group_owners: tuple[tuple[str, int], ...] = ()
    ocr_before_mcids: tuple[int, ...] = ()
    ocr_after_mcids: tuple[int, ...] = ()
    ocr_payload_sha256: str = ""
    ocr_font_sha256: str = ""
    page_text_sha256: str = ""


def _union_bbox(group: MultiEquationRegionGroupV1, field: str):
    boxes = [getattr(child, field) for child in group.children]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def extract_whole_system_raster(
    document: Any, group: MultiEquationRegionGroupV1
) -> ValidatedMultiEquationRaster:
    """Crop the deterministic union from the revalidated original image stream."""

    from src.education.pdf_checks.equation_region_detector import (
        RasterEquationRegionDetector,
    )

    loaded = RasterEquationRegionDetector._load_source(document, group.image_xref)
    if loaded is None:
        raise MultiEquationSemanticRejected("multi_equation_source_unavailable")
    image, source_sha256 = loaded
    try:
        if source_sha256 != group.source_sha256 or image.size != (
            group.source_width,
            group.source_height,
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        pixel_bbox = _union_bbox(group, "pixel_bbox")
        crop = image.crop(pixel_bbox)
        try:
            jpeg = _deterministic_jpeg(crop)
        finally:
            crop.close()
    finally:
        image.close()
    return ValidatedMultiEquationRaster(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
        width=pixel_bbox[2] - pixel_bbox[0],
        height=pixel_bbox[3] - pixel_bbox[1],
    )


def extract_multi_equation_child_raster(
    document: Any, locator: Any
) -> ValidatedMultiEquationRaster:
    """Normalize one child only after exact source and crop revalidation."""

    from src.education.pdf_checks.equation_region_detector import (
        RasterEquationRegionDetector,
    )

    loaded = RasterEquationRegionDetector._load_source(document, locator.image_xref)
    if loaded is None:
        raise MultiEquationSemanticRejected("multi_equation_source_unavailable")
    image, source_sha256 = loaded
    try:
        if source_sha256 != locator.source_sha256 or image.size != (
            locator.source_width,
            locator.source_height,
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        crop = image.crop(locator.pixel_bbox)
        try:
            if (
                child_pixel_payload_sha256(crop.mode, crop.size, crop.tobytes())
                != locator.crop_pixel_sha256
            ):
                raise MultiEquationSemanticRejected("multi_equation_crop_changed")
            jpeg = _deterministic_jpeg(crop)
        finally:
            crop.close()
    finally:
        image.close()
    return ValidatedMultiEquationRaster(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
        width=locator.pixel_bbox[2] - locator.pixel_bbox[0],
        height=locator.pixel_bbox[3] - locator.pixel_bbox[1],
    )


class MultiEquationSemanticPlanner:
    """Recognize and verify every owner required by one revalidated group."""

    def __init__(
        self,
        recognizer: EquationRecognizer,
        verifier: EquationVerifier,
        *,
        detector: Optional[MultiEquationRegionDetector] = None,
        child_source: Optional[Callable[[Any, Any], ValidatedEquationRaster]] = None,
        system_source: Optional[
            Callable[[Any, MultiEquationRegionGroupV1], ValidatedEquationRaster]
        ] = None,
        alt_text_builder: Callable[[str], str] = generate_equation_alt_text,
    ) -> None:
        self.recognizer = recognizer
        self.verifier = verifier
        self.detector = detector or MultiEquationRegionDetector()
        self.child_source = child_source or extract_multi_equation_child_raster
        self.system_source = system_source or extract_whole_system_raster
        self.alt_text_builder = alt_text_builder

    def plan(
        self, document: Any, value: Any
    ) -> tuple[MultiEquationSemanticOwnerV1, ...]:
        """Return all required owners or reject without returning a subset."""

        try:
            group = MultiEquationRegionGroupV1.model_validate(value)
            if self.detector.revalidate_group(document, group) != group:
                raise MultiEquationSemanticRejected("multi_equation_group_stale")
            if group.disposition == "split_children":
                sources = tuple(
                    (
                        "multi_equation_child_v1",
                        index,
                        (child.region_id,),
                        child.pixel_bbox,
                        child.pdf_bbox,
                        self.child_source(document, child),
                    )
                    for index, child in enumerate(group.children)
                )
            else:
                sources = (
                    (
                        "multi_equation_system_v1",
                        0,
                        tuple(child.region_id for child in group.children),
                        _union_bbox(group, "pixel_bbox"),
                        _union_bbox(group, "pdf_bbox"),
                        self.system_source(document, group),
                    ),
                )
            if len(sources) > _MAX_PROVIDER_CALLS_PER_GROUP:
                raise MultiEquationSemanticRejected(
                    "multi_equation_provider_call_budget"
                )
            owners = tuple(self._recognize(*source) for source in sources)
            expected = (
                len(group.children) if group.disposition == "split_children" else 1
            )
            if len(owners) != expected:
                raise MultiEquationSemanticRejected("multi_equation_result_incomplete")
            return owners
        except MultiEquationSemanticRejected:
            raise
        except Exception as exc:
            raise MultiEquationSemanticRejected(
                "multi_equation_semantics_rejected"
            ) from exc

    def _recognize(
        self,
        owner_kind: str,
        ordinal: int,
        region_ids: tuple[str, ...],
        pixel_bbox: tuple[int, int, int, int],
        pdf_bbox: tuple[float, float, float, float],
        source: ValidatedEquationRaster,
    ) -> MultiEquationSemanticOwnerV1:
        if owner_kind == "multi_equation_system_v1" and hasattr(
            self.recognizer, "recognize_system"
        ):
            recognition = self.recognizer.recognize_system(source)
        else:
            recognition = self.recognizer.recognize(source)
        if recognition.classification != "printed_equation" or not recognition.latex:
            raise MultiEquationSemanticRejected("multi_equation_recognition_failed")
        evidence = self.verifier.verify(source, recognition.latex)
        if not evidence.passed or evidence.source_sha256 != source.normalized_sha256:
            raise MultiEquationSemanticRejected("multi_equation_verification_failed")
        mathml = self.verifier.canonicalize_mathml(
            self.verifier.converter(recognition.latex)
        )
        semantic = MathMLExpressionV1(
            semantic_kind="mathml_expression_v1",
            mathml=mathml,
            alt_text=self.alt_text_builder(recognition.latex),
            mathml_sha256=hashlib.sha256(mathml.encode("utf-8")).hexdigest(),
        )
        bounded = PrintedEquationRoundtripEvidenceV1(
            evidence_kind="printed_equation_roundtrip_v1",
            **asdict(evidence),
        )
        return build_multi_equation_semantic_owner(
            owner_kind=owner_kind,  # type: ignore[arg-type]
            ordinal=ordinal,
            region_ids=region_ids,
            pixel_bbox=pixel_bbox,
            pdf_bbox=pdf_bbox,
            semantic_output=semantic,
            normalized_source_sha256=source.normalized_sha256,
            verification_evidence=bounded,
            provider=recognition.provider,
            model=recognition.model,
        )


def _validate_owner_payloads(
    owners: tuple[MultiEquationSemanticOwnerV1, ...],
) -> None:
    aggregate_bytes = 0
    for owner in owners:
        semantic = owner.semantic_output
        mathml_bytes = semantic.mathml.encode("utf-8")
        alt_bytes = semantic.alt_text.encode("utf-8")
        aggregate_bytes += len(mathml_bytes) + len(alt_bytes)
        if (
            not semantic.alt_text
            or len(semantic.alt_text) > 1024
            or not semantic.alt_text.isprintable()
            or not semantic.mathml
            or len(mathml_bytes) > 65_536
            or hashlib.sha256(mathml_bytes).hexdigest() != semantic.mathml_sha256
        ):
            raise MultiEquationSemanticRejected(
                "multi_equation_association_request_invalid"
            )
        if aggregate_bytes > _MAX_SEMANTIC_BYTES_PER_GROUP:
            raise MultiEquationSemanticRejected("multi_equation_semantic_byte_budget")


def _validate_owner_sources(
    source: bytes,
    group: MultiEquationRegionGroupV1,
    owners: tuple[MultiEquationSemanticOwnerV1, ...],
) -> None:
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            boxes = (
                tuple(child.pixel_bbox for child in group.children)
                if group.disposition == "split_children"
                else (_union_bbox(group, "pixel_bbox"),)
            )
            normalized = []
            for bbox in boxes:
                crop = image.crop(bbox)
                try:
                    normalized.append(
                        hashlib.sha256(_deterministic_jpeg(crop)).hexdigest()
                    )
                finally:
                    crop.close()
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_semantic_source_unavailable"
        ) from exc
    if len(normalized) != len(owners) or any(
        digest != owner.normalized_source_sha256
        or owner.verification_evidence.source_sha256 != digest
        for digest, owner in zip(normalized, owners)
    ):
        raise MultiEquationSemanticRejected("multi_equation_semantic_source_changed")


def _source_occurrence(
    document: Any,
    group: MultiEquationRegionGroupV1,
    *,
    require_original_xref: bool,
) -> tuple[dict[str, Any], bytes, tuple[float, ...]]:
    """Resolve one exact displayed source and recheck every child crop."""

    from src.education.pdf_checks.image_checker import _displayed_image_occurrences

    if not 1 <= group.page_number <= len(document):
        raise MultiEquationSemanticRejected("multi_equation_page_changed")
    page = document[group.page_number - 1]
    try:
        infos = list(page.get_image_info(xrefs=True))
        info = infos[group.image_index]
        saved_xref = int(info.get("xref") or 0)
        bbox = tuple(float(value) for value in info["bbox"])
        transform = tuple(float(value) for value in info["transform"])
        source = document.extract_image(saved_xref).get("image")
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_occurrence_changed"
        ) from exc
    original = group.children[0]
    if (
        saved_xref <= 0
        or (require_original_xref and saved_xref != group.image_xref)
        or not isinstance(source, bytes)
        or hashlib.sha256(source).hexdigest() != group.source_sha256
        or any(
            abs(actual - wanted) > 1e-6
            for actual, wanted in zip(bbox, original.parent_bbox)
        )
        or any(
            abs(actual - wanted) > 1e-6
            for actual, wanted in zip(transform, original.transform)
        )
    ):
        raise MultiEquationSemanticRejected("multi_equation_source_changed")
    occurrences = _displayed_image_occurrences(page, group.page_number)
    matches = [
        occurrence
        for occurrence in occurrences
        if occurrence["image_xref"] == saved_xref
        and occurrence["image_index"] == group.image_index
        and occurrence["occurrence_ordinal"] == group.occurrence_ordinal
        and all(
            abs(actual - wanted) <= 1e-6
            for actual, wanted in zip(occurrence["bbox"], original.parent_bbox)
        )
    ]
    if len(matches) != 1 or (
        require_original_xref
        and matches[0]["occurrence_id"] != group.parent_occurrence_id
    ):
        raise MultiEquationSemanticRejected("multi_equation_occurrence_changed")
    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            if image.size != (group.source_width, group.source_height):
                raise MultiEquationSemanticRejected(
                    "multi_equation_source_dimensions_changed"
                )
            for child in group.children:
                crop = image.crop(child.pixel_bbox)
                try:
                    digest = child_pixel_payload_sha256(
                        crop.mode, crop.size, crop.tobytes()
                    )
                finally:
                    crop.close()
                if digest != child.crop_pixel_sha256:
                    raise MultiEquationSemanticRejected(
                        "multi_equation_child_crop_changed"
                    )
    except MultiEquationSemanticRejected:
        raise
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_source_decode_failed"
        ) from exc
    return matches[0], source, transform


def _validate_equation_group_only_source(
    source: bytes, group: MultiEquationRegionGroupV1
) -> None:
    """Refuse to artifact meaningful raster pixels outside the proven children."""

    try:
        with Image.open(BytesIO(source)) as image:
            image.load()
            grayscale = image.convert("L")
            ink = grayscale.point(lambda value: 255 if value < 245 else 0)
            for child in group.children:
                ink.paste(0, child.pixel_bbox)
            outside_ink = ink.getbbox()
            ink.close()
            grayscale.close()
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_source_decode_failed"
        ) from exc
    if outside_ink is not None:
        raise MultiEquationSemanticRejected(
            "multi_equation_nonformula_content_unsupported"
        )


def _clip_for_owner(
    group: MultiEquationRegionGroupV1,
    owner: MultiEquationSemanticOwnerV1,
) -> tuple[float, float, float, float]:
    px0, py0, px1, py1 = owner.pixel_bbox
    return (
        px0 / float(group.source_width),
        1.0 - (py1 / float(group.source_height)),
        (px1 - px0) / float(group.source_width),
        (py1 - py0) / float(group.source_height),
    )


def associate_multi_equation_formulas(
    pdf: Any,
    fitz_doc: Any,
    group: Any,
    owners: Any,
) -> MultiEquationAssociation:
    """Artifact one shared raster and create every required ordered Formula."""

    from src.education.remediation import content_tagger_v2 as tagger
    from src.education.remediation.pdf_structure import PDFStructureTree

    try:
        checked_group, checked_owners = validate_multi_equation_semantic_plan(
            group, owners
        )
        _validate_owner_payloads(checked_owners)
        working_occurrence, source, transform = _source_occurrence(
            fitz_doc, checked_group, require_original_xref=False
        )
        working_xref = int(working_occurrence["image_xref"])
        _validate_owner_sources(source, checked_group, checked_owners)
        tagger.preflight_scanned_region_render_budget(
            fitz_doc, (checked_group.page_number,)
        )
        before_render = tuple(
            tagger._page_render_signature(fitz_doc, checked_group.page_number, dpi)
            for dpi in tagger._REGION_RENDER_DPI
        )
        page = pdf.pages[checked_group.page_number - 1]
        resource_name, image = tagger._region_resource_binding(page, working_xref)
        tagger._validate_opaque_region_image(page, image, checked_group.children[0])
        ops = list(pikepdf.parse_content_stream(page))
        if any(str(op.operator) in {"BMC", "BDC", "EMC"} for op in ops):
            raise MultiEquationSemanticRejected(
                "multi_equation_existing_semantics_unsupported"
            )
        target_index, target_close_index, matrix = tagger._region_target_draw(
            page,
            ops,
            image_xref=working_xref,
            resource_name=resource_name,
            expected_transform=transform,
        )
        tagger._region_draw_ownership(page, ops, working_xref, artifact_required=False)
        union_bbox = tuple(
            float(value) for value in _union_bbox(checked_group, "pdf_bbox")
        )
        ocr_plan = tagger._region_ocr_form_plan(
            pdf,
            page,
            fitz_doc[checked_group.page_number - 1],
            ops,
            image_resource_name=resource_name,
            region_bbox=union_bbox,
        )
        if ocr_plan is None:
            _validate_equation_group_only_source(source, checked_group)
            tagger._validate_equation_only_page_text(
                fitz_doc, checked_group.page_number, union_bbox
            )
        if Name.StructTreeRoot not in pdf.Root:
            raise MultiEquationSemanticRejected("multi_equation_structure_tree_missing")
        struct_root = pdf.Root[Name.StructTreeRoot]
        if struct_root.get(Name.ParentTree) is None:
            raise MultiEquationSemanticRejected("multi_equation_parent_tree_missing")
        parent_tree, entries = tagger._number_tree_entries(struct_root)
        existing_struct_parent = page.obj.get(Name.StructParents)
        if existing_struct_parent is None:
            used_keys = {key for key, _ in entries}
            struct_parent = 0
            while struct_parent in used_keys:
                struct_parent += 1
        else:
            struct_parent = int(existing_struct_parent)
        page_entry = next(
            (value for key, value in entries if key == struct_parent), None
        )
        if page_entry is not None and not isinstance(page_entry, Array):
            raise MultiEquationSemanticRejected("multi_equation_parent_tree_collision")
        page_array = page_entry if page_entry is not None else Array([])
        used_mcids: set[int] = set()
        for op in ops:
            if str(op.operator) == "BDC" and len(op.operands) == 2:
                try:
                    used_mcids.add(int(op.operands[1][Name.MCID]))
                except Exception:
                    continue
        next_mcid = max(used_mcids | {len(page_array) - 1}, default=-1) + 1
        mcids = tuple(range(next_mcid, next_mcid + len(checked_owners)))
        if any(
            mcid < len(page_array) and page_array[mcid] is not None for mcid in mcids
        ):
            raise MultiEquationSemanticRejected("multi_equation_mcid_collision")

        structure = PDFStructureTree(pdf)
        formulas = []
        associated_owners = []
        for owner, mcid in zip(checked_owners, mcids):
            formula_bbox = _formula_bbox_for_owner(checked_group, owner)
            formula = structure.create_formula_element(
                page_num=checked_group.page_number,
                alt_text=owner.semantic_output.alt_text,
                mathml_string=owner.semantic_output.mathml,
                bbox=formula_bbox,
                mcid=mcid,
            )
            formulas.append(formula)
            associated_owners.append(
                MultiEquationOwnerAssociation(
                    ordinal=owner.ordinal,
                    region_ids=owner.region_ids,
                    mcid=mcid,
                    mathml_sha256=owner.semantic_output.mathml_sha256,
                    alt_text_sha256=hashlib.sha256(
                        owner.semantic_output.alt_text.encode("utf-8")
                    ).hexdigest(),
                    formula_bbox=formula_bbox,
                )
            )

        ocr_struct_parent = -1
        ocr_group_owners: list[tuple[str, int]] = []
        ocr_before_mcids: list[int] = []
        ocr_after_mcids: list[int] = []
        ocr_payload_sha256 = ""
        ocr_font_sha256 = ""
        page_text_sha256 = ""
        structure_sequence: list[Any] = []
        if ocr_plan is not None:
            ocr_payload_sha256 = tagger._region_ocr_payload_signature(
                list(pikepdf.parse_content_stream(ocr_plan.form))
            )
            ocr_font_sha256 = tagger._region_ocr_font_signature(ocr_plan.form)
            page_text_sha256 = hashlib.sha256(
                fitz_doc[checked_group.page_number - 1].get_text("text").encode("utf-8")
            ).hexdigest()
            used_keys = {key for key, _ in entries} | {struct_parent}
            ocr_struct_parent = 0
            while ocr_struct_parent in used_keys:
                ocr_struct_parent += 1
            ocr_plan.form[Name.StructParents] = ocr_struct_parent
            form_array = Array([])
            first_equation = next(
                index
                for index, text_group in enumerate(ocr_plan.groups)
                if text_group.owner == "/Artifact"
            )
            inserted_formulas = False
            next_form_mcid = 0
            parent = tagger._region_structure_parent(pdf)
            for group_index, text_group in enumerate(ocr_plan.groups):
                if text_group.owner == "/Artifact":
                    ocr_group_owners.append(("/Artifact", -1))
                    if not inserted_formulas:
                        structure_sequence.extend(formulas)
                        inserted_formulas = True
                    continue
                group_mcid = next_form_mcid
                next_form_mcid += 1
                mcr = Dictionary(
                    {
                        "/Type": Name("/MCR"),
                        "/Pg": page.obj,
                        "/Stm": ocr_plan.form,
                        "/MCID": group_mcid,
                    }
                )
                paragraph = pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name.StructElem,
                            "/S": Name("/P"),
                            "/P": parent,
                            "/Pg": page.obj,
                            "/K": mcr,
                        }
                    )
                )
                while len(form_array) <= group_mcid:
                    form_array.append(None)
                form_array[group_mcid] = paragraph
                structure_sequence.append(paragraph)
                ocr_group_owners.append(("/P", group_mcid))
                if group_index < first_equation:
                    ocr_before_mcids.append(group_mcid)
                else:
                    ocr_after_mcids.append(group_mcid)
            if not inserted_formulas:
                raise MultiEquationSemanticRejected("multi_equation_ocr_group_missing")
            tagger._set_number_tree_value(
                parent_tree,
                ocr_struct_parent,
                pdf.make_indirect(form_array),
            )
        else:
            structure_sequence.extend(formulas)
        tagger._append_region_structure_sequence(
            pdf, structure_sequence, checked_group.page_number
        )
        if existing_struct_parent is None:
            page.obj[Name.StructParents] = struct_parent
        for mcid, formula in zip(mcids, formulas):
            while len(page_array) <= mcid:
                page_array.append(None)
            page_array[mcid] = formula
        if page_entry is None:
            page_array = pdf.make_indirect(page_array)
        tagger._set_number_tree_value(parent_tree, struct_parent, page_array)

        duplicates = []
        for owner, mcid in zip(checked_owners, mcids):
            duplicates.extend(
                [
                    pikepdf.ContentStreamInstruction(
                        [Name("/Formula"), Dictionary({"/MCID": mcid})],
                        Operator("BDC"),
                    ),
                    pikepdf.ContentStreamInstruction([], Operator("q")),
                    pikepdf.ContentStreamInstruction(list(matrix), Operator("cm")),
                    pikepdf.ContentStreamInstruction(
                        list(_clip_for_owner(checked_group, owner)), Operator("re")
                    ),
                    pikepdf.ContentStreamInstruction([], Operator("W")),
                    pikepdf.ContentStreamInstruction([], Operator("n")),
                    pikepdf.ContentStreamInstruction(
                        [Name(resource_name)], Operator("Do")
                    ),
                    pikepdf.ContentStreamInstruction([], Operator("Q")),
                    pikepdf.ContentStreamInstruction([], Operator("EMC")),
                ]
            )
        if ocr_plan is not None:
            form_ops = list(pikepdf.parse_content_stream(ocr_plan.form))
            for text_group, owner in reversed(
                list(zip(ocr_plan.groups, ocr_group_owners))
            ):
                opening = (
                    pikepdf.ContentStreamInstruction(
                        [Name("/Artifact")], Operator("BMC")
                    )
                    if owner[0] == "/Artifact"
                    else pikepdf.ContentStreamInstruction(
                        [Name("/P"), Dictionary({"/MCID": owner[1]})],
                        Operator("BDC"),
                    )
                )
                form_ops.insert(
                    text_group.end + 1,
                    pikepdf.ContentStreamInstruction([], Operator("EMC")),
                )
                form_ops.insert(text_group.start, opening)
            ocr_plan.form.write(pikepdf.unparse_content_stream(form_ops))

        target_start_index = target_index - 2
        new_ops = [
            *ops[:target_start_index],
            pikepdf.ContentStreamInstruction([Name("/Artifact")], Operator("BMC")),
            *ops[target_start_index : target_close_index + 1],
            pikepdf.ContentStreamInstruction([], Operator("EMC")),
            *duplicates,
            *ops[target_close_index + 1 :],
        ]
        page.obj[Name.Contents] = pdf.make_stream(
            pikepdf.unparse_content_stream(new_ops)
        )
        return MultiEquationAssociation(
            page_number=checked_group.page_number,
            image_xref=working_xref,
            resource_name=resource_name,
            struct_parent=struct_parent,
            owners=tuple(associated_owners),
            render_signatures=before_render,
            ocr_resource_name=ocr_plan.resource_name if ocr_plan is not None else "",
            ocr_struct_parent=ocr_struct_parent,
            ocr_group_owners=tuple(ocr_group_owners),
            ocr_before_mcids=tuple(ocr_before_mcids),
            ocr_after_mcids=tuple(ocr_after_mcids),
            ocr_payload_sha256=ocr_payload_sha256,
            ocr_font_sha256=ocr_font_sha256,
            page_text_sha256=page_text_sha256,
        )
    except MultiEquationSemanticRejected:
        raise
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_association_rejected"
        ) from exc


def _formula_elements(pdf: Any, page: Any, mcids: tuple[int, ...]) -> list[Any]:
    from src.education.remediation import content_tagger_v2 as tagger

    formulas: list[Any] = []
    root_kids = pdf.Root[Name.StructTreeRoot].get(Name.K)
    roots = (
        list(root_kids)
        if isinstance(root_kids, Array)
        else ([root_kids] if root_kids else [])
    )
    for root in roots:
        tagger._collect_structure_elements_by_tag(root, formulas, "/Formula")
    by_mcid: dict[int, list[Any]] = {mcid: [] for mcid in mcids}
    for formula in formulas:
        mcr = formula.get(Name.K)
        try:
            mcid = int(mcr.get(Name.MCID, -1))
        except Exception:
            continue
        if (
            mcid in by_mcid
            and hasattr(mcr, "keys")
            and str(mcr.get(Name.Type, "")) == "/MCR"
            and tuple(formula.get(Name.Pg).objgen) == tuple(page.obj.objgen)
        ):
            by_mcid[mcid].append(formula)
    if any(len(by_mcid[mcid]) != 1 for mcid in mcids):
        raise MultiEquationSemanticRejected("multi_equation_formula_not_unique")
    return [by_mcid[mcid][0] for mcid in mcids]


def _verify_multi_equation_ocr(
    pdf: Any,
    page: Any,
    expected: MultiEquationAssociation,
    formulas: list[Any],
) -> None:
    """Prove Form-internal owners and formula insertion order after save."""

    from src.education.remediation import content_tagger_v2 as tagger

    if not expected.ocr_resource_name:
        return
    resources = page.obj.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if resources is not None else None
    form = (
        xobjects.get(Name(expected.ocr_resource_name)) if xobjects is not None else None
    )
    if (
        form is None
        or str(form.get(Name.Subtype, "")) != "/Form"
        or int(form.get(Name.StructParents, -1)) != expected.ocr_struct_parent
    ):
        raise MultiEquationSemanticRejected("multi_equation_ocr_form_changed")
    bindings, draws = tagger._document_xobject_usage(pdf, tuple(form.objgen))
    if bindings != 1 or draws != 1:
        raise MultiEquationSemanticRejected("multi_equation_ocr_form_reused")
    ops = list(pikepdf.parse_content_stream(form))
    if (
        tagger._region_ocr_payload_signature(ops) != expected.ocr_payload_sha256
        or tagger._region_ocr_font_signature(form) != expected.ocr_font_sha256
    ):
        raise MultiEquationSemanticRejected("multi_equation_ocr_payload_changed")
    stack: list[tuple[str, int]] = []
    group_owner: Optional[tuple[str, int]] = None
    owners: list[tuple[str, int]] = []
    render_mode: Optional[int] = None
    allowed = {
        "BMC",
        "BDC",
        "EMC",
        "BT",
        "ET",
        "Tf",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Ts",
        "Tr",
        "Tj",
        "TJ",
        "'",
        '"',
        "q",
        "Q",
        "cm",
        "g",
        "G",
        "rg",
        "RG",
    }
    for op in ops:
        operator = str(op.operator)
        if operator not in allowed:
            raise MultiEquationSemanticRejected("multi_equation_ocr_grammar_changed")
        if operator in {"BMC", "BDC"}:
            tag = str(op.operands[0]) if op.operands else ""
            mcid = -1
            if operator == "BDC":
                try:
                    mcid = int(op.operands[1][Name.MCID])
                except Exception as exc:
                    raise MultiEquationSemanticRejected(
                        "multi_equation_ocr_mcid_invalid"
                    ) from exc
            stack.append((tag, mcid))
        elif operator == "EMC":
            if not stack:
                raise MultiEquationSemanticRejected(
                    "multi_equation_ocr_marked_content_unbalanced"
                )
            stack.pop()
        elif operator == "BT":
            if group_owner is not None or len(stack) != 1:
                raise MultiEquationSemanticRejected(
                    "multi_equation_ocr_owner_ambiguous"
                )
            group_owner = stack[0]
            render_mode = None
        elif operator == "Tr":
            try:
                render_mode = int(op.operands[0])
            except Exception as exc:
                raise MultiEquationSemanticRejected(
                    "multi_equation_ocr_render_mode_invalid"
                ) from exc
        elif operator == "ET":
            if (
                group_owner is None
                or len(stack) != 1
                or stack[0] != group_owner
                or render_mode != 3
            ):
                raise MultiEquationSemanticRejected(
                    "multi_equation_ocr_owner_ambiguous"
                )
            owners.append(group_owner)
            group_owner = None
    if stack or group_owner is not None or tuple(owners) != expected.ocr_group_owners:
        raise MultiEquationSemanticRejected("multi_equation_ocr_owners_changed")

    _, entries = tagger._number_tree_entries(pdf.Root[Name.StructTreeRoot])
    form_array = next(
        (value for key, value in entries if key == expected.ocr_struct_parent),
        None,
    )
    p_by_mcid: dict[int, Any] = {}
    for owner in expected.ocr_group_owners:
        if owner[0] != "/P":
            continue
        group_mcid = owner[1]
        if (
            not isinstance(form_array, Array)
            or group_mcid >= len(form_array)
            or not hasattr(form_array[group_mcid], "keys")
        ):
            raise MultiEquationSemanticRejected(
                "multi_equation_ocr_parent_tree_mismatch"
            )
        paragraph = form_array[group_mcid]
        mcr = paragraph.get(Name.K)
        if (
            str(paragraph.get(Name.S, "")) != "/P"
            or not hasattr(mcr, "keys")
            or str(mcr.get(Name.Type, "")) != "/MCR"
            or int(mcr.get(Name.MCID, -1)) != group_mcid
            or tuple(mcr.get(Name.Pg).objgen) != tuple(page.obj.objgen)
            or tuple(mcr.get(Name("/Stm")).objgen) != tuple(form.objgen)
        ):
            raise MultiEquationSemanticRejected("multi_equation_ocr_mcr_mismatch")
        p_by_mcid[group_mcid] = paragraph
    parent = formulas[0].get(Name.P)
    if any(
        tuple(formula.get(Name.P).objgen) != tuple(parent.objgen)
        for formula in formulas
    ):
        raise MultiEquationSemanticRejected("multi_equation_structure_order_changed")
    siblings_value = parent.get(Name.K)
    siblings = (
        list(siblings_value)
        if isinstance(siblings_value, Array)
        else ([siblings_value] if siblings_value is not None else [])
    )
    wanted = [p_by_mcid[value] for value in expected.ocr_before_mcids]
    wanted.extend(formulas)
    wanted.extend(p_by_mcid[value] for value in expected.ocr_after_mcids)
    wanted_ids = [tuple(value.objgen) for value in wanted]
    sibling_ids = [
        tuple(value.objgen) if hasattr(value, "objgen") else None for value in siblings
    ]
    matches = [
        index
        for index in range(0, len(sibling_ids) - len(wanted_ids) + 1)
        if sibling_ids[index : index + len(wanted_ids)] == wanted_ids
    ]
    if len(matches) != 1:
        raise MultiEquationSemanticRejected("multi_equation_ocr_reading_order_changed")


def verify_multi_equation_formulas(
    path: str | Path,
    group: Any,
    owners: Any,
    expected: MultiEquationAssociation,
) -> MultiEquationSavedEvidenceV1:
    """Reopen and prove the complete source, structure, semantics, and pixels."""

    from src.education.remediation import content_tagger_v2 as tagger

    try:
        checked_group, checked_owners = validate_multi_equation_semantic_plan(
            group, owners
        )
        if (
            expected.page_number != checked_group.page_number
            or len(expected.owners) != len(checked_owners)
            or tuple(owner.ordinal for owner in expected.owners)
            != tuple(owner.ordinal for owner in checked_owners)
            or any(
                association.mcid < 0
                or association.mcid > 25_000_000
                or len(association.formula_bbox) != 4
                or any(
                    not math.isfinite(float(value))
                    for value in association.formula_bbox
                )
                or any(
                    abs(actual - wanted) > 1e-6
                    for actual, wanted in zip(
                        association.formula_bbox,
                        _formula_bbox_for_owner(checked_group, owner),
                    )
                )
                for owner, association in zip(checked_owners, expected.owners)
            )
        ):
            raise MultiEquationSemanticRejected(
                "multi_equation_expected_association_changed"
            )
        with fitz.open(str(path)) as fitz_doc, pikepdf.open(str(path)) as pdf:
            saved_occurrence, saved_source, saved_transform = _source_occurrence(
                fitz_doc, checked_group, require_original_xref=False
            )
            _validate_owner_sources(saved_source, checked_group, checked_owners)
            saved_xref = int(saved_occurrence["image_xref"])
            page = pdf.pages[checked_group.page_number - 1]
            resource_name, _ = tagger._region_resource_binding(page, saved_xref)
            if resource_name != expected.resource_name:
                raise MultiEquationSemanticRejected("multi_equation_resource_changed")
            expected_mcids = tuple(owner.mcid for owner in expected.owners)
            if len(set(expected_mcids)) != len(expected_mcids):
                raise MultiEquationSemanticRejected("multi_equation_mcid_duplicated")
            formulas = _formula_elements(pdf, page, expected_mcids)
            parent = formulas[0].get(Name.P)
            siblings_value = parent.get(Name.K) if hasattr(parent, "keys") else None
            siblings = (
                list(siblings_value)
                if isinstance(siblings_value, Array)
                else ([siblings_value] if siblings_value is not None else [])
            )
            formula_ids = [tuple(formula.objgen) for formula in formulas]
            sibling_ids = [
                tuple(sibling.objgen) if hasattr(sibling, "objgen") else None
                for sibling in siblings
            ]
            starts = [
                index
                for index in range(0, len(sibling_ids) - len(formula_ids) + 1)
                if sibling_ids[index : index + len(formula_ids)] == formula_ids
            ]
            if len(starts) != 1:
                raise MultiEquationSemanticRejected(
                    "multi_equation_structure_order_changed"
                )
            _, entries = tagger._number_tree_entries(pdf.Root[Name.StructTreeRoot])
            page_array = next(
                (value for key, value in entries if key == expected.struct_parent),
                None,
            )
            if (
                not isinstance(page_array, Array)
                or int(page.obj.get(Name.StructParents, -1)) != expected.struct_parent
            ):
                raise MultiEquationSemanticRejected(
                    "multi_equation_parent_tree_changed"
                )
            saved_owners = []
            for owner, association, formula in zip(
                checked_owners, expected.owners, formulas
            ):
                if (
                    association.region_ids != owner.region_ids
                    or association.mathml_sha256 != owner.semantic_output.mathml_sha256
                    or association.alt_text_sha256
                    != hashlib.sha256(
                        owner.semantic_output.alt_text.encode("utf-8")
                    ).hexdigest()
                    or str(formula.get(Name.Alt, "")) != owner.semantic_output.alt_text
                ):
                    raise MultiEquationSemanticRejected(
                        "multi_equation_semantics_changed"
                    )
                mcr = formula.get(Name.K)
                attributes = formula.get(Name.A)
                bbox = (
                    attributes.get(Name("/BBox"))
                    if hasattr(attributes, "keys")
                    else None
                )
                backlinks = sum(
                    1
                    for sibling in siblings
                    if hasattr(sibling, "objgen")
                    and tuple(sibling.objgen) == tuple(formula.objgen)
                )
                parent_tree_count = sum(
                    1
                    for item in page_array
                    if item is not None
                    and hasattr(item, "objgen")
                    and tuple(item.objgen) == tuple(formula.objgen)
                )
                if (
                    not hasattr(mcr, "keys")
                    or str(mcr.get(Name.Type, "")) != "/MCR"
                    or int(mcr.get(Name.MCID, -1)) != association.mcid
                    or tuple(mcr.get(Name.Pg).objgen) != tuple(page.obj.objgen)
                    or backlinks != 1
                    or parent_tree_count != 1
                    or association.mcid >= len(page_array)
                    or tuple(page_array[association.mcid].objgen)
                    != tuple(formula.objgen)
                    or not isinstance(bbox, Array)
                    or len(bbox) != 4
                    or any(
                        abs(float(actual) - wanted) > 1e-6
                        for actual, wanted in zip(bbox, association.formula_bbox)
                    )
                ):
                    raise MultiEquationSemanticRejected(
                        "multi_equation_formula_contract_changed"
                    )
                tagger._verify_region_global_reading_order(
                    pdf, formula, checked_group.page_number
                )
                af = formula.get(Name("/AF"))
                if not isinstance(af, Array) or len(af) != 1:
                    raise MultiEquationSemanticRejected(
                        "multi_equation_attachment_changed"
                    )
                filespec = af[0]
                embedded = filespec.get(Name("/EF")).get(Name.F)
                payload = embedded.read_bytes() if embedded is not None else b""
                params = embedded.get(Name("/Params")) if embedded is not None else None
                checksum = (
                    params.get(Name("/CheckSum")) if hasattr(params, "keys") else None
                )
                attachment_sha256 = hashlib.sha256(payload).hexdigest()
                if (
                    str(filespec.get(Name.Type, "")) != "/Filespec"
                    or str(filespec.get(Name("/AFRelationship"), "")) != "/Supplement"
                    or embedded is None
                    or str(embedded.get(Name.Type, "")) != "/EmbeddedFile"
                    or str(embedded.get(Name.Subtype, ""))
                    != "/application#2Fmathml+xml"
                    or len(payload) > 65_536
                    or payload != owner.semantic_output.mathml.encode("utf-8")
                    or not hasattr(params, "keys")
                    or int(params.get(Name("/Size"), -1)) != len(payload)
                    or checksum is None
                    or bytes(checksum)
                    != hashlib.md5(payload, usedforsecurity=False).digest()
                    or attachment_sha256 != association.mathml_sha256
                ):
                    raise MultiEquationSemanticRejected("multi_equation_mathml_changed")
                saved_owners.append(
                    MultiEquationSavedOwnerV1(
                        ordinal=owner.ordinal,
                        region_ids=owner.region_ids,
                        struct_parent=expected.struct_parent,
                        mcid=association.mcid,
                        formula_bbox=tuple(float(value) for value in bbox),
                        mathml_sha256=association.mathml_sha256,
                        alt_text_sha256=association.alt_text_sha256,
                        attachment_sha256=attachment_sha256,
                        backlink_count=1,
                        parent_tree_count=1,
                    )
                )
            _verify_multi_equation_ocr(pdf, page, expected, formulas)
            ops = list(pikepdf.parse_content_stream(page))
            tagger._region_target_draw(
                page,
                ops,
                image_xref=saved_xref,
                resource_name=resource_name,
                expected_transform=saved_transform,
                artifact_original_allowed=True,
            )
            allowed_unmarked = (
                frozenset({expected.ocr_resource_name})
                if expected.ocr_resource_name
                else frozenset()
            )
            artifact_count, formula_mcids = tagger._region_draw_ownership(
                page,
                ops,
                saved_xref,
                allowed_unmarked_do_names=allowed_unmarked,
            )
            if artifact_count != 1 or tuple(formula_mcids) != expected_mcids:
                raise MultiEquationSemanticRejected(
                    "multi_equation_draw_ownership_changed"
                )
            for owner, association in zip(checked_owners, expected.owners):
                wanted_clip = _clip_for_owner(checked_group, owner)
                matched = 0
                for index in range(0, len(ops) - 8):
                    sequence = ops[index : index + 9]
                    if [str(item.operator) for item in sequence] != [
                        "BDC",
                        "q",
                        "cm",
                        "re",
                        "W",
                        "n",
                        "Do",
                        "Q",
                        "EMC",
                    ]:
                        continue
                    try:
                        exact = (
                            str(sequence[0].operands[0]) == "/Formula"
                            and int(sequence[0].operands[1][Name.MCID])
                            == association.mcid
                            and str(sequence[6].operands[0]) == resource_name
                            and all(
                                abs(float(actual) - wanted) <= 1e-6
                                for actual, wanted in zip(
                                    sequence[2].operands, saved_transform
                                )
                            )
                            and all(
                                abs(float(actual) - wanted) <= 1e-6
                                for actual, wanted in zip(
                                    sequence[3].operands, wanted_clip
                                )
                            )
                        )
                    except Exception:
                        exact = False
                    matched += int(exact)
                if matched != 1:
                    raise MultiEquationSemanticRejected(
                        "multi_equation_clip_sequence_changed"
                    )
            if (
                expected.ocr_resource_name
                and hashlib.sha256(
                    fitz_doc[checked_group.page_number - 1]
                    .get_text("text")
                    .encode("utf-8")
                ).hexdigest()
                != expected.page_text_sha256
            ):
                raise MultiEquationSemanticRejected("multi_equation_ocr_text_changed")
            after_render = tuple(
                tagger._page_render_signature(fitz_doc, checked_group.page_number, dpi)
                for dpi in tagger._REGION_RENDER_DPI
            )
            if after_render != expected.render_signatures:
                raise MultiEquationSemanticRejected("multi_equation_render_changed")
        return MultiEquationSavedEvidenceV1(
            evidence_kind="multi_equation_saved_v1",
            passed=True,
            saved_file_sha256=_file_sha256(Path(path)),
            page_number=checked_group.page_number,
            parent_occurrence_id=checked_group.parent_occurrence_id,
            saved_parent_occurrence_id=str(saved_occurrence["occurrence_id"]),
            image_xref=saved_xref,
            image_index=int(saved_occurrence["image_index"]),
            occurrence_ordinal=int(saved_occurrence["occurrence_ordinal"]),
            source_sha256=checked_group.source_sha256,
            parent_bbox=tuple(float(value) for value in saved_occurrence["bbox"]),
            transform=tuple(float(value) for value in saved_transform),
            disposition=checked_group.disposition,
            original_artifact_count=1,
            owners=tuple(saved_owners),
            render_signatures=after_render,
        )
    except MultiEquationSemanticRejected:
        raise
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_saved_verification_rejected"
        ) from exc


def associate_multi_equation_pdf(
    path: str | Path,
    group: Any,
    owners: Any,
) -> MultiEquationAssociation:
    """Associate one group in a disposable PDF candidate and serialize it."""

    from src.education.remediation.pdf_structure import PDFStructureTree

    candidate = Path(path)
    with (
        fitz.open(str(candidate)) as fitz_doc,
        pikepdf.open(str(candidate), allow_overwriting_input=True) as pdf,
    ):
        if Name.StructTreeRoot not in pdf.Root:
            PDFStructureTree(pdf)
        association = associate_multi_equation_formulas(pdf, fitz_doc, group, owners)
        pdf.save(str(candidate))
    return association


def remediate_multi_equation_pdf(
    input_path: str | Path,
    output_path: str | Path,
    group: Any,
    owners: Any,
) -> MultiEquationSemanticContractV1:
    """Run the production association and saved verifier as one transaction."""

    association: Optional[MultiEquationAssociation] = None

    def associate(
        candidate: Path,
        checked_group: MultiEquationRegionGroupV1,
        checked_owners: tuple[MultiEquationSemanticOwnerV1, ...],
    ) -> None:
        nonlocal association
        association = associate_multi_equation_pdf(
            candidate, checked_group, checked_owners
        )

    def verify_saved(
        candidate: Path,
        checked_group: MultiEquationRegionGroupV1,
        checked_owners: tuple[MultiEquationSemanticOwnerV1, ...],
    ) -> MultiEquationSavedEvidenceV1:
        if association is None:
            raise MultiEquationSemanticRejected(
                "multi_equation_association_result_missing"
            )
        return verify_multi_equation_formulas(
            candidate, checked_group, checked_owners, association
        )

    return commit_multi_equation_transaction(
        input_path,
        output_path,
        group,
        owners,
        associate=associate,
        verify_saved=verify_saved,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_TRANSACTION_BYTES:
                raise MultiEquationSemanticRejected(
                    "multi_equation_transaction_byte_limit"
                )
            digest.update(chunk)
    return digest.hexdigest()


def commit_multi_equation_transaction(
    input_path: str | Path,
    output_path: str | Path,
    group: Any,
    owners: Any,
    *,
    associate: Callable[
        [Path, MultiEquationRegionGroupV1, tuple[MultiEquationSemanticOwnerV1, ...]],
        None,
    ],
    verify_saved: Callable[
        [Path, MultiEquationRegionGroupV1, tuple[MultiEquationSemanticOwnerV1, ...]],
        MultiEquationSavedEvidenceV1,
    ],
) -> MultiEquationSemanticContractV1:
    """Commit only a fully reverse-verified disposable PDF transaction."""

    try:
        validated_group, validated_owners = validate_multi_equation_semantic_plan(
            group, owners
        )
    except Exception as exc:
        raise MultiEquationSemanticRejected("multi_equation_plan_invalid") from exc
    source = Path(input_path)
    destination = Path(output_path)
    if not source.is_file() or not destination.parent.is_dir():
        raise MultiEquationSemanticRejected("multi_equation_transaction_path_invalid")
    source_sha256 = _file_sha256(source)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{destination.name}.multi-equation-",
        suffix=".pdf",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    try:
        shutil.copyfile(source, candidate)
        if (
            _file_sha256(source) != source_sha256
            or _file_sha256(candidate) != source_sha256
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        associate(candidate, validated_group, validated_owners)
        saved = MultiEquationSavedEvidenceV1.model_validate(
            verify_saved(candidate, validated_group, validated_owners)
        )
        if saved.saved_file_sha256 != _file_sha256(candidate):
            raise MultiEquationSemanticRejected("multi_equation_saved_digest_mismatch")
        contract = build_multi_equation_semantic_contract(
            group=validated_group,
            owners=validated_owners,
            saved_evidence=saved,
        )
        os.replace(candidate, destination)
        return contract
    except MultiEquationSemanticRejected:
        raise
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_transaction_rejected"
        ) from exc
    finally:
        candidate.unlink(missing_ok=True)


__all__ = [
    "MultiEquationAssociation",
    "MultiEquationOwnerAssociation",
    "MultiEquationSemanticPlanner",
    "MultiEquationSemanticRejected",
    "ValidatedMultiEquationRaster",
    "associate_multi_equation_formulas",
    "associate_multi_equation_pdf",
    "commit_multi_equation_transaction",
    "extract_multi_equation_child_raster",
    "extract_whole_system_raster",
    "remediate_multi_equation_pdf",
    "verify_multi_equation_formulas",
]
