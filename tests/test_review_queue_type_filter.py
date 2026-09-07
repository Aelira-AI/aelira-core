"""Tests for scan_type filter and per-type breakdown in review queue API models."""

from datetime import datetime, timezone

from src.api.review_routes import DepartmentSummary, QueueItem, QueueStats


class TestQueueItemScanType:
    """Tests for QueueItem.scan_type field."""

    def test_queue_item_has_scan_type_field(self):
        """QueueItem model should include a scan_type field."""
        item = QueueItem(
            scan_id="scan-1",
            file_name="test.pdf",
            scan_type="pdf",
            total_fixes=3,
            needs_review_count=1,
            lowest_confidence=0.65,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        assert item.scan_type == "pdf"

    def test_queue_item_scan_type_is_optional(self):
        """QueueItem.scan_type should default to None when not provided."""
        item = QueueItem(
            scan_id="scan-2",
            file_name="test.docx",
            total_fixes=5,
            needs_review_count=2,
            lowest_confidence=0.4,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        assert item.scan_type is None


class TestQueueStatsByType:
    """Tests for QueueStats.by_type field."""

    def test_queue_stats_has_by_type_field(self):
        """QueueStats model should include a by_type field."""
        stats = QueueStats(
            pending=10,
            approved=5,
            rejected=1,
            total=16,
            by_type={"pdf": 8, "docx": 6, "web": 4},
        )
        assert stats.by_type == {"pdf": 8, "docx": 6, "web": 4}

    def test_queue_stats_by_type_is_optional(self):
        """QueueStats.by_type should default to None when not provided."""
        stats = QueueStats(
            pending=10,
            approved=5,
            rejected=1,
            total=16,
        )
        assert stats.by_type is None


class TestDepartmentSummaryByType:
    """Tests for DepartmentSummary.by_type field."""

    def test_department_summary_has_by_type_field(self):
        """DepartmentSummary model should include a by_type field."""
        summary = DepartmentSummary(
            total_documents=20,
            reviewed_percent=75.0,
            approved_count=12,
            pending_count=5,
            rejected_count=3,
            avg_confidence=0.82,
            by_type={"pdf": 10, "pptx": 5, "xlsx": 5},
        )
        assert summary.by_type == {"pdf": 10, "pptx": 5, "xlsx": 5}

    def test_department_summary_by_type_is_optional(self):
        """DepartmentSummary.by_type should default to None when not provided."""
        summary = DepartmentSummary(
            total_documents=20,
            reviewed_percent=75.0,
            approved_count=12,
            pending_count=5,
            rejected_count=3,
            avg_confidence=0.82,
        )
        assert summary.by_type is None
