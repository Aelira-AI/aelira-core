"""Durable image/chart analysis lifecycle and canonical review binding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import ScanFix, VisualAnalysis, VisualAnalysisAttempt
from src.education.image_alt_text import ImageAltTextGenerator
from src.education.multimedia_processor import MultimediaProcessor
from src.services.scan_fix_service import bind_fix_review_decision
from src.services.visual_analysis_service import (
    DurableVisualAnalysisRecorder,
    SAFE_FAILURE_CATEGORIES,
    VisualAnalysisRequest,
    claim_visual_analysis,
    complete_visual_analysis,
    enqueue_visual_analysis,
    fail_visual_analysis,
    recover_stale_visual_analyses,
    visual_request_digest,
)

HASH = "a" * 64
NOW = datetime(2026, 9, 5, 4, 30, tzinfo=timezone.utc)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(output, format="PNG")
    return output.getvalue()


def _db() -> Session:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("departments", metadata, Column("id", String(36), primary_key=True))
    Table(
        "scans",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("department_id", String(36), nullable=False),
        Column("current_remediation_artifact_id", String(36)),
    )
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table(
        "cloud_files",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("current_remediation_artifact_id", String(36)),
        Column("writeback_status", String(32)),
        Column("has_remediated_version", Boolean),
        Column("remediation_origin", String(32)),
    )
    Table(
        "remediation_artifacts",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("scan_id", String(36), nullable=False),
        Column("cloud_file_id", String(36)),
        Column("review_status", String(32)),
        Column("written_back_at", DateTime(timezone=True)),
        Column("approval_checksum", String(64)),
        Column("approval_review_digest", String(64)),
        Column("approved_by_id", String(36)),
        Column("approved_by_ref", String(255)),
        Column("approved_at", DateTime(timezone=True)),
    )
    ScanFix.__table__.to_metadata(metadata)
    VisualAnalysis.__table__.to_metadata(metadata)
    VisualAnalysisAttempt.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    db = Session(engine)
    db.execute(metadata.tables["departments"].insert(), {"id": "dept-1"})
    db.execute(
        metadata.tables["scans"].insert(),
        {"id": "scan-1", "department_id": "dept-1"},
    )
    db.commit()
    return db


def _approve(fix: ScanFix) -> None:
    bind_fix_review_decision(fix, "approve")
    fix.review_status = "approved"
    fix.reviewed_by = "reviewer-1"
    fix.reviewed_at = NOW


def _request(**overrides) -> VisualAnalysisRequest:
    values = {
        "department_id": "dept-1",
        "scan_id": "scan-1",
        "source_kind": "image",
        "parent_artifact_sha256": HASH,
        "source_bytes": _png_bytes(),
        "source_locator": {
            "kind": "page_image",
            "page_number": 2,
            "image_xref": 17,
        },
        "purpose": "alt_text",
        "max_attempts": 3,
    }
    values.update(overrides)
    return VisualAnalysisRequest(**values)


@pytest.mark.parametrize("source_kind", ["image", "chart"])
def test_enqueue_persists_stable_source_identity(source_kind):
    with _db() as db:
        request = _request(source_kind=source_kind)
        analysis = enqueue_visual_analysis(db, request, now=NOW)
        db.commit()

        assert analysis.status == "queued"
        assert analysis.source_kind == source_kind
        assert analysis.parent_artifact_sha256 == HASH
        assert (
            analysis.source_sha256 == hashlib.sha256(request.source_bytes).hexdigest()
        )
        assert analysis.source_locator == request.source_locator
        assert len(analysis.request_digest) == 64


@pytest.mark.parametrize("source_kind", ["photo", "diagram", "IMAGE", ""])
def test_request_rejects_unknown_source_kind(source_kind):
    with pytest.raises(ValueError, match="source kind"):
        _request(source_kind=source_kind)


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "page_image", "page_number": 1, "image_xref": 4},
        {"kind": "slide_shape", "slide_number": 3, "shape_id": 9},
        {"kind": "media_frame", "timestamp_ms": 1250},
    ],
)
def test_request_accepts_bounded_locator_variants(locator):
    assert _request(source_locator=locator).source_locator == locator


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "page_image", "page_number": 0, "image_xref": 4},
        {"kind": "slide_shape", "slide_number": 1, "shape_id": -1},
        {"kind": "media_frame", "timestamp_ms": -1},
        {"kind": "page_image", "page_number": 1, "image_xref": 4, "path": "/tmp/a"},
        {"kind": "unknown", "page_number": 1},
    ],
)
def test_request_rejects_invalid_or_path_bearing_locator(locator):
    with pytest.raises(ValueError, match="locator"):
        _request(source_locator=locator)


def test_request_digest_is_canonical_and_tenant_scoped():
    first = _request()
    reordered = _request(
        source_locator={"image_xref": 17, "page_number": 2, "kind": "page_image"}
    )
    foreign = _request(department_id="dept-2")

    assert visual_request_digest(first) == visual_request_digest(reordered)
    assert visual_request_digest(first) != visual_request_digest(foreign)


def test_request_digest_distinguishes_prompt_affecting_inputs():
    first = _request(request_fingerprint="1" * 64)
    changed = _request(request_fingerprint="2" * 64)

    assert visual_request_digest(first) != visual_request_digest(changed)


def test_enqueue_is_idempotent():
    with _db() as db:
        first = enqueue_visual_analysis(db, _request(), now=NOW)
        again = enqueue_visual_analysis(db, _request(), now=NOW)
        assert again.id == first.id
        assert db.query(VisualAnalysis).count() == 1


def test_claim_requires_current_token_and_records_attempt_chronology():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        db.commit()

        assert analysis.status == "running"
        assert analysis.claim_token == claim.claim_token
        attempt = db.get(VisualAnalysisAttempt, claim.attempt_id)
        assert attempt.attempt_number == 1
        assert attempt.purpose == "alt_text"
        assert attempt.started_at.replace(tzinfo=timezone.utc) == NOW

        stale = SimpleNamespace(**{**claim.__dict__, "claim_token": "stale"})
        with pytest.raises(ValueError, match="ownership"):
            complete_visual_analysis(
                db,
                stale,
                proposal={"alt_text": "A labelled campus map"},
                provider="gemini",
                model="vision-safe",
                now=NOW + timedelta(seconds=2),
            )


def test_success_routes_proposal_to_one_pending_scan_fix():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        completed = complete_visual_analysis(
            db,
            claim,
            proposal={"alt_text": "A labelled campus map"},
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=2),
        )
        db.commit()

        assert completed.status == "review_required"
        assert completed.proposal == {"alt_text": "A labelled campus map"}
        assert completed.proposal_sha256
        attempt = db.query(VisualAnalysisAttempt).one()
        assert attempt.status == "succeeded"
        assert attempt.finished_at.replace(tzinfo=timezone.utc) == NOW + timedelta(
            seconds=2
        )
        assert attempt.provider == "gemini"
        assert attempt.model == "vision-safe"
        fix = db.get(ScanFix, completed.review_fix_id)
        assert fix.fixed_content == "A labelled campus map"
        assert fix.needs_review is True
        assert fix.review_status == "pending"
        assert fix.approved_review_digest is None


def test_replayed_success_does_not_duplicate_or_reset_unchanged_approval():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        first_claim = claim_visual_analysis(db, analysis.id, now=NOW)
        complete_visual_analysis(
            db,
            first_claim,
            proposal={"alt_text": "A labelled campus map"},
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=1),
        )
        fix = db.get(ScanFix, analysis.review_fix_id)
        _approve(fix)
        approved_digest = fix.approved_review_digest
        analysis.status = "retryable_failure"
        db.commit()

        second_claim = claim_visual_analysis(
            db, analysis.id, now=NOW + timedelta(seconds=2)
        )
        complete_visual_analysis(
            db,
            second_claim,
            proposal={"alt_text": "A labelled campus map"},
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=3),
        )
        db.commit()

        assert db.query(ScanFix).count() == 1
        assert db.get(ScanFix, fix.id).review_status == "approved"
        assert db.get(ScanFix, fix.id).approved_review_digest == approved_digest
        assert db.query(VisualAnalysisAttempt).count() == 2


def test_changed_proposal_invalidates_approval_without_duplicate_fix():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        complete_visual_analysis(
            db,
            claim,
            proposal={"alt_text": "Old proposal"},
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=1),
        )
        fix = db.get(ScanFix, analysis.review_fix_id)
        _approve(fix)
        analysis.status = "retryable_failure"
        db.commit()

        retry = claim_visual_analysis(db, analysis.id, now=NOW + timedelta(seconds=2))
        complete_visual_analysis(
            db,
            retry,
            proposal={"alt_text": "New proposal"},
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=3),
        )
        db.commit()

        assert db.query(ScanFix).count() == 1
        assert fix.fixed_content == "New proposal"
        assert fix.review_status == "pending"
        assert fix.approved_review_digest is None


@pytest.mark.parametrize(
    ("retryable", "expected"),
    [(True, "retryable_failure"), (False, "terminal_failure")],
)
def test_failure_is_sanitized_and_classified(retryable, expected):
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        failed = fail_visual_analysis(
            db,
            claim,
            failure_category="provider_unavailable",
            retryable=retryable,
            provider="gemini",
            model="vision-safe",
            now=NOW + timedelta(seconds=1),
        )
        db.commit()

        assert failed.status == expected
        attempt = db.query(VisualAnalysisAttempt).one()
        assert attempt.purpose == "alt_text"
        assert attempt.provider == "gemini"
        assert attempt.model == "vision-safe"
        assert attempt.failure_category in SAFE_FAILURE_CATEGORIES
        assert not hasattr(attempt, "error_message")
        assert failed.proposal is None


def test_unknown_failure_category_is_rejected_without_persisting_raw_error():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        with pytest.raises(ValueError, match="failure category"):
            fail_visual_analysis(
                db,
                claim,
                failure_category="/srv/aelira token=secret upstream exploded",
                retryable=True,
                now=NOW + timedelta(seconds=1),
            )


def test_retry_exhaustion_becomes_terminal():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(max_attempts=1), now=NOW)
        claim = claim_visual_analysis(db, analysis.id, now=NOW)
        failed = fail_visual_analysis(
            db,
            claim,
            failure_category="provider_unavailable",
            retryable=True,
            now=NOW + timedelta(seconds=1),
        )
        assert failed.status == "terminal_failure"


def test_recovery_requeues_only_stale_running_work():
    with _db() as db:
        stale = enqueue_visual_analysis(db, _request(), now=NOW)
        stale_claim = claim_visual_analysis(db, stale.id, now=NOW)
        stale.lease_expires_at = NOW + timedelta(seconds=10)

        live_request = _request(purpose="chart_description", source_kind="chart")
        live = enqueue_visual_analysis(db, live_request, now=NOW)
        live_claim = claim_visual_analysis(db, live.id, now=NOW)
        live.lease_expires_at = NOW + timedelta(minutes=10)
        db.commit()

        recovered = recover_stale_visual_analyses(db, now=NOW + timedelta(minutes=1))
        db.commit()

        assert recovered == [stale.id]
        assert stale.status == "retryable_failure"
        assert live.status == "running"
        stale_attempt = db.get(VisualAnalysisAttempt, stale_claim.attempt_id)
        live_attempt = db.get(VisualAnalysisAttempt, live_claim.attempt_id)
        assert stale_attempt.failure_category == "worker_interrupted"
        assert live_attempt.failure_category is None


def test_database_constraints_reject_invalid_digest_and_status():
    with _db() as db:
        analysis = enqueue_visual_analysis(db, _request(), now=NOW)
        db.commit()
        analysis.parent_artifact_sha256 = "NOT-A-DIGEST"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        analysis = db.get(VisualAnalysis, analysis.id)
        analysis.status = "invented"
        with pytest.raises(IntegrityError):
            db.commit()


class _VisionClient:
    provider = "gemini"

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def analyze_image_sync(self, **_kwargs):
        self.calls += 1
        return {
            "success": True,
            "content": self.content,
            "provider": "gemini",
            "model": "vision-safe",
        }


def _recorder(db: Session) -> DurableVisualAnalysisRecorder:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    return DurableVisualAnalysisRecorder(
        factory,
        department_id="dept-1",
        scan_id="scan-1",
        parent_artifact_sha256=HASH,
    )


@pytest.mark.asyncio
async def test_image_generator_records_alt_text_lifecycle(tmp_path):
    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        client = _VisionClient("A labelled campus map")
        generator = ImageAltTextGenerator(
            lms_client=client, visual_analysis_recorder=_recorder(db)
        )

        result = await generator.generate_alt_text(
            str(image_path),
            analysis_locator={
                "kind": "page_image",
                "page_number": 2,
                "image_xref": 17,
            },
        )

        assert result["success"] is True
        assert result["analysis_status"] == "review_required"
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.purpose == "alt_text"
            assert analysis.source_kind == "image"
            assert analysis.review_fix_id is not None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_image_generator_records_chart_lifecycle(tmp_path):
    db = _db()
    try:
        image_path = tmp_path / "chart.png"
        image_path.write_bytes(_png_bytes())
        client = _VisionClient(
            '{"chart_type":"line graph","title":"Enrollment",'
            '"short_description":"Enrollment rises each year",'
            '"detailed_description":"Enrollment rises from 2022 to 2026.",'
            '"data_summary":{},"insights":[],"visual_elements":{},'
            '"accessibility_note":"Use a long description"}'
        )
        generator = ImageAltTextGenerator(
            lms_client=client, visual_analysis_recorder=_recorder(db)
        )

        result = await generator.describe_chart_or_graph(
            str(image_path),
            analysis_locator={
                "kind": "slide_shape",
                "slide_number": 3,
                "shape_id": 9,
            },
        )

        assert result["analysis_status"] == "review_required"
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.purpose == "chart_description"
            assert analysis.source_kind == "chart"
            assert (
                verify.get(ScanFix, analysis.review_fix_id).review_status == "pending"
            )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_comprehensive_analysis_propagates_locator_to_nested_requests(tmp_path):
    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        client = _VisionClient(
            '{"is_decorative":false,"image_purpose":"informative",'
            '"confidence":0.9,"reasoning":"instructional image",'
            '"recommended_alt":"A labelled campus map","visual_elements":[]}'
        )
        generator = ImageAltTextGenerator(
            lms_client=client, visual_analysis_recorder=_recorder(db)
        )
        locator = {"kind": "page_image", "page_number": 2, "image_xref": 17}

        result = await generator.analyze_image_comprehensive(
            str(image_path),
            context="Campus orientation",
            analysis_locator=locator,
        )

        assert result["success"] is True
        assert result["analysis_status"] == "review_required"
        assert result["review_fix_id"]
        with Session(db.get_bind()) as verify:
            analyses = verify.query(VisualAnalysis).order_by(VisualAnalysis.purpose)
            assert [analysis.purpose for analysis in analyses] == [
                "alt_text",
                "image_type",
            ]
            assert all(analysis.source_locator == locator for analysis in analyses)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_recorder_fails_closed_before_provider_for_missing_source(tmp_path):
    db = _db()
    called = False

    async def invoke():
        nonlocal called
        called = True
        return {"success": True, "alt_text": "invented"}

    try:
        result = await _recorder(db).execute(
            purpose="alt_text",
            source_kind="image",
            source_path=str(tmp_path / "missing.png"),
            source_locator={
                "kind": "page_image",
                "page_number": 2,
                "image_xref": 17,
            },
            invoke=invoke,
        )

        assert called is False
        assert result == {
            "success": False,
            "analysis_id": result["analysis_id"],
            "analysis_status": "terminal_failure",
            "review_fix_id": None,
            "error": "source_unavailable",
        }
    finally:
        db.close()


@pytest.mark.asyncio
async def test_recorder_recovers_expired_claim_before_retrying(tmp_path):
    db = _db()
    calls = 0

    async def invoke():
        nonlocal calls
        calls += 1
        return {
            "success": True,
            "alt_text": "A labelled campus map",
            "provider": "gemini",
            "model": "vision-safe",
        }

    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        analysis = enqueue_visual_analysis(db, _request())
        claim_visual_analysis(db, analysis.id)
        analysis.lease_expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.commit()

        result = await _recorder(db).execute(
            purpose="alt_text",
            source_kind="image",
            source_path=str(image_path),
            source_locator={
                "kind": "page_image",
                "page_number": 2,
                "image_xref": 17,
            },
            invoke=invoke,
        )

        assert result["analysis_status"] == "review_required"
        assert calls == 1
        with Session(db.get_bind()) as verify:
            attempts = verify.query(VisualAnalysisAttempt).order_by(
                VisualAnalysisAttempt.attempt_number
            )
            assert [attempt.status for attempt in attempts] == [
                "retryable_failure",
                "succeeded",
            ]
    finally:
        db.close()


def test_multimedia_fallback_records_audio_description_lifecycle(tmp_path, monkeypatch):
    db = _db()
    try:
        image_path = tmp_path / "frame.png"
        image_path.write_bytes(_png_bytes())
        client = _VisionClient("A lecturer points to a projected equation")
        processor = MultimediaProcessor(
            llm_client=client,
            visual_analysis_recorder=_recorder(db),
        )
        monkeypatch.setattr(processor, "_get_image_generator", lambda: None)

        result = processor._describe_keyframe(client, str(image_path), 12.5)

        assert result is not None
        assert result.description == "A lecturer points to a projected equation"
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.purpose == "audio_description"
            assert analysis.source_locator == {
                "kind": "media_frame",
                "timestamp_ms": 12500,
            }
            assert analysis.status == "review_required"
    finally:
        db.close()
