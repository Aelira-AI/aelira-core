"""Tests for review API route models and business logic.

Tests cover:
- Pydantic response model serialization (FixSummary, QueueItem, QueueStats, etc.)
- Request model validation (FixAction, BatchAction)
- Business logic helpers (compliance level computation, doc status)
- Edge cases for each model

These tests import from `src.api.review_routes` using the normal package path.
Run with the backend venv to ensure FastAPI and SQLAlchemy are available.
"""

from datetime import datetime, timezone

import pytest

from pydantic import ValidationError

from src.api.review_routes import (
    FixSummary,
    QueueItem,
    QueueStats,
    DocumentReview,
    FixAction,
    BatchAction,
    ReviewResponse,
    BatchResponse,
    AuditEntry,
    compute_compliance_level,
    compute_doc_status,
    compute_validator_result,
)

# ---------------------------------------------------------------------------
# Business logic tests
# ---------------------------------------------------------------------------


class TestComputeComplianceLevel:
    """Tests for the compliance level computation helper."""

    def test_not_validated_when_no_checkpoints(self):
        assert compute_compliance_level(total=0, failed=0) == "not_validated"

    def test_compliant_when_zero_failures(self):
        assert compute_compliance_level(total=10, failed=0) == "compliant"

    def test_partial_when_failures_at_20_percent(self):
        # 2 out of 10 = exactly 20%
        assert compute_compliance_level(total=10, failed=2) == "partial"

    def test_partial_when_failures_below_20_percent(self):
        # 1 out of 10 = 10%
        assert compute_compliance_level(total=10, failed=1) == "partial"

    def test_non_compliant_when_failures_above_20_percent(self):
        # 3 out of 10 = 30%
        assert compute_compliance_level(total=10, failed=3) == "non_compliant"

    def test_non_compliant_when_all_fail(self):
        assert compute_compliance_level(total=5, failed=5) == "non_compliant"

    def test_partial_boundary_with_5_total(self):
        # 1 out of 5 = 20% exactly
        assert compute_compliance_level(total=5, failed=1) == "partial"

    def test_non_compliant_boundary_with_5_total(self):
        # 2 out of 5 = 40% > 20%
        assert compute_compliance_level(total=5, failed=2) == "non_compliant"


class TestComputeValidatorResult:
    """Tests for the non-conformance validator summary exposed by the API."""

    def test_not_run_when_no_checkpoints(self):
        assert compute_validator_result(total=0, passed=0, failed=0) == "not_run"

    def test_all_recorded_checkpoints_passed(self):
        assert (
            compute_validator_result(total=5, passed=5, failed=0)
            == "all_recorded_checkpoints_passed"
        )

    def test_failures_take_precedence(self):
        assert (
            compute_validator_result(total=5, passed=4, failed=1)
            == "recorded_checkpoint_failures"
        )

    def test_non_pass_non_fail_results_are_neutral(self):
        assert (
            compute_validator_result(total=5, passed=4, failed=0)
            == "recorded_checkpoint_results_available"
        )


class TestComputeDocStatus:
    """Tests for the document-level status computation."""

    def test_approved_when_no_pending(self):
        assert compute_doc_status(0) == "approved"

    def test_pending_when_fixes_need_review(self):
        assert compute_doc_status(1) == "pending"

    def test_pending_when_many_fixes_need_review(self):
        assert compute_doc_status(42) == "pending"


# ---------------------------------------------------------------------------
# Pydantic model serialization tests
# ---------------------------------------------------------------------------


class TestFixSummary:
    """Tests for the FixSummary response model."""

    def test_basic_serialization(self):
        fix = FixSummary(
            id="fix-001",
            category="images",
            severity="critical",
            description="Missing alt text on figure",
            confidence=0.75,
            fix_method="ai_vision",
            needs_review=True,
            review_status="pending",
            page_number=3,
        )
        data = fix.model_dump()
        assert data["id"] == "fix-001"
        assert data["category"] == "images"
        assert data["severity"] == "critical"
        assert data["confidence"] == 0.75
        assert data["fix_method"] == "ai_vision"
        assert data["needs_review"] is True
        assert data["review_status"] == "pending"
        assert data["page_number"] == 3

    def test_optional_page_number(self):
        fix = FixSummary(
            id="fix-002",
            category="structure",
            severity="minor",
            description="Missing heading",
            confidence=0.95,
            fix_method="rule",
            needs_review=False,
            review_status="auto_approved",
        )
        assert fix.page_number is None
        data = fix.model_dump()
        assert data["page_number"] is None

    def test_exposes_typed_region_provenance(self):
        from tests.test_image_equation_review_gate import _locator

        locator = _locator()
        fix = FixSummary(
            id="fix-region",
            category="structure",
            severity="high",
            description="Printed equation region",
            confidence=0.55,
            fix_method="ai_vision",
            needs_review=True,
            review_status="pending",
            source_kind="image_equation",
            source_locator=locator,
        )

        data = fix.model_dump(mode="json")
        assert data["source_kind"] == "image_equation"
        assert data["source_locator"]["region_id"] == locator["region_id"]
        assert data["source_locator"]["pixel_bbox"] == locator["pixel_bbox"]
        with pytest.raises(ValidationError):
            FixSummary(
                **{
                    **data,
                    "source_locator": {**locator, "provider_payload": "secret"},
                }
            )


class TestQueueItem:
    """Tests for the QueueItem response model."""

    def test_basic_serialization(self):
        now = datetime.now(timezone.utc)
        item = QueueItem(
            scan_id="scan-001",
            file_name="syllabus.pdf",
            department_id="dept-eng",
            total_fixes=10,
            needs_review_count=3,
            lowest_confidence=0.45,
            status="pending",
            created_at=now,
        )
        data = item.model_dump()
        assert data["scan_id"] == "scan-001"
        assert data["file_name"] == "syllabus.pdf"
        assert data["department_id"] == "dept-eng"
        assert data["total_fixes"] == 10
        assert data["needs_review_count"] == 3
        assert data["lowest_confidence"] == 0.45
        assert data["status"] == "pending"
        assert data["created_at"] == now

    def test_optional_department_id(self):
        now = datetime.now(timezone.utc)
        item = QueueItem(
            scan_id="scan-002",
            file_name="lecture.pdf",
            total_fixes=5,
            needs_review_count=0,
            lowest_confidence=0.90,
            status="approved",
            created_at=now,
        )
        assert item.department_id is None


class TestQueueStats:
    """Tests for the QueueStats response model."""

    def test_serialization(self):
        stats = QueueStats(
            pending=15,
            in_review=3,
            approved=42,
            rejected=5,
            total=65,
        )
        data = stats.model_dump()
        assert data["pending"] == 15
        assert data["in_review"] == 3
        assert data["approved"] == 42
        assert data["rejected"] == 5
        assert data["total"] == 65

    def test_all_zeros(self):
        stats = QueueStats(
            pending=0,
            in_review=0,
            approved=0,
            rejected=0,
            total=0,
        )
        assert stats.total == 0


class TestDocumentReview:
    """Tests for the DocumentReview response model."""

    def test_serialization_with_fixes(self):
        fixes = [
            FixSummary(
                id="f1",
                category="images",
                severity="critical",
                description="Missing alt text",
                confidence=0.55,
                fix_method="ai_vision",
                needs_review=True,
                review_status="pending",
            ),
            FixSummary(
                id="f2",
                category="structure",
                severity="minor",
                description="Missing heading tag",
                confidence=0.95,
                fix_method="rule",
                needs_review=False,
                review_status="auto_approved",
            ),
        ]
        doc = DocumentReview(
            scan_id="scan-001",
            file_name="test.pdf",
            fixes=fixes,
            matterhorn_total=10,
            matterhorn_passed=8,
            matterhorn_failed=2,
            validator_result="recorded_checkpoint_failures",
        )
        data = doc.model_dump()
        assert data["scan_id"] == "scan-001"
        assert data["file_name"] == "test.pdf"
        assert len(data["fixes"]) == 2
        assert data["matterhorn_total"] == 10
        assert data["matterhorn_passed"] == 8
        assert data["matterhorn_failed"] == 2
        assert data["validator_result"] == "recorded_checkpoint_failures"
        assert "compliance_level" not in data

    def test_empty_fixes_list(self):
        doc = DocumentReview(
            scan_id="scan-002",
            file_name="empty.pdf",
            fixes=[],
            matterhorn_total=0,
            matterhorn_passed=0,
            matterhorn_failed=0,
            validator_result="not_run",
        )
        assert doc.fixes == []
        assert doc.validator_result == "not_run"


# ---------------------------------------------------------------------------
# Request model validation tests
# ---------------------------------------------------------------------------


class TestFixAction:
    """Tests for the FixAction request model."""

    def test_approve_action(self):
        action = FixAction(action="approve", notes="Looks good")
        assert action.action == "approve"
        assert action.notes == "Looks good"
        assert action.edited_content is None

    def test_reject_action(self):
        action = FixAction(action="reject", notes="Incorrect alt text")
        assert action.action == "reject"
        assert action.notes == "Incorrect alt text"

    def test_edit_action(self):
        action = FixAction(
            action="edit",
            notes="Improved alt text",
            edited_content="A bar chart showing enrollment trends",
        )
        assert action.action == "edit"
        assert action.edited_content == "A bar chart showing enrollment trends"

    def test_minimal_action(self):
        action = FixAction(action="approve")
        assert action.notes is None
        assert action.edited_content is None

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            FixAction(action="invalid")


class TestBatchAction:
    """Tests for the BatchAction request model."""

    def test_approve_by_confidence(self):
        batch = BatchAction(
            action="approve",
            min_confidence=0.90,
            notes="High confidence batch approval",
        )
        assert batch.action == "approve"
        assert batch.min_confidence == 0.90
        assert batch.category is None
        assert batch.fix_ids is None

    def test_reject_by_category(self):
        batch = BatchAction(
            action="reject",
            category="images",
            notes="Need manual image descriptions",
        )
        assert batch.action == "reject"
        assert batch.category == "images"

    def test_approve_by_ids(self):
        batch = BatchAction(
            action="approve",
            fix_ids=["fix-1", "fix-2", "fix-3"],
        )
        assert len(batch.fix_ids) == 3

    def test_minimal_batch(self):
        batch = BatchAction(action="approve")
        assert batch.min_confidence is None
        assert batch.category is None
        assert batch.fix_ids is None
        assert batch.notes is None

    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            BatchAction(action="edit")

    def test_invalid_action_string_rejected(self):
        with pytest.raises(ValidationError):
            BatchAction(action="invalid")


class TestAuditEntry:
    """Tests for the AuditEntry response model."""

    def test_serialization(self):
        now = datetime.now(timezone.utc)
        entry = AuditEntry(
            id="audit-001",
            action="fix_approve",
            user_name="Jane Doe",
            details={"notes": "LGTM", "edited": False},
            created_at=now,
        )
        data = entry.model_dump()
        assert data["id"] == "audit-001"
        assert data["action"] == "fix_approve"
        assert data["user_name"] == "Jane Doe"
        assert data["details"]["notes"] == "LGTM"
        assert data["created_at"] == now

    def test_system_user(self):
        now = datetime.now(timezone.utc)
        entry = AuditEntry(
            id="audit-002",
            action="batch_approve",
            user_name="System",
            details={"count": 5, "min_confidence": 0.9, "category": None},
            created_at=now,
        )
        assert entry.user_name == "System"

    def test_optional_details(self):
        now = datetime.now(timezone.utc)
        entry = AuditEntry(
            id="audit-003",
            action="fix_reject",
            created_at=now,
        )
        assert entry.user_name is None
        assert entry.details is None


class TestReviewResponse:
    """Tests for the ReviewResponse response model."""

    def test_serialization(self):
        resp = ReviewResponse(
            status="ok",
            fix_id="fix-001",
            review_status="approved",
        )
        data = resp.model_dump()
        assert data["status"] == "ok"
        assert data["fix_id"] == "fix-001"
        assert data["review_status"] == "approved"

    def test_rejected_status(self):
        resp = ReviewResponse(
            status="ok",
            fix_id="fix-002",
            review_status="rejected",
        )
        assert resp.review_status == "rejected"


class TestBatchResponse:
    """Tests for the BatchResponse response model."""

    def test_serialization(self):
        resp = BatchResponse(status="ok", affected=5)
        data = resp.model_dump()
        assert data["status"] == "ok"
        assert data["affected"] == 5

    def test_zero_affected(self):
        resp = BatchResponse(status="ok", affected=0)
        assert resp.affected == 0
