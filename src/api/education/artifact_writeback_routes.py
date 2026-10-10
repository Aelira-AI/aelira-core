"""Explicit human-triggered publication of approved managed artifacts."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.orm import Session

from ...auth.dependencies import AuthenticatedPrincipal, get_authenticated_principal
from ...db.database import get_db_dependency
from ...jobs.remediation_job import _queue_upload_job
from ...services.remediation_artifact_service import ArtifactError
from .remediation_routes import _managed_artifact_authority

router = APIRouter()
WRITEBACK_PROVIDERS = frozenset({"google", "microsoft", "blackboard"})


class ArtifactWritebackRequest(BaseModel):
    create_new_version: StrictBool = Field(
        default=True,
        description=(
            "Use a _remediated filename when true; otherwise use the original name. "
            "Google and Blackboard create a new file/content item. Microsoft may "
            "replace an existing file at the selected folder/name."
        ),
    )


class SelectedArtifactWriteback(ArtifactWritebackRequest):
    scan_id: str = Field(min_length=1, max_length=36)
    artifact_id: str = Field(min_length=1, max_length=36)


class BatchArtifactWritebackRequest(BaseModel):
    items: list[SelectedArtifactWriteback] = Field(min_length=1, max_length=100)


def _queue_selected_writebacks(
    db: Session,
    principal: AuthenticatedPrincipal,
    items: list[SelectedArtifactWriteback],
) -> list[dict[str, Any]]:
    identities = [(item.scan_id, item.artifact_id) for item in items]
    if len(set(identities)) != len(identities):
        raise HTTPException(status_code=400, detail="Duplicate artifact selection")
    # Authorize the whole explicit selection before any enqueue or mutation.
    authorities = [
        _managed_artifact_authority(
            db,
            scan_id=item.scan_id,
            artifact_id=item.artifact_id,
            principal=principal,
        )
        for item in items
    ]
    if any(
        cloud is None or cloud.provider not in WRITEBACK_PROVIDERS
        for _, cloud, _ in authorities
    ):
        raise HTTPException(
            status_code=501,
            detail="Artifact write-back is unsupported for this provider; use download",
        )
    results = []
    try:
        for item, (_, cloud, artifact) in zip(items, authorities):
            assert cloud is not None  # The complete selection was validated above.
            job_id = _queue_upload_job(
                db=db,
                cloud_file_id=str(cloud.id),
                department_id=principal.department_id,
                provider=str(cloud.provider),
                artifact_id=str(artifact.id),
                remediation_job_id=(
                    str(artifact.remediation_job_id)
                    if artifact.remediation_job_id is not None
                    else None
                ),
                scan_id=item.scan_id,
                credential_id=str(cloud.credential_id),
                create_new_version=item.create_new_version,
                requested_by_ref=f"{principal.auth_method}:{principal.user_id}",
            )
            results.append({"artifact_id": artifact.id, "job_id": job_id})
        db.commit()
    except (ArtifactError, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "A prior write-back requires reconciliation before another request"
                if str(exc) == "writeback_reconciliation_required"
                else "Current human artifact approval is required before write-back"
            ),
        ) from None
    return results


@router.post("/scans/{scan_id}/artifacts/{artifact_id}/writeback", status_code=202)
def write_back_artifact(
    scan_id: Annotated[str, Path(min_length=1, max_length=36)],
    artifact_id: Annotated[str, Path(min_length=1, max_length=36)],
    request: ArtifactWritebackRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    result = _queue_selected_writebacks(
        db,
        principal,
        [
            SelectedArtifactWriteback(
                scan_id=scan_id,
                artifact_id=artifact_id,
                create_new_version=request.create_new_version,
            )
        ],
    )[0]
    return {"status": "queued", **result}


@router.post("/artifacts/batch-writeback", status_code=202)
def batch_write_back_artifacts(
    request: BatchArtifactWritebackRequest,
    db: Session = Depends(get_db_dependency),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    results = _queue_selected_writebacks(db, principal, request.items)
    return {"status": "queued", "queued_count": len(results), "items": results}
