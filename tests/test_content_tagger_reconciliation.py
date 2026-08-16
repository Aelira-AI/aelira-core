"""Reconciliation of ContentTagger fixes into the result buckets (issue #48).

Phase 1 classifies the scanner's four document-level structure issue types
(missing_content_marking, empty_parent_tree, missing_document_root,
missing_pdfua_identifier) as manual because no per-issue fixer handles
them - but Phase 2's ContentTaggerV2 fixes exactly those four things
during save. Without reconciliation the result reports them as manual
even though verification confirms them fixed.

These tests drive _reconcile_content_tagger_fixes directly: seed the
manual bucket the way Phase 1 does, set the tagger stats the way
_save_document does, and assert the buckets end up truthful.
"""

from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator

CONTENT_TAGGER_TYPES = [
    "missing_content_marking",
    "empty_parent_tree",
    "missing_document_root",
    "missing_pdfua_identifier",
]


def _make_remediator(tmp_path, issue_types):
    # The base class requires the file to exist; it is never opened here.
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    issues = [
        {
            "type": "structure",
            "issue_type": itype,
            "rule": "PDF/UA",
            "message": f"Scanner finding for {itype}",
        }
        for itype in issue_types
    ]
    remediator = PdfRemediator(
        str(pdf_path),
        issues,
        RemediationConfig(use_ai=False, create_backup=False),
    )
    # Seed the manual bucket exactly the way Phase 1 does for these types
    for issue in remediator.issues:
        remediator._add_manual_issue(
            issue,
            reason="Cannot auto-fix this issue type",
            recommendation="Manual remediation required",
        )
    return remediator


class TestReconcileContentTaggerFixes:
    def test_all_four_types_promoted_when_tagging_succeeded(self, tmp_path):
        remediator = _make_remediator(tmp_path, CONTENT_TAGGER_TYPES)
        remediator._content_tagger_stats = {
            "pages_processed": 3,
            "blocks_matched": 10,
            "blocks_created": 2,
        }

        remediator._reconcile_content_tagger_fixes()

        assert remediator.result.fixed_count == 4
        assert remediator.result.manual_count == 0
        assert remediator.result.manual_issues == []
        assert {f.issue_id for f in remediator.result.fixed_issues} == {
            i.id for i in remediator.issues
        }
        for fixed in remediator.result.fixed_issues:
            assert fixed.fix_method == "rule"

    def test_marking_types_not_promoted_when_nothing_tagged(self, tmp_path):
        remediator = _make_remediator(tmp_path, CONTENT_TAGGER_TYPES)
        remediator._content_tagger_stats = {
            "pages_processed": 3,
            "blocks_matched": 0,
            "blocks_created": 0,
        }

        remediator._reconcile_content_tagger_fixes()

        # Document root and PDF/UA identifier are set unconditionally by
        # the tagger; content marking and ParentTree need tagged blocks.
        promoted = {f.issue_id for f in remediator.result.fixed_issues}
        still_manual = {m.issue_id for m in remediator.result.manual_issues}
        by_type = {i.metadata["issue_type"]: i.id for i in remediator.issues}

        assert by_type["missing_document_root"] in promoted
        assert by_type["missing_pdfua_identifier"] in promoted
        assert by_type["missing_content_marking"] in still_manual
        assert by_type["empty_parent_tree"] in still_manual
        assert remediator.result.fixed_count == 2
        assert remediator.result.manual_count == 2

    def test_nothing_promoted_without_tagger_stats(self, tmp_path):
        """v1 fallback or tagger failure: stay honest, keep issues manual."""
        remediator = _make_remediator(tmp_path, CONTENT_TAGGER_TYPES)

        remediator._reconcile_content_tagger_fixes()

        assert remediator.result.fixed_count == 0
        assert remediator.result.manual_count == 4

    def test_unrelated_manual_issues_untouched(self, tmp_path):
        remediator = _make_remediator(tmp_path, ["missing_document_root"])
        # Add an unrelated manual issue (different issue_type)
        unrelated = PdfRemediator(
            str(tmp_path / "input.pdf"),
            [
                {
                    "type": "contrast",
                    "issue_type": "low_contrast_text",
                    "message": "Low contrast text",
                }
            ],
            RemediationConfig(use_ai=False, create_backup=False),
        ).issues[0]
        remediator._add_manual_issue(
            unrelated,
            reason="Contrast requires human judgment",
            recommendation="Adjust colors",
        )
        remediator._content_tagger_stats = {
            "pages_processed": 1,
            "blocks_matched": 5,
            "blocks_created": 0,
        }

        remediator._reconcile_content_tagger_fixes()

        assert remediator.result.fixed_count == 1
        assert remediator.result.manual_count == 1
        assert remediator.result.manual_issues[0].issue_id == unrelated.id
