"""AI wording must not change identity or hide image loss in saved verification."""

import io
import hashlib
import shutil
from contextlib import nullcontext

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image

from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_image_finding_identity import (
    _ResourceProof,
    bind_missing_image_findings,
    output_image_key,
    preserve_image_pages,
)
from src.education.remediation.pdf_remediator import PdfRemediator

pytestmark = pytest.mark.unit


def _fixture(path, *, alpha=False):
    image = Image.new(
        "RGBA" if alpha else "RGB",
        (8, 6),
        (20, 90, 140, 100) if alpha else (20, 90, 140),
    )
    image.putpixel((0, 0), (250, 10, 40, 255) if alpha else (250, 10, 40))
    png = io.BytesIO()
    image.save(png, format="PNG")
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(
            fitz.Rect(40, 200, 540, 700),
            "Readable course material for accessibility review. " * 30,
        )
        xref = page.insert_image(fitz.Rect(40, 40, 120, 100), stream=png.getvalue())
        page.insert_image(fitz.Rect(160, 40, 240, 100), xref=xref)
        page.insert_image(fitz.Rect(280, 40, 360, 100), xref=xref)
        pdf.save(path)


def _key(issue):
    return issue.category.value, issue.location or "", issue.description


def _findings(path):
    scan = PDFProcessor(generate_alt_text=False, validate_alt_text=False).process_pdf(
        str(path)
    )
    remediator = PdfRemediator(
        str(path),
        scan.issues,
        RemediationConfig(use_ai=False, allow_legacy_nested_ai=False),
    )
    images = [
        issue
        for issue in remediator.issues
        if issue.metadata.get("issue_type") == "missing_alt_text"
    ]
    assert len(images) == 3
    return remediator, images


def _wrapper(image, **changes):
    wrapped = image.model_copy(deep=True)
    wrapped.description = 'AI-Generated Alt Text: "A small blue rectangle."'
    # Historical scans predate the new stable fields.
    wrapped.metadata.pop("issue_type")
    wrapped.metadata.pop("has_alt_text")
    wrapped.metadata.update(changes)
    return wrapped


def _tag(source, output, *, first_only=True, mutate=None):
    with pikepdf.open(source) as pdf:
        page = pdf.pages[0]
        root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot))
        doc = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document, P=root
            )
        )
        owners = []
        operations = []
        index = 0
        for op in pikepdf.parse_content_stream(page):
            if str(op.operator) == "Do":
                if not first_only or index == 0:
                    figure = pdf.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name.StructElem,
                            S=pikepdf.Name.Figure,
                            P=doc,
                            Pg=page.obj,
                            K=index,
                            Alt="A blue rectangle with a red corner.",
                        )
                    )
                    owners.append(figure)
                    operations.append(
                        pikepdf.ContentStreamInstruction(
                            [pikepdf.Name.Figure, pikepdf.Dictionary(MCID=index)],
                            pikepdf.Operator("BDC"),
                        )
                    )
                    operations.append(op)
                    operations.append(
                        pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))
                    )
                else:
                    owners.append(None)
                    operations.append(op)
                index += 1
            else:
                operations.append(op)
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(operations))
        doc.K = pikepdf.Array([owner for owner in owners if owner is not None])
        root.K = pikepdf.Array([doc])
        root.ParentTree = pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array([0, pikepdf.Array(owners)]))
        )
        page.obj.StructParents = 0
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        if mutate:
            mutate(pdf)
        pdf.save(output)


@pytest.mark.parametrize(
    "description",
    [
        'AI-Generated Alt Text: "A blue rectangle."',
        'Chart/Graph detected - Alt: "A chart."',
        'Decorative image detected - use empty alt="" attribute',
        "Image missing alternative text - AI analysis pending",
        "Image missing alternative text",
    ],
)
def test_historical_ai_labels_bind_to_unique_source(description, tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    wrapped = _wrapper(fresh[0])
    wrapped.description = description
    assert bind_missing_image_findings([wrapped], fresh, _key)[wrapped.id].key == _key(
        fresh[0]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"has_alt_text": True},
        {"has_alt_text": None},
        {"issue_type": "alt_text_quality_review"},
        {"page_number": True},
        {"image_index": "0"},
        {"image_xref": 0},
        {"occurrence_ordinal": -1},
        {"bbox": [True, 40, 120, 100]},
        {"bbox": [0, 0, float("nan"), 100]},
        {"occurrence_id": "imgocc-v1-" + "0" * 24},
    ],
)
def test_contradictory_or_forged_source_metadata_cannot_fall_back(changes, tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    with pytest.raises(ValueError, match="uniquely reproduced"):
        bind_missing_image_findings([_wrapper(fresh[0], **changes)], fresh, _key)


def test_duplicate_ids_and_duplicate_source_claims_are_rejected(tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    first = _wrapper(fresh[0])
    with pytest.raises(ValueError, match="Duplicate"):
        bind_missing_image_findings([first, first], fresh, _key)
    second = first.model_copy(deep=True)
    second.id = "different-id-same-draw"
    with pytest.raises(ValueError, match="uniquely reproduced"):
        bind_missing_image_findings([first, second], fresh, _key)


def test_structural_markers_and_xref_renumbering_preserve_correct_draw(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings(
        [_wrapper(image) for image in fresh], fresh, _key
    )
    _tag(source, output)
    saved = preserve_image_pages(str(source), str(output), bindings)
    with fitz.open(output) as pdf:
        occurrences = _displayed_image_occurrences(pdf[0], 1)
    assert fresh[0].metadata["image_xref"] != occurrences[0]["image_xref"]
    assert saved.accessible == frozenset({(1, 0)})
    output_scan = PDFProcessor(generate_alt_text=False).process_pdf(str(output))
    normalized = PdfRemediator(
        str(output),
        output_scan.issues,
        RemediationConfig(use_ai=False, allow_legacy_nested_ai=False),
    ).issues
    missing = [
        issue
        for issue in normalized
        if issue.metadata.get("issue_type") == "missing_alt_text"
    ]
    assert [output_image_key(issue, saved, _key) for issue in missing] == [
        _key(fresh[1]),
        _key(fresh[2]),
    ]


def _change_paint(pdf, mode):
    page = pdf.pages[0]
    ops = list(pikepdf.parse_content_stream(page))
    if mode == "delete_first":
        draw = next(index for index, op in enumerate(ops) if str(op.operator) == "Do")
        del ops[draw]
    elif mode == "reflect":
        index = next(index for index, op in enumerate(ops) if str(op.operator) == "cm")
        args = list(ops[index].operands)
        args[0], args[4] = -args[0], args[4] + args[0]
        ops[index] = pikepdf.ContentStreamInstruction(args, pikepdf.Operator("cm"))
    elif mode == "reorder":
        positions = [index for index, op in enumerate(ops) if str(op.operator) == "cm"]
        ops[positions[0]], ops[positions[1]] = ops[positions[1]], ops[positions[0]]
    elif mode == "clip":
        ops = [
            pikepdf.ContentStreamInstruction([0, 0, 1, 1], pikepdf.Operator("re")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("W")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("n")),
        ] + ops
    elif mode == "opacity":
        page.Resources.ExtGState = pikepdf.Dictionary(Faded=pikepdf.Dictionary(ca=0.1))
        ops.insert(
            0,
            pikepdf.ContentStreamInstruction(
                [pikepdf.Name.Faded], pikepdf.Operator("gs")
            ),
        )
    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))


@pytest.mark.parametrize(
    "mode", ["delete_first", "reflect", "reorder", "clip", "opacity"]
)
def test_image_changes_cannot_be_credited_as_alt_repairs(mode, tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings(
        [_wrapper(image) for image in fresh], fresh, _key
    )
    _tag(source, output, mutate=lambda pdf: _change_paint(pdf, mode))
    with pytest.raises(ValueError, match="paint or rendering dependency changed"):
        preserve_image_pages(str(source), str(output), bindings)


def test_changed_soft_mask_is_rejected_even_with_unchanged_base_image(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source, alpha=True)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings([_wrapper(fresh[0])], fresh, _key)

    def change_mask(pdf):
        image = next(iter(pdf.pages[0].Resources.XObject.items()))[1]
        mask = image.SMask
        mask.write(bytes([0]) * (int(mask.Width) * int(mask.Height)))

    _tag(source, output, mutate=change_mask)
    with pytest.raises(ValueError, match="paint or rendering dependency changed"):
        preserve_image_pages(str(source), str(output), bindings)


def test_unmodified_missing_alt_is_never_semantically_verified(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings(
        [_wrapper(image) for image in fresh], fresh, _key
    )
    shutil.copyfile(source, output)
    saved = preserve_image_pages(str(source), str(output), bindings)
    assert saved.accessible == frozenset()


@pytest.mark.parametrize(
    "mode", ["unchanged", "missing_rule", "wrong_rule", "existing_alt", "wrong_kind"]
)
def test_full_verifier_rejects_forged_image_credit(mode, tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    remediator, fresh = _findings(source)
    # Use the exact plain source label: a rejected image contract must not
    # bypass preservation via the historical description-key fallback.
    image = fresh[0]
    remediator.issues = [image]
    if mode == "unchanged":
        shutil.copyfile(source, output)
    else:
        _tag(source, output)
        if mode == "missing_rule":
            image.metadata.pop("rule")
        elif mode == "wrong_rule":
            image.metadata["rule"] = "WCAG 2.4.2"
        elif mode == "existing_alt":
            image.metadata["has_alt_text"] = True
        else:
            image.metadata["issue_type"] = "alt_text_quality_review"
    remediator._add_fixed_issue(image, "Claimed description", "ai_generated")
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(output)),
    )
    verification = remediator._verify_fixes(str(output))
    assert verification.passed is False
    assert verification.issues_fixed == []
    assert remediator.result.fixed_count == 0


def test_full_verifier_reconciles_ai_source_label_and_credits_owned_alt(
    tmp_path, monkeypatch
):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    remediator, fresh = _findings(source)
    image = _wrapper(fresh[0])
    remediator.issues = [image]
    _tag(source, output)
    remediator._add_fixed_issue(
        image, "A blue rectangle with a red corner.", "ai_generated"
    )
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(output)),
    )
    verification = remediator._verify_fixes(str(output))
    assert verification.issues_fixed == [image.id]
    assert remediator.result.fixed_count == 1
    assert remediator.result.fixed_issues[0].verification_passed is True
    assert remediator.result.fixed_issues[0].needs_review is True
    assert remediator.result.fixed_issues[0].saved_file_verification == {
        "method_version": "pdf-finding-presence-v1",
        "issue_id": image.id,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    assert remediator.result.score_verification_reason is None


@pytest.mark.parametrize(
    "mode", ["optional", "oversize", "cycle", "annotation", "predictor"]
)
def test_unsupported_render_scopes_refuse_before_inventory(mode, tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings([_wrapper(fresh[0])], fresh, _key)

    def change(pdf):
        if mode == "optional":
            pdf.Root.OCProperties = pikepdf.Dictionary()
        elif mode == "oversize":
            next(iter(pdf.pages[0].Resources.XObject.items()))[1].Width = 100_000
        elif mode == "cycle":
            image = next(iter(pdf.pages[0].Resources.XObject.items()))[1]
            image.SMask = image
        elif mode == "predictor":
            image = next(iter(pdf.pages[0].Resources.XObject.items()))[1]
            image.DecodeParms = pikepdf.Dictionary(
                Predictor=12, Colors=10_000, Columns=10_000_000
            )
        else:
            pdf.pages[0].obj.Annots = pikepdf.Array(
                [pikepdf.Dictionary(Subtype=pikepdf.Name.Stamp)]
            )

    _tag(source, output, mutate=None if mode == "predictor" else change)
    if mode == "predictor":
        # qpdf strips unused DecodeParms from an unfiltered source on save.
        # Introduce the malicious filter parameters into the actual saved
        # Flate image without another normalizing qpdf save.
        with fitz.open(output) as pdf:
            xref = pdf[0].get_images()[0][0]
            pdf.xref_set_key(
                xref,
                "DecodeParms",
                "<< /Predictor 12 /Colors 10000 /Columns 10000000 >>",
            )
            pdf.saveIncr()
    monkeypatch.setattr(
        "src.education.remediation.pdf_image_finding_identity._displayed_image_occurrences",
        lambda *_: pytest.fail("Must preflight before image inventory"),
    )
    with pytest.raises(ValueError):
        preserve_image_pages(str(source), str(output), bindings)


def test_predictor_cannot_collide_with_unfiltered_resource():
    import zlib

    with pikepdf.new() as pdf:
        payload = b"\x00\xff\x00\x00"
        filtered = pdf.make_stream(zlib.compress(payload))
        filtered.Filter = pikepdf.Name.FlateDecode
        filtered.DecodeParms = pikepdf.Dictionary(
            Predictor=12, Colors=3, Columns=1, BitsPerComponent=8
        )
        raw = pdf.make_stream(payload)
        raw.DecodeParms = filtered.DecodeParms
        _ResourceProof().value(filtered)
        with pytest.raises(ValueError, match="predictor scope"):
            _ResourceProof().value(raw)


def test_full_pipeline_verifies_owned_image_descriptions(tmp_path):
    source = tmp_path / "source.pdf"
    _fixture(source)
    scan = PDFProcessor(generate_alt_text=False).process_pdf(str(source))

    class Vision:
        def analyze_image_sync(self, *, image_data, **_):
            assert image_data
            return {"success": True, "content": "A blue rectangle with a red corner."}

    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=True,
            allow_legacy_nested_ai=False,
            verify_fixes=True,
            create_backup=False,
            output_directory=str(tmp_path / "output"),
        ),
        ai_client=None,
        alt_text_client=Vision(),
    )
    result = remediator.remediate()
    try:
        assert result.success
        assert result.verification_passed, result.verification_result
        fixed_images = [
            fixed for fixed in result.fixed_issues if fixed.category.value == "alt_text"
        ]
        assert len(fixed_images) == 3
        assert all(
            fixed.verification_passed and fixed.needs_review for fixed in fixed_images
        )
        assert result.has_output_claim()
        from src.education.remediation.outcome_accounting import outcome_accounting
        from src.jobs.contracts import JobSuccess, public_job_result
        from src.jobs.remediation_subprocess import (
            SubprocessRemediationResult,
            _json_record,
        )

        child = SubprocessRemediationResult(
            values={
                "fixed_issues": [_json_record(fixed) for fixed in result.fixed_issues],
                "manual_issues": [
                    _json_record(issue) for issue in result.manual_issues
                ],
            }
        )
        outcomes = outcome_accounting(
            remediator.issues,
            child,
            published=True,
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            output_sha256=result.output_claim_metadata()["sha256"],
        )
        public = public_job_result(JobSuccess(outcomes).result)
        fixed_rows = [
            row for row in public["issue_outcomes"] if row["status"] == "fixed"
        ]
        assert fixed_rows
        assert all(row["verification_passed"] is True for row in fixed_rows)
        image_ids = {fix.issue_id for fix in fixed_images}
        assert all(
            row["needs_review"] is True
            for row in fixed_rows
            if row.get("issue_id") in image_ids
        )
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("budget", ["decoded_image_bytes", "draw_bytes"])
def test_aggregate_image_work_is_bounded_before_inventory(
    budget, tmp_path, monkeypatch
):
    from src.education.remediation import pdf_image_finding_identity as module

    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    _fixture(source)
    _, fresh = _findings(source)
    bindings = bind_missing_image_findings([_wrapper(fresh[0])], fresh, _key)
    _tag(source, output)
    real = module._ResourceProof

    def proof():
        instance = real()
        setattr(instance, budget, module._MAX_IMAGE_WORK_BYTES)
        return instance

    monkeypatch.setattr(module, "_ResourceProof", proof)
    monkeypatch.setattr(
        module,
        "_displayed_image_occurrences",
        lambda *_: pytest.fail("Must preflight aggregate decode/draw work"),
    )
    with pytest.raises(ValueError, match="Aggregate image"):
        preserve_image_pages(str(source), str(output), bindings)
