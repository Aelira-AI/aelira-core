"""Source-bound map changes, exact preservation and immutable v2 transport."""

from dataclasses import replace
import hashlib
import io

import pikepdf
import pymupdf
import pytest

from pdf_font_fixtures import cmap_bytes, snapshot, truetype_pdf
from src.education.remediation.pdf_font_mapping_proposals import (
    AssignmentChange,
    ExpectedRun,
    FontMapPatch,
    FontMappingProposal,
    MappingProposalError,
    compile_font_mapping_proposal,
    inventory_sha256,
    proposal_from_json,
    proposal_to_json,
)
from src.education.remediation.pdf_text_inventory import inspect_pdf_text_inventory

pytestmark = pytest.mark.unit
EVIDENCE = hashlib.sha256(
    b"authored fixture text AB; renderer glyphs authored separately"
).hexdigest()


def proposal(source, operation, changes, text="AB", *, target=0):
    inventory = inspect_pdf_text_inventory(source)
    info = inventory.fonts[target]
    return FontMappingProposal(
        inventory.source_sha256,
        inventory_sha256(inventory),
        (
            FontMapPatch(
                info.identity,
                info.fingerprint,
                info.original_map_sha256,
                info.used_codes,
                operation,
                tuple(
                    AssignmentChange(code, old, new, "trusted_authoring_text", EVIDENCE)
                    for code, old, new in changes
                ),
            ),
        ),
        tuple(
            ExpectedRun(
                run.page_index, run.start, run.end, text if index == 0 else "AB"
            )
            for index, run in enumerate(inventory.runs)
        ),
        "internal-test-actor",
        "synthetic-authorship-review",
    )


def pixels(source):
    with pymupdf.open(stream=source, filetype="pdf") as pdf:
        return [page.get_pixmap().samples for page in pdf]


@pytest.mark.parametrize(
    "operation,mapping,changes",
    [
        ("create_missing", None, [(1, None, "A"), (2, None, "B")]),
        ("supplement_partial", {1: "A", 99: "unused"}, [(2, None, "B")]),
        ("replace_reviewed", {1: "7", 2: "B", 99: "unused"}, [(1, "7", "A")]),
    ],
)
def test_all_operations_preserve_pixels_unused_assignments_and_review_boundary(
    operation, mapping, changes
):
    with truetype_pdf(mappings=mapping) as pdf:
        source = snapshot(pdf)
    plan = proposal(source, operation, changes)
    result = compile_font_mapping_proposal(source, plan)
    assert result.independent_review_pending and result.fidelity_status == "unassessed"
    assert (
        result.saved_consistency_verified
        and result.source_sha256 == hashlib.sha256(source).hexdigest()
    )
    assert pixels(source) == pixels(result.pdf_bytes)
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as saved:
        from src.education.remediation.pdf_font_mapping_proposals import _existing_map

        assignments = _existing_map(saved.pages[0].Resources.Font.F1, 2)
    assert assignments[1] == "A" and assignments[2] == "B"
    if mapping:
        assert assignments[99] == "unused"


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda plan: replace(plan, source_sha256="0" * 64), "source_changed"),
        (lambda plan: replace(plan, inventory_sha256="0" * 64), "inventory_changed"),
        (
            lambda plan: replace(
                plan, fonts=(replace(plan.fonts[0], old_map_sha256="0" * 64),)
            ),
            "font_changed",
        ),
        (
            lambda plan: replace(
                plan, fonts=(replace(plan.fonts[0], used_codes=(1,)),)
            ),
            "font_changed",
        ),
        (lambda plan: replace(plan, runs=()), "transcript"),
        (
            lambda plan: replace(plan, runs=(replace(plan.runs[0], text="7B"),)),
            "transcript",
        ),
    ],
)
def test_stale_source_evidence_and_incomplete_or_wrong_transcripts_refuse(
    mutation, reason
):
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    with pytest.raises(MappingProposalError, match=reason):
        compile_font_mapping_proposal(source, mutation(plan))


def test_supplement_never_overwrites_existing_assignment():
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        source = snapshot(pdf)
    plan = proposal(source, "supplement_partial", [(1, "7", "A")])
    with pytest.raises(MappingProposalError, match="overwrite_forbidden"):
        compile_font_mapping_proposal(source, plan)


def test_reviewed_replacement_requires_context_evidence():
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    change = replace(
        plan.fonts[0].changes[0], evidence_origin="embedded_truetype_chain"
    )
    plan = replace(plan, fonts=(replace(plan.fonts[0], changes=(change,)),))
    with pytest.raises(MappingProposalError, match="correction_review_required"):
        compile_font_mapping_proposal(source, plan)


def test_shared_font_later_page_cannot_omit_occurrence():
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        page.Resources = pdf.pages[0].Resources
        page.Contents = pdf.pages[0].Contents
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    assert len(plan.runs) == 2
    compile_font_mapping_proposal(source, plan)
    with pytest.raises(MappingProposalError, match="transcript"):
        compile_font_mapping_proposal(source, replace(plan, runs=plan.runs[:1]))


def test_mixed_healthy_font_and_saved_untargeted_map_tampering_refuses(monkeypatch):
    with (
        truetype_pdf(mappings={1: "7", 2: "B"}) as pdf,
        truetype_pdf(mappings={1: "A", 2: "B"}) as healthy,
    ):
        pdf.pages.append(healthy.pages[0])
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    compile_font_mapping_proposal(source, plan)
    original_save = pikepdf.Pdf.save

    def corrupt(self, *args, **kwargs):
        self.pages[1].Resources.Font.F1.ToUnicode = self.make_stream(
            cmap_bytes({1: "Z", 2: "B"})
        )
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", corrupt)
    with pytest.raises(MappingProposalError, match="preservation"):
        compile_font_mapping_proposal(source, plan)


def test_saved_target_map_cannot_drop_unused_assignments(monkeypatch):
    with truetype_pdf(mappings={1: "7", 2: "B", 99: "unused"}) as pdf:
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    original_save = pikepdf.Pdf.save

    def corrupt(self, *args, **kwargs):
        self.pages[0].Resources.Font.F1.ToUnicode = self.make_stream(
            cmap_bytes({1: "A", 2: "B"})
        )
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(pikepdf.Pdf, "save", corrupt)
    with pytest.raises(MappingProposalError, match="retained_assignments"):
        compile_font_mapping_proposal(source, plan)


def test_duplicate_original_map_entry_refuses_without_overwrite():
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        font = pdf.pages[0].Resources.Font.F1
        font.ToUnicode = pdf.make_stream(
            cmap_bytes({1: "7", 2: "B"})
            .replace(b"2 beginbfchar", b"3 beginbfchar")
            .replace(b"endbfchar", b"<0001> <0041> endbfchar")
        )
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "A", "7")], text="7B")
    with pytest.raises(MappingProposalError, match="old_map_duplicate"):
        compile_font_mapping_proposal(source, plan)


def test_transport_is_canonical_closed_and_immutable():
    with truetype_pdf() as pdf:
        source = snapshot(pdf)
    plan = proposal(source, "create_missing", [(1, None, "A"), (2, None, "B")])
    encoded = proposal_to_json(plan)
    assert proposal_from_json(encoded) == plan
    assert proposal_to_json(proposal_from_json(encoded)) == encoded
    for raw in [
        encoded.replace(b'"schema_version":2', b'"schema_version":1'),
        encoded[:-1] + b',"unknown":1}',
        encoded[:-1] + b',"schema_version":2}',
        encoded.replace(b'"code":1', b'"code":1.0'),
    ]:
        with pytest.raises(MappingProposalError):
            proposal_from_json(raw)
    with pytest.raises(MappingProposalError):
        proposal_to_json(replace(plan, fonts=list(plan.fonts)))


def test_deterministic_planner_uses_actual_chain_and_rechecks_rule_evidence():
    from src.education.remediation.pdf_font_mapping_proposals import (
        plan_deterministic_font_maps,
    )

    with truetype_pdf(gid_map=b"\0\0\0\2\0\1") as pdf:
        source = snapshot(pdf)
    plan = plan_deterministic_font_maps(source).proposal
    assert plan is not None and plan.runs[0].text == "BA"
    compile_font_mapping_proposal(source, plan)
    change = replace(plan.fonts[0].changes[0], new="Z")
    forged = replace(
        plan,
        fonts=(replace(plan.fonts[0], changes=(change,) + plan.fonts[0].changes[1:]),),
    )
    with pytest.raises(MappingProposalError, match="rule_evidence_changed"):
        compile_font_mapping_proposal(source, forged)


def test_deterministic_simple_map_covers_base_letters_and_differences():
    from src.education.remediation.pdf_font_mapping_proposals import (
        plan_deterministic_font_maps,
        _existing_map,
    )
    from test_pdf_font_text import simple_pdf

    with simple_pdf(
        b"BT /F1 12 Tf 30 300 Td <41427f> Tj ET",
        encoding=pikepdf.Dictionary(
            BaseEncoding=pikepdf.Name.WinAnsiEncoding,
            Differences=pikepdf.Array([127, pikepdf.Name.bullet]),
        ),
    ) as pdf:
        pdf.pages[0].Resources.Font.F1 = pdf.make_indirect(
            pdf.pages[0].Resources.Font.F1
        )
        source = snapshot(pdf)
    plan = plan_deterministic_font_maps(source).proposal
    assert plan is not None and plan.runs[0].text == "AB•"
    result = compile_font_mapping_proposal(source, plan)
    with pikepdf.open(io.BytesIO(result.pdf_bytes)) as saved:
        mapping = _existing_map(saved.pages[0].Resources.Font.F1, 1)
        assert mapping[65] == "A" and mapping[66] == "B" and mapping[127] == "•"
        assert len(mapping) > 200


def test_deterministic_planner_abstains_on_alias_and_existing_maps():
    from src.education.remediation.pdf_font_mapping_proposals import (
        plan_deterministic_font_maps,
    )

    with truetype_pdf(alias=True) as pdf:
        result = plan_deterministic_font_maps(snapshot(pdf))
    assert result.proposal is None and result.unresolved_fonts
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        result = plan_deterministic_font_maps(snapshot(pdf))
    assert result.proposal is None


def test_actualtext_and_marked_content_cannot_override_corrected_map():
    for wrapper in [b"/Span << /ActualText (7) >> BDC", b"/Artifact BMC"]:
        with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
            page = pdf.pages[0]
            page.Contents = pdf.make_stream(
                wrapper + b" " + page.Contents.read_bytes() + b" EMC"
            )
            source = snapshot(pdf)
        plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
        with pytest.raises(MappingProposalError, match="marked_content_scope"):
            compile_font_mapping_proposal(source, plan)


def test_direct_font_planner_reports_unresolved_scope():
    from src.education.remediation.pdf_font_mapping_proposals import (
        plan_deterministic_font_maps,
    )
    from test_pdf_font_text import simple_pdf

    with simple_pdf(b"BT /F1 12 Tf (AB) Tj ET") as pdf:
        result = plan_deterministic_font_maps(snapshot(pdf))
    assert result.proposal is None and result.unresolved_fonts == ("page:0/font:/F1",)


def test_original_codespace_must_be_complete_and_match_entry_width():
    with truetype_pdf(mappings={1: "7", 2: "B"}) as pdf:
        font = pdf.pages[0].Resources.Font.F1
        font.ToUnicode = pdf.make_stream(
            cmap_bytes({1: "7", 2: "B"}).replace(b"<0000> <FFFF>", b"<00> <FF>")
        )
        source = snapshot(pdf)
    plan = proposal(source, "replace_reviewed", [(1, "7", "A")])
    with pytest.raises(MappingProposalError, match="code_space"):
        compile_font_mapping_proposal(source, plan)
