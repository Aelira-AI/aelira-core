"""Contract and detector probes for exact PDF vector-equation clusters."""

from __future__ import annotations

import hashlib
from io import BytesIO

import fitz
import pytest
from PIL import Image
from pydantic import ValidationError

from src.education.vector_equation_cluster import (
    VectorObjectIdentityV1,
    VectorOperatorSpanV1,
    VectorResourceIdentityV1,
    build_vector_equation_cluster,
)
from src.education.pdf_checks.vector_equation_cluster_detector import (
    VectorEquationClusterDetector,
)
from src.education.pdf_checks import vector_equation_cluster_detector as detector_module


def _png_bytes() -> bytes:
    image = Image.new("RGB", (24, 12), "white")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _object(number: int, payload: bytes) -> VectorObjectIdentityV1:
    return VectorObjectIdentityV1(
        object_number=number,
        generation=0,
        passive_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _cluster():
    page = _object(3, b"page")
    stream = _object(7, b"stream")
    resource_object = _object(11, b"resource")
    span = VectorOperatorSpanV1(
        stream=stream,
        first_operator=2,
        last_operator=9,
        operator_count=8,
        operators_sha256=hashlib.sha256(b"operators").hexdigest(),
        graphics_state_sha256=hashlib.sha256(b"graphics-state").hexdigest(),
        painted_bbox=(10.0, 20.0, 34.0, 32.0),
    )
    resource = VectorResourceIdentityV1(
        resource_kind="xobject",
        resource_name="/EqGlyphs",
        object_identity=resource_object,
    )
    return build_vector_equation_cluster(
        page_number=1,
        page_object=page,
        content_streams=(stream,),
        operator_spans=(span,),
        resources=(resource,),
        pdf_bbox=(10.0, 20.0, 34.0, 32.0),
        raster_png=_png_bytes(),
        raster_scale=2.0,
    )


def test_contract_binds_page_operators_resources_state_and_raster():
    cluster = _cluster()

    assert cluster.source_kind == "pdf_vector_cluster"
    assert cluster.page_object.object_number == 3
    assert cluster.operator_spans[0].stream == cluster.content_streams[0]
    assert cluster.resources[0].resource_name == "/EqGlyphs"
    assert cluster.raster.width == 24
    assert cluster.raster.height == 12
    assert cluster.raster.png_sha256 == hashlib.sha256(_png_bytes()).hexdigest()
    assert cluster.cluster_id == "eqvector-v1-" + cluster.cluster_sha256[:24]


def test_contract_is_frozen_exact_and_json_roundtrips():
    cluster = _cluster()

    restored = type(cluster).model_validate_json(cluster.model_dump_json())

    assert restored == cluster
    with pytest.raises(ValidationError):
        type(cluster).model_validate({**cluster.model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        cluster.page_number = 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "0" * 64),
        ("cluster_sha256", "0" * 64),
        ("cluster_id", "eqvector-v1-" + "0" * 24),
        ("pdf_bbox", (10.0, 20.0, 33.0, 32.0)),
    ],
)
def test_contract_rejects_identity_or_geometry_tampering(field, value):
    payload = _cluster().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        type(_cluster()).model_validate(payload)


def test_operator_span_rejects_inconsistent_count_and_stream():
    payload = _cluster().model_dump()
    payload["operator_spans"][0]["operator_count"] = 7

    with pytest.raises(ValidationError):
        type(_cluster()).model_validate(payload)

    payload = _cluster().model_dump()
    payload["operator_spans"][0]["stream"]["object_number"] = 99
    with pytest.raises(ValidationError):
        type(_cluster()).model_validate(payload)


def test_raster_bytes_and_pixels_are_both_digest_bound():
    payload = _cluster().model_dump()
    payload["raster"]["png_bytes"] = b"not-a-png"

    with pytest.raises(ValidationError):
        type(_cluster()).model_validate(payload)


def _write_vector_pdf(
    path,
    *,
    cue: bool = True,
    decoration: bool = False,
    overlap: bool = False,
    reverse_paint_order: bool = False,
) -> None:
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    if cue:
        page.insert_text((36, 28), "Equation 1", fontsize=10)
    equation = page.new_shape()
    segments = [
        ((50, 80), (62, 60)),
        ((50, 60), (62, 80)),
        ((75, 66), (95, 66)),
        ((75, 73), (95, 73)),
        ((108, 60), (118, 70)),
        ((128, 60), (118, 70)),
        ((118, 70), (118, 82)),
    ]
    for start, end in reversed(segments) if reverse_paint_order else segments:
        equation.draw_line(start, end)
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()
    if decoration:
        art = page.new_shape()
        art.draw_rect(
            fitz.Rect(88, 62, 145, 95) if overlap else fitz.Rect(170, 90, 220, 140)
        )
        art.finish(color=(0, 0, 0), width=1)
        art.commit()
    document.save(path)
    document.close()


def test_path_equation_discovery_is_stable_and_revalidates(tmp_path):
    path = tmp_path / "vector-equation.pdf"
    _write_vector_pdf(path, decoration=True)
    detector = VectorEquationClusterDetector()

    first = detector.find_clusters(path)
    second = detector.find_clusters(path)

    assert len(first) == 1
    assert first == second
    assert first[0].operator_spans[0].operator_count > 1
    assert first[0].raster.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert detector.revalidate(path, first[0])


def test_uncued_or_overlapped_vector_drawing_fails_closed(tmp_path):
    uncued = tmp_path / "uncued.pdf"
    overlapped = tmp_path / "overlapped.pdf"
    _write_vector_pdf(uncued, cue=False)
    _write_vector_pdf(overlapped, decoration=True, overlap=True)
    detector = VectorEquationClusterDetector()

    assert detector.find_clusters(uncued) == ()
    assert detector.find_clusters(overlapped) == ()


def test_revalidation_rejects_source_tampering(tmp_path):
    original = tmp_path / "original.pdf"
    changed = tmp_path / "changed.pdf"
    _write_vector_pdf(original)
    candidate = VectorEquationClusterDetector().find_clusters(original)[0]
    _write_vector_pdf(changed, decoration=True)

    assert not VectorEquationClusterDetector().revalidate(changed, candidate)


def test_shared_form_resource_occurrences_remain_distinct(tmp_path):
    source = fitz.open()
    source_page = source.new_page(width=100, height=60)
    equation = source_page.new_shape()
    equation.draw_line((10, 36), (22, 16))
    equation.draw_line((10, 16), (22, 36))
    equation.draw_line((35, 22), (55, 22))
    equation.draw_line((35, 29), (55, 29))
    equation.draw_line((68, 16), (78, 26))
    equation.draw_line((88, 16), (78, 26))
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()

    target = fitz.open()
    page = target.new_page(width=300, height=200)
    page.insert_text((24, 24), "Equation 1", fontsize=10)
    page.insert_text((164, 24), "Equation 2", fontsize=10)
    page.show_pdf_page(fitz.Rect(20, 40, 120, 100), source, 0)
    page.show_pdf_page(fitz.Rect(160, 40, 260, 100), source, 0)
    path = tmp_path / "shared-form.pdf"
    target.save(path)
    target.close()
    source.close()

    clusters = VectorEquationClusterDetector().find_clusters(path)

    assert len(clusters) == 2
    assert clusters[0].cluster_id != clusters[1].cluster_id
    assert clusters[0].resources
    assert clusters[1].resources
    assert (
        clusters[0].resources[-1].object_identity
        == clusters[1].resources[-1].object_identity
    )


def test_outlined_glyph_geometry_is_supported(tmp_path):
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    page.insert_text((36, 28), "Formula 1", fontsize=10)
    outline = page.new_shape()
    outline.draw_rect(fitz.Rect(48, 58, 62, 82))
    outline.draw_rect(fitz.Rect(76, 65, 96, 67))
    outline.draw_rect(fitz.Rect(76, 72, 96, 74))
    outline.draw_rect(fitz.Rect(110, 58, 124, 82))
    outline.finish(color=(0, 0, 0), fill=(0, 0, 0), width=1)
    outline.commit()
    path = tmp_path / "outlined-glyphs.pdf"
    document.save(path)
    document.close()

    clusters = VectorEquationClusterDetector().find_clusters(path)

    assert len(clusters) == 1
    assert VectorEquationClusterDetector().revalidate(path, clusters[0])


def test_cued_chart_geometry_remains_negative(tmp_path):
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    page.insert_text((36, 28), "Formula overview", fontsize=10)
    chart = page.new_shape()
    chart.draw_line((50, 60), (150, 60))
    chart.draw_line((50, 70), (150, 70))
    chart.draw_line((50, 45), (50, 120))
    chart.draw_line((80, 80), (80, 120))
    chart.draw_line((110, 75), (110, 120))
    chart.finish(color=(0, 0, 0), width=2)
    chart.commit()
    path = tmp_path / "chart.pdf"
    document.save(path)
    document.close()

    assert VectorEquationClusterDetector().find_clusters(path) == ()


def test_native_text_overlapping_vector_cluster_refuses(tmp_path):
    path = tmp_path / "mixed-overlap.pdf"
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    page.insert_text((36, 28), "Equation 1", fontsize=10)
    page.insert_text((78, 71), "native", fontsize=8)
    equation = page.new_shape()
    equation.draw_line((50, 80), (62, 60))
    equation.draw_line((50, 60), (62, 80))
    equation.draw_line((75, 66), (95, 66))
    equation.draw_line((75, 73), (95, 73))
    equation.draw_line((108, 60), (118, 70))
    equation.draw_line((128, 60), (118, 70))
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()
    document.save(path)
    document.close()

    assert VectorEquationClusterDetector().find_clusters(path) == ()


def test_drawing_budget_and_page_rotation_return_no_partial_result(
    tmp_path, monkeypatch
):
    path = tmp_path / "budget.pdf"
    _write_vector_pdf(path)
    monkeypatch.setattr(detector_module, "MAX_VECTOR_DRAWINGS_PER_PAGE", 0)

    assert VectorEquationClusterDetector().find_clusters(path) == ()

    rotated = tmp_path / "rotated.pdf"
    document = fitz.open(path)
    document[0].set_rotation(90)
    document.save(rotated)
    document.close()
    monkeypatch.setattr(detector_module, "MAX_VECTOR_DRAWINGS_PER_PAGE", 2_000)
    assert VectorEquationClusterDetector().find_clusters(rotated) == ()


def test_clipped_transformed_form_preserves_exact_visible_cluster(tmp_path):
    source = fitz.open()
    source_page = source.new_page(width=160, height=80)
    equation = source_page.new_shape()
    equation.draw_line((10, 45), (22, 25))
    equation.draw_line((10, 25), (22, 45))
    equation.draw_line((35, 31), (55, 31))
    equation.draw_line((35, 38), (55, 38))
    equation.draw_line((68, 25), (78, 35))
    equation.draw_line((88, 25), (78, 35))
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()

    target = fitz.open()
    page = target.new_page(width=260, height=180)
    page.insert_text((24, 24), "Equation 1", fontsize=10)
    page.show_pdf_page(
        fitz.Rect(20, 40, 220, 140),
        source,
        0,
        clip=fitz.Rect(0, 0, 100, 60),
    )
    path = tmp_path / "clipped-form.pdf"
    target.save(path)
    target.close()
    source.close()

    clusters = VectorEquationClusterDetector().find_clusters(path)

    assert len(clusters) == 1
    assert clusters[0].pdf_bbox[2] < 220
    assert VectorEquationClusterDetector().revalidate(path, clusters[0])


def test_nearby_disconnected_drawing_makes_membership_ambiguous(tmp_path):
    path = tmp_path / "ambiguous-neighbor.pdf"
    document = fitz.open()
    page = document.new_page(width=240, height=180)
    page.insert_text((36, 28), "Equation 1", fontsize=10)
    equation = page.new_shape()
    equation.draw_line((50, 80), (62, 60))
    equation.draw_line((50, 60), (62, 80))
    equation.draw_line((75, 66), (95, 66))
    equation.draw_line((75, 73), (95, 73))
    equation.draw_line((108, 60), (118, 70))
    equation.draw_line((128, 60), (118, 70))
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()
    neighbor = page.new_shape()
    neighbor.draw_rect(fitz.Rect(120, 60, 140, 82))
    neighbor.finish(color=(0, 0, 0), width=1)
    neighbor.commit()
    document.save(path)
    document.close()

    assert VectorEquationClusterDetector().find_clusters(path) == ()


def test_unsupported_transparency_and_layer_fail_before_raster():
    drawing = {
        "rect": fitz.Rect(0, 0, 20, 20),
        "items": [("re", fitz.Rect(0, 0, 20, 20), 1)],
        "layer": "",
        "fill_opacity": None,
        "stroke_opacity": 1.0,
    }

    assert detector_module._supported_drawing(drawing)
    assert not detector_module._supported_drawing({**drawing, "stroke_opacity": 0.5})
    assert not detector_module._supported_drawing({**drawing, "layer": "Optional"})


def test_multi_paint_form_resource_is_rejected_as_ambiguous(tmp_path):
    source = fitz.open()
    source_page = source.new_page(width=160, height=80)
    equation = source_page.new_shape()
    equation.draw_line((10, 45), (22, 25))
    equation.draw_line((10, 25), (22, 45))
    equation.draw_line((35, 31), (55, 31))
    equation.draw_line((35, 38), (55, 38))
    equation.draw_line((68, 25), (78, 35))
    equation.draw_line((88, 25), (78, 35))
    equation.finish(color=(0, 0, 0), width=2)
    equation.commit()
    extra = source_page.new_shape()
    extra.draw_rect(fitz.Rect(120, 20, 150, 60))
    extra.finish(color=(0, 0, 0), width=1)
    extra.commit()

    target = fitz.open()
    page = target.new_page(width=260, height=180)
    page.insert_text((24, 24), "Equation 1", fontsize=10)
    page.show_pdf_page(fitz.Rect(20, 40, 220, 140), source, 0)
    path = tmp_path / "multi-paint-form.pdf"
    target.save(path)
    target.close()
    source.close()

    assert VectorEquationClusterDetector().find_clusters(path) == ()


def test_discovery_and_revalidation_leave_source_bytes_unchanged(tmp_path):
    path = tmp_path / "passive.pdf"
    _write_vector_pdf(path)
    before = path.read_bytes()
    detector = VectorEquationClusterDetector()

    candidate = detector.find_clusters(path)[0]
    assert detector.revalidate(path, candidate)

    assert path.read_bytes() == before


def test_operator_paint_order_contributes_to_candidate_identity(tmp_path):
    original = tmp_path / "ordered.pdf"
    reordered = tmp_path / "reordered.pdf"
    _write_vector_pdf(original)
    _write_vector_pdf(reordered, reverse_paint_order=True)
    detector = VectorEquationClusterDetector()

    first = detector.find_clusters(original)[0]
    second = detector.find_clusters(reordered)[0]

    assert (
        first.operator_spans[0].operators_sha256
        != second.operator_spans[0].operators_sha256
    )
    assert first.cluster_id != second.cluster_id
    assert not detector.revalidate(reordered, first)
