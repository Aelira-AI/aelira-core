"""Race-safe persistence for uniquely identified Canvas CloudFile rows."""

from collections.abc import Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.models import CloudFile

CANVAS_IDENTITY_CONSTRAINT = "uq_cloud_files_canvas_content_identity"


def invalidate_canvas_derived_state(cloud_file: CloudFile) -> None:
    """Clear scan/remediation authority after the provider source changes."""
    cloud_file.last_scan_id = None
    cloud_file.last_scanned_at = None
    cloud_file.last_compliance_score = None
    cloud_file.needs_rescan = True
    cloud_file.has_remediated_version = False
    cloud_file.remediated_file_id = None
    cloud_file.current_remediation_artifact_id = None
    cloud_file.remediated_body = None
    cloud_file.remediated_compliance_score = None
    cloud_file.remediated_issues_fixed = None
    cloud_file.remediated_issues_remaining = None
    cloud_file.writeback_status = None
    cloud_file.writeback_at = None


def load_canvas_file(
    db: Session,
    *,
    department_id: str,
    course_id: str,
    provider_file_id: str,
) -> CloudFile | None:
    """Load one Canvas file using its normalized composite identity."""
    return (
        db.query(CloudFile)
        .filter(
            CloudFile.department_id == department_id,
            CloudFile.provider == "canvas",
            CloudFile.provider_parent_id == course_id,
            or_(
                CloudFile.content_source == "file",
                CloudFile.content_source.is_(None),
            ),
            CloudFile.provider_file_id == provider_file_id,
        )
        .first()
    )


def add_or_get_canvas_cloud_file(
    db: Session,
    candidate: CloudFile,
    load_existing: Callable[[], CloudFile | None],
) -> CloudFile:
    """Flush a candidate under a savepoint or return the concurrent winner."""
    savepoint = db.begin_nested()
    try:
        db.add(candidate)
        db.flush()
        savepoint.commit()
        return candidate
    except IntegrityError as exc:
        savepoint.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) != CANVAS_IDENTITY_CONSTRAINT:
            raise
        winner = load_existing()
        if winner is None:
            raise
        return winner
