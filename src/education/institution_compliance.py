"""Current institution compliance derived from canonical department inventories."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from sqlalchemy.orm import Session

from ..db.models import Department
from .current_compliance import (
    CurrentComplianceProjection,
    get_department_current_compliance,
)


class InstitutionNotFoundError(LookupError):
    """The authenticated department cannot anchor an institution scope."""


class InvalidComplianceStateError(ValueError):
    """A verified source score cannot be represented truthfully."""


def _rounded(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


@dataclass(frozen=True)
class DocumentCoverage:
    enrolled: int
    scanned: int
    verified: int
    stale: int
    failed: int
    total_coverage_percent: float

    def to_dict(self) -> dict[str, int | float]:
        return {
            "enrolled": self.enrolled,
            "scanned": self.scanned,
            "verified": self.verified,
            "stale": self.stale,
            "failed": self.failed,
            "total_coverage_percent": self.total_coverage_percent,
        }


@dataclass(frozen=True)
class DepartmentCurrentCompliance:
    department_id: str
    department_name: str
    projection: CurrentComplianceProjection


@dataclass(frozen=True)
class DepartmentComplianceRollup:
    department_id: str
    department_name: str
    document_weighted_score: float | None
    score_numerator: float
    coverage: DocumentCoverage

    def to_dict(self) -> dict:
        return {
            "department_id": self.department_id,
            "department_name": self.department_name,
            "document_weighted_score": self.document_weighted_score,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True)
class InstitutionComplianceRollup:
    institution_name: str
    document_weighted_score: float | None
    flat_department_mean: float | None
    coverage: DocumentCoverage
    departments: tuple[DepartmentComplianceRollup, ...]
    flat_department_mean_label: str = "Secondary: flat department mean"

    def to_dict(self) -> dict:
        return {
            "institution_name": self.institution_name,
            "document_weighted_score": self.document_weighted_score,
            "document_weighted_score_label": "Document-weighted institution score",
            "flat_department_mean": self.flat_department_mean,
            "flat_department_mean_label": self.flat_department_mean_label,
            "coverage": self.coverage.to_dict(),
            "departments": [department.to_dict() for department in self.departments],
        }


def _coverage(projection: CurrentComplianceProjection) -> DocumentCoverage:
    enrolled = projection.enrolled_document_count
    return DocumentCoverage(
        enrolled=enrolled,
        scanned=projection.scanned_document_count,
        verified=projection.verified_document_count,
        stale=projection.stale_document_count,
        failed=projection.failed_document_count,
        total_coverage_percent=(
            round(projection.verified_document_count / enrolled * 100, 2)
            if enrolled
            else 0.0
        ),
    )


def _verified_scores(projection: CurrentComplianceProjection) -> list[float]:
    scores: list[float] = []
    for document in projection.verified_documents:
        try:
            score = float(document.result.compliance_score)
        except (TypeError, ValueError) as error:
            raise InvalidComplianceStateError(
                "Verified compliance score is invalid"
            ) from error
        if not isfinite(score) or score < 0 or score > 100:
            raise InvalidComplianceStateError("Verified compliance score is invalid")
        scores.append(score)
    return scores


def aggregate_institution_compliance(
    institution_name: str,
    departments: Sequence[DepartmentCurrentCompliance],
) -> InstitutionComplianceRollup:
    """Combine department projections without flattening department sizes."""

    department_rollups: list[DepartmentComplianceRollup] = []
    institution_numerator = 0.0
    enrolled = scanned = verified = stale = failed = 0

    for department in sorted(departments, key=lambda item: item.department_id):
        projection = department.projection
        coverage = _coverage(projection)
        scores = _verified_scores(projection)
        score_numerator = sum(scores)
        department_rollups.append(
            DepartmentComplianceRollup(
                department_id=department.department_id,
                department_name=department.department_name,
                document_weighted_score=_rounded(
                    score_numerator / len(scores) if scores else None
                ),
                score_numerator=score_numerator,
                coverage=coverage,
            )
        )
        institution_numerator += score_numerator
        enrolled += coverage.enrolled
        scanned += coverage.scanned
        verified += coverage.verified
        stale += coverage.stale
        failed += coverage.failed

    department_scores = [
        department.document_weighted_score
        for department in department_rollups
        if department.document_weighted_score is not None
    ]
    return InstitutionComplianceRollup(
        institution_name=institution_name.strip(),
        document_weighted_score=_rounded(
            institution_numerator / verified if verified else None
        ),
        flat_department_mean=_rounded(
            sum(department_scores) / len(department_scores)
            if department_scores
            else None
        ),
        coverage=DocumentCoverage(
            enrolled=enrolled,
            scanned=scanned,
            verified=verified,
            stale=stale,
            failed=failed,
            total_coverage_percent=(
                round(verified / enrolled * 100, 2) if enrolled else 0.0
            ),
        ),
        departments=tuple(department_rollups),
    )


def get_institution_current_compliance(
    db: Session, department_id: str
) -> InstitutionComplianceRollup:
    """Resolve institution scope from the authenticated department only."""

    anchor = db.query(Department).filter(Department.id == department_id).first()
    if anchor is None:
        raise InstitutionNotFoundError("Department not found")

    departments = (
        db.query(Department)
        .filter(Department.institution_scope_id == anchor.institution_scope_id)
        .order_by(Department.id.asc())
        .all()
    )
    projections = [
        DepartmentCurrentCompliance(
            department_id=str(department.id),
            department_name=department.name,
            projection=get_department_current_compliance(db, str(department.id)),
        )
        for department in departments
    ]
    return aggregate_institution_compliance(anchor.institution, projections)
