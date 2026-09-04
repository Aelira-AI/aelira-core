"""Color-vision-deficiency evidence persistence and aggregation contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from alembic.config import Config
from alembic.script import ScriptDirectory

from src.api.education.compliance_routes import get_department_compliance_stats
from src.db.models import Department, ScanResult, ScanStatus, ScanType, User
from src.db.scan_service import ScanService
from src.education.compliance_dashboard import ComplianceDashboard
from src.education.current_compliance import project_current_documents
from src.education.cvd_metrics import (
    aggregate_cvd_metrics,
    serialize_cvd_analysis,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


class DumpableAnalysis:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.payload


def _scan(scan_id: str, *, file_hash: str, created_offset: int = 0):
    created_at = NOW + timedelta(minutes=created_offset)
    return SimpleNamespace(
        id=scan_id,
        department_id="dept-a",
        document_id=None,
        document_source="standalone",
        file_hash=file_hash,
        file_name="document.pdf",
        scan_type=ScanType.PDF,
        status=ScanStatus.COMPLETED,
        remediation_outcome=None,
        created_at=created_at,
        completed_at=created_at,
        pages=1,
        user_id="user-a",
    )


def _result(scan_id: str, cvd_analysis):
    return SimpleNamespace(
        scan_id=scan_id,
        compliance_score=90,
        critical_issues=0,
        high_issues=0,
        medium_issues=0,
        low_issues=0,
        issues=[],
        cvd_analysis=cvd_analysis,
    )


def _department_db(projection):
    db = MagicMock()

    def query(model):
        result = MagicMock()
        if model is Department:
            result.filter.return_value.first.return_value = SimpleNamespace(
                id="dept-a",
                name="Department A",
                institution="Institution A",
                country_code="AU",
                regulatory_framework=None,
                custom_deadline=None,
            )
        elif model is User:
            result.filter.return_value.count.return_value = 1
        else:
            raise AssertionError(f"unexpected query for {model}")
        return result

    db.query.side_effect = query
    return db


def test_serializes_direct_and_page_level_cvd_evidence_without_loss():
    issue = {"color_blindness_type": "deuteranopia", "severity": "serious"}
    analysis = {"accessible_for_all": False, "issues": [issue]}

    direct = SimpleNamespace(cvd_analysis=[DumpableAnalysis(analysis)])
    assert serialize_cvd_analysis(direct) == [analysis]

    paged = SimpleNamespace(
        pages=[
            SimpleNamespace(cvd_analysis=[]),
            SimpleNamespace(cvd_analysis=[DumpableAnalysis(analysis)]),
        ]
    )
    assert serialize_cvd_analysis(paged) == [analysis]
    assert (
        serialize_cvd_analysis(
            SimpleNamespace(pages=[SimpleNamespace(cvd_analysis=None)])
        )
        is None
    )
    assert serialize_cvd_analysis(SimpleNamespace()) is None


def test_aggregation_uses_only_current_analyzed_verified_documents():
    scans = [
        _scan("old", file_hash="a" * 64, created_offset=0),
        _scan("current", file_hash="a" * 64, created_offset=10),
        _scan("clean", file_hash="b" * 64),
        _scan("unknown", file_hash="c" * 64),
    ]
    results = [
        _result("old", [{"issues": [{}, {}, {}, {}]}]),
        _result("current", [{"issues": [{}, {}, {}]}]),
        _result("clean", []),
        _result("unknown", None),
    ]
    projection = project_current_documents(scans, results, [])

    metrics = aggregate_cvd_metrics(projection.verified_documents)

    assert metrics.files_analyzed == 2
    assert metrics.affected_files == 1
    assert metrics.issues_total == 3
    assert metrics.accessibility_rate == 50.0


def test_malformed_or_missing_evidence_never_counts_as_accessible():
    documents = [
        SimpleNamespace(result=SimpleNamespace(cvd_analysis=None)),
        SimpleNamespace(result=SimpleNamespace(cvd_analysis={"issues": []})),
        SimpleNamespace(result=SimpleNamespace(cvd_analysis=[{"issues": "bad"}])),
    ]

    metrics = aggregate_cvd_metrics(documents)

    assert metrics.files_analyzed == 0
    assert metrics.affected_files == 0
    assert metrics.issues_total == 0
    assert metrics.accessibility_rate is None


def test_general_and_comprehensive_stats_expose_the_same_cvd_metrics(monkeypatch):
    scans = [
        _scan("affected", file_hash="d" * 64),
        _scan("clean", file_hash="e" * 64),
    ]
    projection = project_current_documents(
        scans,
        [
            _result("affected", [{"issues": [{}, {}]}]),
            _result("clean", []),
        ],
        [],
    )
    monkeypatch.setattr(
        "src.education.compliance_dashboard.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )
    monkeypatch.setattr(
        "src.education.current_compliance.get_department_current_compliance",
        lambda _db, _department_id: projection,
    )
    db = _department_db(projection)

    comprehensive = ComplianceDashboard.get_department_compliance(db, "dept-a")
    general = ScanService.get_department_stats(db, "dept-a")

    assert comprehensive.cvd_files_analyzed == 2
    assert comprehensive.cvd_affected_files == 1
    assert comprehensive.cvd_issues_total == 2
    assert comprehensive.cvd_accessibility_rate == 50.0
    assert general["cvd_files_analyzed"] == 2
    assert general["cvd_affected_files"] == 1
    assert general["cvd_issues_total"] == 2
    assert general["cvd_accessibility_rate"] == 50.0

    response = asyncio.run(
        get_department_compliance_stats(
            "dept-a",
            db=db,
            api_key_info=(None, "user-a", "dept-a"),
        )
    )
    assert response["cvd"] == {
        "files_analyzed": 2,
        "affected_files": 1,
        "issues_total": 2,
        "accessibility_rate": 50.0,
    }


def test_cvd_storage_is_nullable_and_migration_is_the_single_head():
    assert ScanResult.__table__.c.cvd_analysis.nullable is True

    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["20260905_review_deferrals"]
    revision = scripts.get_revision("20260830_cvd_metrics")
    assert revision is not None
    assert revision.down_revision == "20260830_weekly_summary"


def test_every_cvd_capable_scan_persistence_surface_stores_evidence():
    sources = {
        "src/db/scan_service.py": 4,
        "src/api/education/scan_routes.py": 5,
        "src/api/education/web_scan_routes.py": 4,
    }
    for relative_path, minimum_count in sources.items():
        source = (ROOT / relative_path).read_text()
        assert source.count("cvd_analysis=serialize_cvd_analysis(") >= minimum_count
