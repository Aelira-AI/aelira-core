"""Public outcome guidance from known codes, never raw exceptions or paths."""

from collections.abc import Mapping

# Only these authored messages may cross the durable-job public boundary.
EXPLANATIONS = {
    "source_text_run_ambiguous": (
        "The proposed heading cannot be attached to a distinct, complete source text run.",
        "Split or correct the heading in the source document, export again, and rescan.",
        "not_applied",
    ),
    "saved_finding_unresolved": (
        "A change was attempted, but the saved-file rescan still detected this finding.",
        "Inspect the affected page or element in a PDF editor, correct it, and rescan.",
        "candidate_change",
    ),
    "category_disabled": (
        "This remediation category was disabled for the job.",
        "Enable this category in remediation settings or correct the source document.",
        "not_attempted",
    ),
    "alt_text_provider_unavailable": (
        "No image-description AI provider was available to this remediation job.",
        "Ask your administrator to check the image AI configuration, or supply a reviewed description.",
        "not_attempted",
    ),
    "image_description_unavailable": (
        "The image-description step did not produce a usable description.",
        "Review the image and its context, supply an accurate description, and retry remediation.",
        "not_applied",
    ),
    "image_description_math_not_speakable": (
        "The image description proposed by the AI contained mathematical notation, such as LaTeX, that a screen reader would announce symbol by symbol, so no description was written.",
        "Describe the image yourself, writing any mathematics in plain words, or add a Formula tag with alt text if it is an equation, then rescan.",
        "not_applied",
    ),
    "image_occurrence_unbound": (
        "The image description could not be attached to one uniquely identified source image occurrence.",
        "Locate the image in the source document or PDF tag tree and verify its Figure and marked-content association before adding a description.",
        "not_applied",
    ),
    "image_ownership_unsupported": (
        "The image has shared, ambiguous or unsupported Figure ownership, or already has an accessible description.",
        "Review the image's existing Figure, description and marked-content ownership in a PDF editor before changing it.",
        "not_applied",
    ),
    "decorative_image_requires_review": (
        "The image was proposed as decorative, but safely marking its actual PDF content as an artifact requires source-level review.",
        "Confirm that the image is decorative, then mark it as a background artifact in the source document or PDF editor and rescan. An empty description alone is insufficient.",
        "not_applied",
    ),
    "reading_order_candidate_still_unverified": (
        "The proposed tag order did not pass the reading-order check. The proposal was rolled back and the original semantic groups were retained.",
        "Review the page's column groups, headings and footer in the PDF tag tree. Confirm the intended sequence with a screen reader before editing and rescanning.",
        "candidate_rolled_back",
    ),
    "reviewed_pdf_change_set_unsupported": (
        "This set of reviewed changes is outside the supported PDF replay scope.",
        "Apply the reviewed changes in the source document or a PDF editor, then rescan. Specialized visual content requires its existing review workflow.",
        "not_applied",
    ),
    "reviewed_pdf_exact_change_unavailable": (
        "The reviewed PDF change could not be applied exactly as approved.",
        "Review the saved replacement and source finding, correct the source document if needed, and retry the reviewed rebuild.",
        "not_applied",
    ),
    "fix_generation_unavailable": (
        "The remediator could not generate an appropriate change for this finding.",
        "Review the scan guidance and correct the affected element in the source document.",
        "not_applied",
    ),
    "pdfua_declaration_requires_validation": (
        "A PDF/UA identifier is a conformance declaration; adding it alone would not make the PDF conformant.",
        "Resolve the accessibility findings and complete independent PDF/UA validation before declaring conformance.",
        "not_applied",
    ),
    "image_draw_unresolved": (
        "The image could not be matched to exactly one drawing operation in the page content that structure can mark, so its description was not written.",
        "Describe the image manually, or re-export the source document and rescan.",
        "not_applied",
    ),
    "image_draw_ambiguous": (
        "Another image is drawn over this one at nearly the same place, so a description could not be bound to this image alone and was not written.",
        "Describe the image manually, or separate the overlapping images in the source document and rescan.",
        "not_applied",
    ),
    "image_in_marked_content": (
        "The image is drawn inside a marked-content scope in the page content, such as an artifact or span, where a description cannot be attached to it, so none was written.",
        "Describe the image in the source document, or correct how it is marked there, then rescan.",
        "not_applied",
    ),
    "image_occurrence_unidentified": (
        "The finding does not identify which image on its page it describes, and the page holds other images, so no description was written.",
        "Rescan the document so each image is reported with its own identity, then describe the image manually if it is still undescribed.",
        "not_applied",
    ),
    "image_equation_existing_owner_unsupported": (
        "The image is already inside other marked content, such as a tagged element or an artifact, so the equation was not read or changed.",
        "Describe the equation in the alternative text of the tagged element that contains the image, or correct how the image is marked in the source document, then rescan.",
        "not_attempted",
    ),
    "image_equation_draw_unresolved": (
        "The image could not be matched to exactly one drawing operation in the page content, so the equation was not read or changed.",
        "Describe the equation manually, or re-export the source document and rescan.",
        "not_attempted",
    ),
    "image_equation_alt_text_deferred": (
        "The alternative text for this image was left to the equation fix, and that fix did not complete.",
        "Review the equation finding for this image and describe the image manually, then rescan.",
        "not_applied",
    ),
    "image_equation_alt_text_withheld": (
        "The image was recognised as an equation that could not be verified, so no description was written and it needs review.",
        "Review the equation finding for this image, then describe the image manually or correct the source document and rescan.",
        "not_applied",
    ),
    "printed_equation_verifier_unavailable": (
        "The equation was read once and matched its image, but no second, independent reading was available to confirm it, so it was not applied.",
        "Ask your administrator to check the image AI configuration, or check the equation against the image and add its Formula tag and alt text manually, then rescan.",
        "not_applied",
    ),
    "printed_equation_verifier_failed": (
        "The equation was read once and matched its image, but a second, independent reading could not be completed, so it was not applied.",
        "Retry remediation, or check the equation against the image and add its Formula tag and alt text manually, then rescan.",
        "not_applied",
    ),
    "printed_equation_verifier_declined": (
        "The equation was read once and matched its image, but a second, independent reading did not find a printed equation in it, so it was not applied.",
        "Check the image. If it is an equation, add its Formula tag and alt text manually; if it is not, describe it as an image, then rescan.",
        "not_applied",
    ),
    "printed_equation_semantic_disagreement": (
        "Two independent readings of the equation did not agree, so neither was applied.",
        "Check the equation against the image, then add its Formula tag and alt text manually, and rescan.",
        "not_applied",
    ),
    "image_equation_alt_text_ambiguous": (
        "The equation was read, but its notation cannot be turned into reliable plain-language alternative text automatically, so it was not applied.",
        "Write the equation's alternative text yourself and add its Formula tag, or rewrite the equation with braced fractions, roots and exponents in the source document, then rescan.",
        "not_applied",
    ),
    "table_structure_not_verified": (
        "The table's actual cell content and structure could not be verified.",
        "Review the table cells and headers in the source document or PDF editor, then rescan.",
        "not_applied",
    ),
    "table_structure_too_complex": (
        "The table exceeds the supported automatic structure scope.",
        "Simplify or split the table, verify its headers and cell associations, then rescan.",
        "not_applied",
    ),
    "equation_too_large_for_verification": (
        "The recognized equation renders larger than the size the automatic equation check supports, so it could not be compared with the image.",
        "Check the equation against the image, split a long equation into separate lines or images, or add its Formula tag and alt text manually, then rescan.",
        "not_applied",
    ),
    "output_verification_failed": (
        "The change exists only in a candidate file. The document did not pass the required output verification, so that file was not delivered.",
        "Review the unresolved findings and correct the document before running remediation again.",
        "candidate_change",
    ),
    "output_not_published": (
        "The change was recorded in a candidate, but no resulting file was published for this job.",
        "Review the job status and resolve its blocking condition before retrying.",
        "candidate_change",
    ),
    "output_adds_pdfua_failures": (
        "The change exists only in a candidate file. Publishing it would add PDF/UA problems the source document did not have, so the file was withheld and your original is unchanged.",
        "Running remediation again on this document is unlikely to change the result. Review the unresolved findings and correct the document in its source, then rescan.",
        "candidate_change",
    ),
    "manual_reason_unrecorded": (
        "This job requires manual remediation but did not record a more specific public reason.",
        "Use the scan guidance to inspect and correct this finding, then rescan.",
        "not_recorded",
    ),
}

# Explicit supported guard codes share guidance only when they have the same
# corrective action. Unknown exception strings never become public messages.
_ORDER_GUARD_GROUPS = (
    (
        (
            "reading_order_page_limit",
            "reading_order_document_repair_limit",
            "reading_order_snapshot_limit",
            "reading_order_structure_limit",
            "reading_order_page_reference_limit",
            "reading_order_text_limit",
            "reading_order_word_limit",
            "reading_order_text_match_limit",
        ),
        "The document exceeds the bounded size or complexity supported for automatic reading-order repair.",
        "Review and repair the tag order in the source document or a PDF editor, or split the document into smaller independently tagged documents.",
    ),
    (
        (
            "reading_order_columns_or_overlapping_content",
            "reading_order_semantic_group_requires_review",
            "reading_order_mixed_or_discontiguous_group",
            "reading_order_interleaved_semantic_groups",
            "reading_order_replacement_text_requires_review",
            "reading_order_no_supported_permutation",
        ),
        "The layout or semantic groups cannot be safely reordered by the supported automatic repair.",
        "Review the intended sequence of columns, tables, lists, figures and surrounding text in the source document or PDF tag tree, then rescan.",
    ),
    (
        (
            "reading_order_ambiguous_mcid_ownership",
            "reading_order_ambiguous_text_ownership",
            "reading_order_repeated_or_ambiguous_text",
            "reading_order_source_geometry_mismatch",
            "reading_order_source_text_mismatch",
            "reading_order_unresolved_source_content",
            "reading_order_empty_content_reference",
            "reading_order_unbound_block_indices",
            "reading_order_source_preflight_failed",
        ),
        "The tag order could not be matched unambiguously to the actual source text and its placement.",
        "Verify each affected tag's marked-content association and text placement in a PDF editor before reordering; re-export from the source if necessary.",
    ),
    (
        (
            "reading_order_missing_structure",
            "reading_order_invalid_reference",
            "reading_order_invalid_structure",
            "reading_order_invalid_page_reference",
            "reading_order_cyclic_or_shared_structure",
            "reading_order_invalid_parent_relation",
            "reading_order_invalid_structure_type",
            "reading_order_invalid_role",
            "reading_order_invalid_page",
        ),
        "The PDF tag tree or page references are missing, inconsistent or unsupported for safe automatic reordering.",
        "Repair the tag tree and page associations in the source document or PDF editor, then rescan before reordering.",
    ),
    (
        (
            "reading_order_form_stream_scope",
            "reading_order_object_reference",
            "reading_order_rotated_or_cropped_page",
            "reading_order_unsupported_text_direction",
        ),
        "The page uses content references, geometry or text direction outside the supported automatic reading-order scope.",
        "Inspect the affected page and its referenced content in a PDF editor, set the intended reading sequence manually, and rescan.",
    ),
)
for _codes, _reason, _next_step in _ORDER_GUARD_GROUPS:
    for _code in _codes:
        EXPLANATIONS[_code] = (_reason, _next_step, "not_applied")

_MANUAL_REASON_CODES = {
    "Heading cannot be bound to a distinct complete source text run.": "source_text_run_ambiguous",
    "The saved-file rescan did not verify this finding was resolved.": "saved_finding_unresolved",
    "Category disabled in configuration": "category_disabled",
    "Alt-text AI provider unavailable": "alt_text_provider_unavailable",
    "No usable image description was returned": "image_description_unavailable",
    "image_description_math_not_speakable": "image_description_math_not_speakable",
    "The image occurrence could not be uniquely bound to an existing Figure.": "image_occurrence_unbound",
    "The existing Figure has ambiguous, shared or unsupported image ownership.": "image_ownership_unsupported",
    "Decorative image artifact conversion requires source-level review.": "decorative_image_requires_review",
    "Reviewed PDF replay does not support this change set.": "reviewed_pdf_change_set_unsupported",
    "Reviewed PDF change could not be applied exactly.": "reviewed_pdf_exact_change_unavailable",
    "Could not generate appropriate fix": "fix_generation_unavailable",
    "PDF/UA conformance declaration requires independent validation": "pdfua_declaration_requires_validation",
    "table_structure_not_verified": "table_structure_not_verified",
    "table_structure_too_complex": "table_structure_too_complex",
    "image_draw_unresolved": "image_draw_unresolved",
    "image_draw_ambiguous": "image_draw_ambiguous",
    "image_in_marked_content": "image_in_marked_content",
    "image_occurrence_unidentified": "image_occurrence_unidentified",
    "image_equation_existing_owner_unsupported": "image_equation_existing_owner_unsupported",
    "image_equation_draw_unresolved": "image_equation_draw_unresolved",
    "image_equation_alt_text_deferred": "image_equation_alt_text_deferred",
    "image_equation_alt_text_withheld": "image_equation_alt_text_withheld",
    "equation_too_large_for_verification": "equation_too_large_for_verification",
    "printed_equation_verifier_unavailable": "printed_equation_verifier_unavailable",
    "printed_equation_verifier_failed": "printed_equation_verifier_failed",
    "printed_equation_verifier_declined": "printed_equation_verifier_declined",
    "printed_equation_semantic_disagreement": "printed_equation_semantic_disagreement",
    "image_equation_alt_text_ambiguous": "image_equation_alt_text_ambiguous",
}
_MANUAL_REASON_CODES.update(
    {code: code for codes, _, _ in _ORDER_GUARD_GROUPS for code in codes}
)
_MANUAL_REASON_CODES["reading_order_candidate_still_unverified"] = (
    "reading_order_candidate_still_unverified"
)


def public_explanation(code):
    """Recreate canonical text; ignore caller-supplied explanation strings."""
    if not isinstance(code, str) or code not in EXPLANATIONS:
        return {}
    reason, next_step, attempt = EXPLANATIONS[code]
    return {
        "reason_code": code,
        "reason": reason,
        "next_step": next_step,
        "attempt": attempt,
    }


def manual_explanation(record):
    reason = (
        record.get("reason")
        if isinstance(record, Mapping)
        else getattr(record, "reason", None)
    )
    if not isinstance(reason, str):
        return {}
    return public_explanation(
        _MANUAL_REASON_CODES.get(reason, "manual_reason_unrecorded")
    )
