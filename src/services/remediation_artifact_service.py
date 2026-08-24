"""DB-first, descriptor-confined storage for managed remediation artifacts."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
from typing import Any, BinaryIO, Iterator
import uuid
import zipfile

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudOAuthCredentials,
    ContentWritebackLog,
    Department,
    RemediationArtifact,
    ReviewAuditLog,
    Scan,
    ScanFix,
    ScanStatus,
    RemediationOutcome,
    ScanType,
    User,
)


class ArtifactError(Exception):
    """Base class for managed-artifact failures."""


class ArtifactValidationError(ArtifactError):
    """Caller input or source-file validation failed."""


class ArtifactTooLargeError(ArtifactValidationError):
    """Artifact bytes exceed the configured bound."""


class ArtifactMimeError(ArtifactValidationError):
    """Artifact bytes, extension, provider, and scan type are incompatible."""


class ArtifactAuthorizationError(ArtifactError):
    """Artifact authority or state does not permit the requested operation."""


class ArtifactExpiredError(ArtifactAuthorizationError):
    """The artifact is no longer available for use."""


class ArtifactIntegrityError(ArtifactError):
    """Stored metadata and bytes no longer agree."""


class ArtifactInProgressError(ArtifactError):
    """An idempotent job claim exists but publication is still in progress."""


@dataclass(frozen=True)
class ArtifactPublicationRetry:
    """Bounded retry state for one known publication claim."""

    artifact_id: str
    publication_token: str | None = dataclass_field(default=None, repr=False)
    cleanup_complete: bool = False


class ArtifactPublicationRetryable(ArtifactError):
    """A transient publication failure safe for queue retry."""

    def __init__(self, result: ArtifactPublicationRetry):
        self.result = result
        super().__init__("artifact_publication_retryable")


@dataclass(frozen=True)
class ParentCleanupTransaction:
    """Artifact deletes staged for the caller's parent transaction."""

    artifact_ids: tuple[str, ...]
    claimed_at: datetime | None
    reason: str
    owner: str
    removed: int = 0
    missing: int = 0
    cleanup_token: str | None = dataclass_field(default=None, repr=False)

    @property
    def count(self) -> int:
        return len(self.artifact_ids)


_PROVIDERS = {
    "google",
    "microsoft",
    "canvas",
    "blackboard",
    "moodle",
    "brightspace",
    "local",
}
_MIME_BY_SCAN_TYPE = {
    "PDF": {".pdf": "application/pdf"},
    "WORD": {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    "POWERPOINT": {
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    },
    "EXCEL": {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    },
    "LATEX": {".tex": "text/plain"},
    "CANVAS_CONTENT": {".html": "text/html", ".htm": "text/html"},
    "WEBSITE": {".html": "text/html", ".htm": "text/html"},
    "IMAGE": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"},
}
_SCAN_TYPE_ALIASES = {
    **{scan_type.value: scan_type.value for scan_type in ScanType},
    "DOCX": "WORD",
    "PPTX": "POWERPOINT",
    "XLSX": "EXCEL",
    "TEX": "LATEX",
}
_MIME_BY_EXTENSION = {
    extension: mime
    for extensions in _MIME_BY_SCAN_TYPE.values()
    for extension, mime in extensions.items()
}
_COPY_CHUNK_BYTES = 64 * 1024
_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
LOCK_ORDER = (
    Department,
    User,
    CloudOAuthCredentials,
    Scan,
    CloudFile,
    CloudJobQueue,
    RemediationArtifact,
)
_PARENT_CLEANUP_LIMIT = 10_000


@dataclass(frozen=True)
class PreparedRemediationArtifact:
    """Complete expected metadata computed from an already-open source descriptor."""

    id: str
    department_id: str
    scan_id: str
    cloud_file_id: str | None
    remediation_job_id: str | None
    created_by_id: str | None
    provider: str
    scan_type: str
    publication_token: str = dataclass_field(repr=False)
    publication_heartbeat_at: datetime
    published_at: datetime | None
    storage_backend: str
    storage_key: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    lifecycle_status: str
    review_status: str
    approval_checksum: str | None
    approved_by_id: str | None
    approved_by_ref: str | None
    approved_at: datetime | None
    rejected_by_id: str | None
    rejected_by_ref: str | None
    rejected_at: datetime | None
    written_back_at: datetime | None
    cleanup_claimed_at: datetime | None
    deleted_at: datetime | None
    provider_result: dict[str, Any] | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime

    def as_model_kwargs(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactClaim:
    artifact: RemediationArtifact
    owned: bool
    status: str
    publication_token: str | None = dataclass_field(default=None, repr=False)


@dataclass(frozen=True)
class ArtifactPublicationResult:
    """Published artifact plus its in-memory claim for commit cleanup."""

    artifact: RemediationArtifact = dataclass_field(repr=False)
    artifact_id: str
    publication_token: str | None = dataclass_field(default=None, repr=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.artifact, name)


@dataclass(frozen=True)
class _ArtifactMetadata:
    id: str
    department_id: str
    scan_id: str
    cloud_file_id: str | None
    remediation_job_id: str | None
    provider: str


def _canonical_uuid(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{label} must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ArtifactValidationError(f"{label} must be a canonical UUID") from exc
    if str(parsed) != value.lower() or parsed.variant != uuid.RFC_4122:
        raise ArtifactValidationError(f"{label} must be a canonical UUID")
    return str(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sanitize_provider_result(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a detached, canonical JSON value suitable for durable comparison."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ArtifactValidationError("provider_result must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        sanitized = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ArtifactValidationError(
            "provider_result must be JSON serializable"
        ) from exc
    return sanitized


def _normalize_scan_type(scan_type: ScanType | str) -> str:
    if isinstance(scan_type, ScanType):
        name = scan_type.value
    elif isinstance(scan_type, str):
        name = scan_type.upper()
    else:
        raise ArtifactValidationError("scan type is invalid")
    try:
        return _SCAN_TYPE_ALIASES[name]
    except KeyError as exc:
        raise ArtifactValidationError("scan type is invalid") from exc


def _validate_filename(filename: str) -> tuple[str, str]:
    if (
        not isinstance(filename, str)
        or not filename
        or len(filename) > 512
        or "\x00" in filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise ArtifactValidationError("filename must be a safe basename")
    extension = Path(filename).suffix.lower()
    if extension not in _MIME_BY_EXTENSION:
        raise ArtifactMimeError("artifact extension is not supported")
    return filename, extension


def _sniff_mime(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    head = os.read(fd, 4096)
    os.lseek(fd, 0, os.SEEK_SET)
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"PK\x03\x04"):
        duplicate = os.dup(fd)
        try:
            with (
                os.fdopen(duplicate, "rb") as stream,
                zipfile.ZipFile(stream) as archive,
            ):
                names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            return "application/octet-stream"
        if any(name.startswith("word/") for name in names):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if any(name.startswith("ppt/") for name in names):
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if any(name.startswith("xl/") for name in names):
            return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return "application/zip"
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    return "text/plain"


def _inspect_fd(fd: int, max_bytes: int) -> tuple[int, str, str]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _COPY_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise ArtifactTooLargeError("artifact exceeds configured maximum")
        digest.update(chunk)
    return size, digest.hexdigest(), _sniff_mime(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("artifact copy made no progress")
        view = view[written:]


class RemediationArtifactService:
    """Own the DB claim, immutable publication, and artifact state transitions."""

    @classmethod
    def from_settings(cls) -> "RemediationArtifactService":
        from src.config.settings import get_settings

        settings = get_settings()
        return cls(
            root=settings.remediation_artifact_dir,
            max_bytes=settings.remediation_artifact_max_bytes,
            retention_days=settings.remediation_artifact_retention_days,
            approved_retention_days=(
                settings.remediation_artifact_approved_retention_days
            ),
            written_retention_days=settings.remediation_artifact_written_retention_days,
            staging_grace_seconds=settings.remediation_artifact_staging_grace_seconds,
        )

    def __init__(
        self,
        *,
        root: str | Path,
        max_bytes: int,
        retention_days: int,
        staging_grace_seconds: int,
        approved_retention_days: int = 30,
        written_retention_days: int = 7,
    ) -> None:
        configured_root = Path(root)
        if not configured_root.is_absolute() or ".." in configured_root.parts:
            raise ArtifactValidationError("artifact root must be absolute")
        if (
            min(
                max_bytes,
                retention_days,
                staging_grace_seconds,
                approved_retention_days,
                written_retention_days,
            )
            < 1
        ):
            raise ArtifactValidationError("artifact service bounds must be positive")
        self.root = configured_root
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.approved_retention_days = approved_retention_days
        self.written_retention_days = written_retention_days
        self.staging_grace_seconds = staging_grace_seconds

    @contextmanager
    def _open_source_fd(
        self, source_path: str | Path, trusted_temp_root: str | Path
    ) -> Iterator[int]:
        root = Path(trusted_temp_root)
        source = Path(source_path)
        if not root.is_absolute() or not source.is_absolute() or ".." in source.parts:
            raise ArtifactValidationError(
                "trusted source paths must be canonical absolute paths"
            )
        try:
            root_state = root.lstat()
            relative = source.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ArtifactValidationError(
                "source artifact is outside trusted root"
            ) from exc
        if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
            raise ArtifactValidationError(
                "trusted temporary root must be a nonsymlink directory"
            )
        if not relative.parts or ".." in relative.parts:
            raise ArtifactValidationError("source artifact is outside trusted root")
        opened_fds: list[int] = []
        source_fd = -1
        try:
            try:
                directory_fd = os.open(root, _DIRECTORY_FLAGS)
                opened_fds.append(directory_fd)
                opened_root = os.fstat(directory_fd)
                if (opened_root.st_dev, opened_root.st_ino) != (
                    root_state.st_dev,
                    root_state.st_ino,
                ):
                    raise ArtifactValidationError(
                        "trusted temporary root changed while opening"
                    )
                for component in relative.parts[:-1]:
                    directory_fd = os.open(
                        component, _DIRECTORY_FLAGS, dir_fd=directory_fd
                    )
                    opened_fds.append(directory_fd)
                source_fd = os.open(
                    relative.parts[-1], _FILE_READ_FLAGS, dir_fd=directory_fd
                )
                opened = os.fstat(source_fd)
            except OSError as exc:
                raise ArtifactValidationError(
                    "source artifact is unavailable or unsafe"
                ) from exc
            if not stat.S_ISREG(opened.st_mode):
                raise ArtifactValidationError(
                    "source artifact must be a regular nonsymlink"
                )
            yield source_fd
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            for fd in reversed(opened_fds):
                os.close(fd)

    def _source_metadata(
        self, fd: int, *, filename: str, scan_type: ScanType | str, provider: str
    ) -> tuple[str, str, int, str]:
        if provider not in _PROVIDERS:
            raise ArtifactMimeError("provider is not supported for managed artifacts")
        filename, extension = _validate_filename(filename)
        expected_mime = _MIME_BY_SCAN_TYPE.get(_normalize_scan_type(scan_type), {}).get(
            extension
        )
        if expected_mime is None:
            raise ArtifactMimeError("extension is incompatible with scan type")
        size, checksum, mime_type = _inspect_fd(fd, self.max_bytes)
        if mime_type != expected_mime:
            raise ArtifactMimeError(
                "artifact bytes do not match scan type and extension"
            )
        return filename, mime_type, size, checksum

    @contextmanager
    def _borrow_source_stream_fd(self, source_stream: BinaryIO) -> Iterator[int]:
        """Yield a private descriptor duplicate without taking stream ownership."""
        try:
            source_fd = source_stream.fileno()
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                "source stream descriptor is unavailable"
            ) from exc
        if (
            not isinstance(source_fd, int)
            or isinstance(source_fd, bool)
            or source_fd < 0
        ):
            raise ArtifactValidationError("source stream descriptor is unavailable")

        duplicate = -1
        original_offset: int | None = None
        try:
            try:
                descriptor_flags = fcntl.fcntl(source_fd, fcntl.F_GETFD)
                duplicate = os.dup(source_fd)
                os.set_inheritable(duplicate, False)
                opened = os.fstat(duplicate)
                access_mode = fcntl.fcntl(duplicate, fcntl.F_GETFL) & os.O_ACCMODE
            except (OSError, ValueError) as exc:
                raise ArtifactValidationError(
                    "source stream descriptor is unavailable"
                ) from exc
            if not stat.S_ISREG(opened.st_mode):
                raise ArtifactValidationError(
                    "source stream descriptor must reference a regular file"
                )
            if access_mode != os.O_RDONLY:
                raise ArtifactValidationError(
                    "source stream descriptor must be read-only"
                )
            if not descriptor_flags & fcntl.FD_CLOEXEC:
                raise ArtifactValidationError(
                    "source stream descriptor must be close-on-exec"
                )
            if opened.st_size < 0:
                raise ArtifactValidationError(
                    "source stream descriptor has an invalid size"
                )
            if opened.st_size > self.max_bytes:
                raise ArtifactTooLargeError("artifact exceeds configured maximum")
            try:
                original_offset = os.lseek(source_fd, 0, os.SEEK_CUR)
                os.lseek(duplicate, 0, os.SEEK_SET)
            except OSError as exc:
                raise ArtifactValidationError(
                    "source stream descriptor is unavailable"
                ) from exc
            yield duplicate
        finally:
            restore_error: OSError | None = None
            if original_offset is not None:
                try:
                    os.lseek(source_fd, original_offset, os.SEEK_SET)
                except OSError as exc:
                    restore_error = exc
            if duplicate >= 0:
                os.close(duplicate)
            if restore_error is not None:
                raise ArtifactValidationError(
                    "source stream descriptor position could not be restored"
                ) from restore_error

    @staticmethod
    def _locked(db: Any, model: Any, identity: str, label: str):
        row = (
            db.query(model)
            .filter(model.id == identity)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if row is None:
            raise ArtifactAuthorizationError(
                f"{label} does not exist in current authority"
            )
        return row

    @staticmethod
    def _artifact_metadata(db: Any, artifact_id: str) -> _ArtifactMetadata:
        """Read immutable lock coordinates without acquiring any row lock."""
        row = (
            db.query(RemediationArtifact)
            .filter(RemediationArtifact.id == artifact_id)
            .one_or_none()
        )
        if row is None:
            raise ArtifactAuthorizationError(
                "artifact does not exist in current authority"
            )
        return _ArtifactMetadata(
            id=row.id,
            department_id=row.department_id,
            scan_id=row.scan_id,
            cloud_file_id=row.cloud_file_id,
            remediation_job_id=row.remediation_job_id,
            provider=row.provider,
        )

    @staticmethod
    def _validate_cleanup_fence(row: Any, expected_token: str | None) -> None:
        token = getattr(row, "artifact_cleanup_token", None)
        claimed_at = getattr(row, "artifact_cleanup_claimed_at", None)
        if (token is None) != (claimed_at is None):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        if token is not None and (
            expected_token is None
            or not isinstance(expected_token, str)
            or not hmac.compare_digest(token, expected_token)
        ):
            raise ArtifactAuthorizationError("artifact_cleanup_required")

    def _lock_authority_order(
        self,
        db: Any,
        *,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        remediation_job_id: str | None,
        provider: str,
        artifact_id: str | None = None,
        artifact_job_id: str | None = None,
        skip_locked_artifact: bool = False,
        expected_cleanup_token: str | None = None,
    ) -> tuple[Any, Any, Any, Any, RemediationArtifact | None]:
        """Lock Department→User→Credential→Scan→CloudFile→Job→Artifact."""
        # Discover lock coordinates without locking. Every coordinate is checked
        # again after the canonical parent locks have been acquired.
        scan_coordinates = (
            db.query(Scan).filter(Scan.id == scan_id).populate_existing().one_or_none()
        )
        if scan_coordinates is None:
            raise ArtifactAuthorizationError("scan does not exist in current authority")
        cloud_coordinates = None
        if cloud_file_id is not None:
            cloud_coordinates = (
                db.query(CloudFile)
                .filter(CloudFile.id == cloud_file_id)
                .populate_existing()
                .one_or_none()
            )
            if cloud_coordinates is None:
                raise ArtifactAuthorizationError(
                    "cloud file does not exist in current authority"
                )
        job_coordinates = None
        if remediation_job_id is not None:
            job_coordinates = (
                db.query(CloudJobQueue)
                .filter(CloudJobQueue.id == remediation_job_id)
                .populate_existing()
                .one_or_none()
            )
            if job_coordinates is None:
                raise ArtifactAuthorizationError(
                    "remediation job does not exist in current authority"
                )

        user_id = getattr(scan_coordinates, "user_id", None)
        credential_id = (
            getattr(cloud_coordinates, "credential_id", None)
            if cloud_coordinates is not None
            else getattr(job_coordinates, "credential_id", None)
        )
        department = self._locked(db, Department, department_id, "department")
        user = self._locked(db, User, user_id, "user") if user_id is not None else None
        credential = (
            self._locked(db, CloudOAuthCredentials, credential_id, "credential")
            if credential_id is not None
            else None
        )
        scan = self._locked(db, Scan, scan_id, "scan")
        cloud_file = (
            self._locked(db, CloudFile, cloud_file_id, "cloud file")
            if cloud_file_id is not None
            else None
        )
        job = (
            self._locked(db, CloudJobQueue, remediation_job_id, "remediation job")
            if remediation_job_id is not None
            else None
        )
        for parent in (department, user, credential, scan, cloud_file):
            if parent is not None:
                self._validate_cleanup_fence(parent, expected_cleanup_token)
        if (
            scan.department_id != department.id
            or getattr(scan, "user_id", None) != user_id
        ):
            raise ArtifactAuthorizationError(
                "scan and department authority do not match"
            )
        if user is not None and user.department_id != department.id:
            raise ArtifactAuthorizationError(
                "user and department authority do not match"
            )
        if cloud_file is None:
            if provider != "local" or remediation_job_id is not None:
                raise ArtifactAuthorizationError(
                    "jobless artifact without cloud authority must be local"
                )
        elif (
            provider == "local"
            or cloud_file.department_id != department.id
            or cloud_file.last_scan_id != scan.id
            or cloud_file.provider != provider
            or cloud_file.credential_id != credential_id
        ):
            raise ArtifactAuthorizationError(
                "cloud file does not match the exact scan authority"
            )
        if credential is not None and (
            credential.department_id != department.id
            or credential.id != credential_id
            or (cloud_file is not None and credential.provider != cloud_file.provider)
        ):
            raise ArtifactAuthorizationError(
                "credential does not match the exact artifact graph"
            )
        if job is not None:
            if (
                cloud_file is None
                or job.department_id != department.id
                or job.cloud_file_id != cloud_file.id
                or job.job_type != "remediate"
                or job.provider != provider
                or getattr(job, "credential_id", None) not in (None, credential_id)
            ):
                raise ArtifactAuthorizationError(
                    "remediation job does not match the exact artifact graph"
                )
            context = getattr(job, "execution_context", None) or {}
            context_scan_id = context.get("scan_id")
            if context_scan_id is not None and context_scan_id != scan.id:
                raise ArtifactAuthorizationError(
                    "remediation job scan context does not match"
                )
        artifact = None
        if artifact_id is not None or artifact_job_id is not None:
            query = db.query(RemediationArtifact)
            if artifact_id is not None:
                query = query.filter(RemediationArtifact.id == artifact_id)
            else:
                query = query.filter(
                    RemediationArtifact.remediation_job_id == artifact_job_id
                )
            artifact = (
                query.with_for_update(skip_locked=skip_locked_artifact)
                .populate_existing()
                .one_or_none()
            )
            if (
                artifact is None
                and artifact_id is not None
                and not skip_locked_artifact
            ):
                raise ArtifactAuthorizationError(
                    "artifact does not exist in current authority"
                )
        return department, scan, cloud_file, job, artifact

    def _lock_existing_artifact(
        self,
        db: Any,
        artifact_id: str,
        *,
        skip_locked: bool = False,
        expected_cleanup_token: str | None = None,
    ) -> tuple[Any, Any, Any, Any, RemediationArtifact | None]:
        artifact_id = _canonical_uuid(artifact_id, "artifact_id")
        metadata = self._artifact_metadata(db, artifact_id)
        locked = self._lock_authority_order(
            db,
            department_id=metadata.department_id,
            scan_id=metadata.scan_id,
            cloud_file_id=metadata.cloud_file_id,
            remediation_job_id=metadata.remediation_job_id,
            provider=metadata.provider,
            artifact_id=metadata.id,
            skip_locked_artifact=skip_locked,
            expected_cleanup_token=expected_cleanup_token,
        )
        artifact = locked[-1]
        if artifact is None:
            return locked
        actual = _ArtifactMetadata(
            artifact.id,
            artifact.department_id,
            artifact.scan_id,
            artifact.cloud_file_id,
            artifact.remediation_job_id,
            artifact.provider,
        )
        if actual != metadata:
            raise ArtifactAuthorizationError("artifact authority changed while locking")
        return locked

    def lock_current(
        self,
        db: Any,
        *,
        artifact_id: str,
        department_id: str,
        cloud_file_id: str,
        provider: str,
    ) -> tuple[Any, Any, Any, Any, RemediationArtifact | None]:
        """Lock and validate an artifact as the exact current cloud output."""
        locked = self._lock_existing_artifact(db, artifact_id)
        department, _, cloud_file, _, artifact = locked
        if (
            artifact is None
            or cloud_file is None
            or department.id != department_id
            or cloud_file.id != cloud_file_id
            or cloud_file.current_remediation_artifact_id != artifact.id
            or cloud_file.provider != provider
            or artifact.provider != provider
        ):
            raise ArtifactAuthorizationError("artifact is not the exact current output")
        return locked

    @staticmethod
    def _validate_locked_scan_type(
        prepared: PreparedRemediationArtifact, locked_scan_type: Any
    ) -> None:
        try:
            authoritative_type = _normalize_scan_type(locked_scan_type)
        except ArtifactValidationError as exc:
            raise ArtifactAuthorizationError("locked scan type is invalid") from exc
        if authoritative_type != prepared.scan_type:
            raise ArtifactAuthorizationError(
                "prepared artifact scan type does not match locked scan authority"
            )
        try:
            _, extension = _validate_filename(prepared.filename)
        except ArtifactValidationError as exc:
            raise ArtifactAuthorizationError(
                "prepared artifact is incompatible with locked scan authority"
            ) from exc
        authoritative_mime = _MIME_BY_SCAN_TYPE.get(authoritative_type, {}).get(
            extension
        )
        if (
            authoritative_mime is None
            or prepared.mime_type != authoritative_mime
            or PurePosixPath(prepared.storage_key).suffix.lower() != extension
        ):
            raise ArtifactAuthorizationError(
                "prepared artifact is incompatible with locked scan authority"
            )

    def _lock_and_validate_prepared(
        self, db: Any, prepared: PreparedRemediationArtifact
    ) -> None:
        _, scan, _, _, _ = self._lock_authority_order(
            db,
            department_id=prepared.department_id,
            scan_id=prepared.scan_id,
            cloud_file_id=prepared.cloud_file_id,
            remediation_job_id=prepared.remediation_job_id,
            provider=prepared.provider,
        )
        self._validate_locked_scan_type(prepared, scan.scan_type)

    @staticmethod
    def _validate_artifact_scan_type(
        artifact: RemediationArtifact, locked_scan_type: Any
    ) -> None:
        try:
            authoritative = _normalize_scan_type(locked_scan_type)
        except ArtifactValidationError as exc:
            raise ArtifactAuthorizationError("locked scan type is invalid") from exc
        if artifact.scan_type != authoritative:
            raise ArtifactAuthorizationError("artifact scan type authority mismatch")
        try:
            _, extension = _validate_filename(artifact.filename)
        except ArtifactValidationError as exc:
            raise ArtifactAuthorizationError(
                "artifact scan type authority mismatch"
            ) from exc
        if (
            _MIME_BY_SCAN_TYPE.get(authoritative, {}).get(extension)
            != artifact.mime_type
            or PurePosixPath(artifact.storage_key).suffix.lower() != extension
        ):
            raise ArtifactAuthorizationError("artifact scan type authority mismatch")

    def _prepare(
        self,
        fd: int,
        *,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        remediation_job_id: str | None,
        created_by_id: str | None,
        provider: str,
        scan_type: ScanType | str,
        filename: str,
        provider_result: dict[str, Any] | None,
    ) -> PreparedRemediationArtifact:
        department_id = _canonical_uuid(department_id, "department_id")
        scan_id = _canonical_uuid(scan_id, "scan_id")
        if cloud_file_id is not None:
            cloud_file_id = _canonical_uuid(cloud_file_id, "cloud_file_id")
        if remediation_job_id is not None:
            remediation_job_id = _canonical_uuid(
                remediation_job_id, "remediation_job_id"
            )
        if created_by_id is not None:
            created_by_id = _canonical_uuid(created_by_id, "created_by_id")
        scan_type = _normalize_scan_type(scan_type)
        filename, mime_type, size, checksum = self._source_metadata(
            fd, filename=filename, scan_type=scan_type, provider=provider
        )
        artifact_id = str(uuid.uuid4())
        extension = Path(filename).suffix.lower()
        storage_key = PurePosixPath(
            department_id, scan_id, artifact_id, f"{uuid.uuid4()}{extension}"
        ).as_posix()
        now = datetime.now(timezone.utc)
        return PreparedRemediationArtifact(
            id=artifact_id,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
            remediation_job_id=remediation_job_id,
            created_by_id=created_by_id,
            provider=provider,
            scan_type=scan_type,
            publication_token=secrets.token_hex(32),
            publication_heartbeat_at=now,
            published_at=None,
            storage_backend="local",
            storage_key=storage_key,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size,
            sha256=checksum,
            lifecycle_status="staging",
            review_status="pending",
            approval_checksum=None,
            approved_by_id=None,
            approved_by_ref=None,
            approved_at=None,
            rejected_by_id=None,
            rejected_by_ref=None,
            rejected_at=None,
            written_back_at=None,
            cleanup_claimed_at=None,
            deleted_at=None,
            provider_result=provider_result,
            expires_at=now + timedelta(days=self.retention_days),
            created_at=now,
            updated_at=now,
        )

    def claim(self, db: Any, prepared: PreparedRemediationArtifact) -> ArtifactClaim:
        """Commit a staging row before any destination byte can be published."""
        _, scan, _, _, existing = self._lock_authority_order(
            db,
            department_id=prepared.department_id,
            scan_id=prepared.scan_id,
            cloud_file_id=prepared.cloud_file_id,
            remediation_job_id=prepared.remediation_job_id,
            provider=prepared.provider,
            artifact_job_id=prepared.remediation_job_id,
        )
        self._validate_locked_scan_type(prepared, scan.scan_type)
        if existing is not None:
            self._validate_artifact_scan_type(existing, scan.scan_type)
            result = self._existing_claim(existing, prepared)
            db.commit()
            return result
        artifact = RemediationArtifact(**prepared.as_model_kwargs())
        db.add(artifact)
        try:
            db.flush()
            db.commit()
        except IntegrityError:
            db.rollback()
            _, scan, _, _, existing = self._lock_authority_order(
                db,
                department_id=prepared.department_id,
                scan_id=prepared.scan_id,
                cloud_file_id=prepared.cloud_file_id,
                remediation_job_id=prepared.remediation_job_id,
                provider=prepared.provider,
                artifact_job_id=prepared.remediation_job_id,
            )
            self._validate_locked_scan_type(prepared, scan.scan_type)
            if existing is None:
                raise
            self._validate_artifact_scan_type(existing, scan.scan_type)
            result = self._existing_claim(existing, prepared)
            db.commit()
            return result
        return ArtifactClaim(
            artifact=artifact,
            owned=True,
            status="staging",
            publication_token=prepared.publication_token,
        )

    @staticmethod
    def _existing_claim(
        artifact: RemediationArtifact, prepared: PreparedRemediationArtifact
    ) -> ArtifactClaim:
        expected = (
            prepared.department_id,
            prepared.scan_id,
            prepared.cloud_file_id,
            prepared.remediation_job_id,
            prepared.provider,
            prepared.scan_type,
        )
        actual = (
            artifact.department_id,
            artifact.scan_id,
            artifact.cloud_file_id,
            artifact.remediation_job_id,
            artifact.provider,
            artifact.scan_type,
        )
        if actual != expected:
            raise ArtifactAuthorizationError(
                "existing job claim has incoherent authority"
            )
        if artifact.cleanup_claimed_at is not None:
            raise ArtifactInProgressError("existing artifact is claimed for cleanup")
        if artifact.lifecycle_status == "staging":
            if artifact.published_at is not None and artifact.publication_token:
                return ArtifactClaim(
                    artifact=artifact,
                    owned=True,
                    status="published",
                    publication_token=artifact.publication_token,
                )
            return ArtifactClaim(artifact=artifact, owned=False, status="in_progress")
        if artifact.lifecycle_status == "available":
            return ArtifactClaim(artifact=artifact, owned=False, status="available")
        raise ArtifactAuthorizationError("existing job claim is not reusable")

    def _claim_and_publish_fd(
        self,
        db: Any,
        *,
        source_fd: int,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        remediation_job_id: str | None,
        created_by_id: str | None,
        provider: str,
        scan_type: ScanType | str,
        filename: str,
        provider_result: dict[str, Any] | None = None,
        commit: bool = True,
        claimed_metadata: tuple[int, str, str, str] | None = None,
    ) -> ArtifactPublicationResult:
        """Validate, DB-first publish, and optionally defer the final commit."""
        claim: ArtifactClaim | None = None
        prepared = self._prepare(
            source_fd,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
            remediation_job_id=remediation_job_id,
            created_by_id=created_by_id,
            provider=provider,
            scan_type=scan_type,
            filename=filename,
            provider_result=provider_result,
        )
        if claimed_metadata is not None and claimed_metadata != (
            prepared.size_bytes,
            prepared.sha256,
            prepared.mime_type,
            prepared.filename,
        ):
            raise ArtifactIntegrityError(
                "source stream does not match output claim metadata"
            )
        claim = self.claim(db, prepared)
        try:
            if claim.status == "in_progress":
                raise ArtifactInProgressError(
                    "artifact publication is already in progress"
                )
            if claim.status == "available":
                with self.open_verified(
                    db,
                    claim.artifact,
                    department_id=department_id,
                    scan_id=scan_id,
                    cloud_file_id=cloud_file_id,
                ):
                    pass
                return ArtifactPublicationResult(
                    artifact=claim.artifact,
                    artifact_id=str(claim.artifact.id),
                )
            assert claim.publication_token is not None
            if claim.status != "published":
                self._publish_fd(db, claim.artifact, claim.publication_token, source_fd)
            artifact = self.finalize(
                db,
                artifact_id=claim.artifact.id,
                publication_token=claim.publication_token,
            )
            if commit:
                db.commit()
            return ArtifactPublicationResult(
                artifact=artifact,
                artifact_id=str(artifact.id),
                publication_token=claim.publication_token,
            )
        except (OSError, ArtifactIntegrityError, SQLAlchemyError) as exc:
            db.rollback()
            cleanup_complete = False
            if claim.owned and claim.publication_token is not None:
                try:
                    self.abort_staging(
                        db,
                        artifact_id=str(claim.artifact.id),
                        publication_token=claim.publication_token,
                    )
                    cleanup_complete = True
                except Exception:
                    db.rollback()
            raise ArtifactPublicationRetryable(
                ArtifactPublicationRetry(
                    artifact_id=str(claim.artifact.id),
                    publication_token=claim.publication_token,
                    cleanup_complete=cleanup_complete,
                )
            ) from exc

    def claim_and_publish(
        self,
        db: Any,
        *,
        source_path: str | Path,
        trusted_temp_root: str | Path,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        remediation_job_id: str | None,
        created_by_id: str | None,
        provider: str,
        scan_type: ScanType | str,
        filename: str,
        provider_result: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> ArtifactPublicationResult:
        """Publish a trusted path through the common descriptor implementation."""
        with self._open_source_fd(source_path, trusted_temp_root) as source_fd:
            return self._claim_and_publish_fd(
                db,
                source_fd=source_fd,
                department_id=department_id,
                scan_id=scan_id,
                cloud_file_id=cloud_file_id,
                remediation_job_id=remediation_job_id,
                created_by_id=created_by_id,
                provider=provider,
                scan_type=scan_type,
                filename=filename,
                provider_result=provider_result,
                commit=commit,
            )

    def claim_and_publish_stream(
        self,
        db: Any,
        *,
        source_stream: BinaryIO,
        claimed_size_bytes: int,
        claimed_sha256: str,
        claimed_mime_type: str,
        claimed_filename: str,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        remediation_job_id: str | None,
        created_by_id: str | None,
        provider: str,
        scan_type: ScanType | str,
        filename: str,
        provider_result: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> ArtifactPublicationResult:
        """Publish an exact caller-owned output claim without closing its stream."""
        with self._borrow_source_stream_fd(source_stream) as source_fd:
            return self._claim_and_publish_fd(
                db,
                source_fd=source_fd,
                department_id=department_id,
                scan_id=scan_id,
                cloud_file_id=cloud_file_id,
                remediation_job_id=remediation_job_id,
                created_by_id=created_by_id,
                provider=provider,
                scan_type=scan_type,
                filename=filename,
                provider_result=provider_result,
                commit=commit,
                claimed_metadata=(
                    claimed_size_bytes,
                    claimed_sha256,
                    claimed_mime_type,
                    claimed_filename,
                ),
            )

    def persist(self, **_: Any) -> PreparedRemediationArtifact:
        """Bytes-first persistence is intentionally disabled; use claim_and_publish."""
        raise ArtifactValidationError(
            "bytes-first persist is disabled; use claim_and_publish"
        )

    def create_row(self, *_: Any, **__: Any):
        raise ArtifactValidationError(
            "detached row creation is disabled; use claim_and_publish"
        )

    @contextmanager
    def _storage_directory(
        self, components: tuple[str, ...], *, create: bool
    ) -> Iterator[int]:
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            root_state = self.root.lstat()
        except OSError as exc:
            raise ArtifactIntegrityError("artifact root is unavailable") from exc
        if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
            raise ArtifactIntegrityError("artifact root must be a nonsymlink directory")
        current_fd = os.open(self.root, _DIRECTORY_FLAGS)
        opened = [current_fd]
        try:
            opened_root = os.fstat(current_fd)
            if (opened_root.st_dev, opened_root.st_ino) != (
                root_state.st_dev,
                root_state.st_ino,
            ):
                raise ArtifactIntegrityError("artifact root changed while opening")
            for component in components:
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
                os.fchmod(next_fd, 0o700)
                opened.append(next_fd)
                current_fd = next_fd
            yield current_fd
        finally:
            for fd in reversed(opened):
                os.close(fd)

    @staticmethod
    def _require_publication_owner(
        artifact: RemediationArtifact, publication_token: str
    ) -> None:
        if (
            artifact.lifecycle_status != "staging"
            or artifact.cleanup_claimed_at is not None
            or artifact.publication_heartbeat_at is None
            or not artifact.publication_token
            or not hmac.compare_digest(artifact.publication_token, publication_token)
        ):
            raise ArtifactAuthorizationError("artifact publication lease is not owned")

    def _heartbeat_publication(
        self, db: Any, artifact_id: str, publication_token: str
    ) -> RemediationArtifact:
        _, scan, _, _, artifact = self._lock_existing_artifact(db, artifact_id)
        assert artifact is not None
        self._validate_artifact_scan_type(artifact, scan.scan_type)
        self._require_publication_owner(artifact, publication_token)
        artifact.publication_heartbeat_at = datetime.now(timezone.utc)
        db.flush()
        db.commit()
        return artifact

    def _publish_fd(
        self,
        db: Any,
        artifact: RemediationArtifact,
        publication_token: str,
        source_fd: int,
    ) -> None:
        parts = self._storage_parts(artifact)
        final_name = parts[-1]
        partial_name = f"{final_name}.partial"
        partial_fd = -1
        partial_created = False
        linked = False
        with self._storage_directory(parts[:-1], create=True) as directory_fd:
            try:
                partial_fd = os.open(
                    partial_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                partial_created = True
                digest = hashlib.sha256()
                size = 0
                heartbeat_interval = max(1, min(30, self.staging_grace_seconds // 3))
                last_heartbeat = datetime.now(timezone.utc)
                os.lseek(source_fd, 0, os.SEEK_SET)
                while chunk := os.read(source_fd, _COPY_CHUNK_BYTES):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ArtifactTooLargeError(
                            "artifact exceeds configured maximum"
                        )
                    _write_all(partial_fd, chunk)
                    digest.update(chunk)
                    heartbeat_now = datetime.now(timezone.utc)
                    if (
                        heartbeat_now - last_heartbeat
                    ).total_seconds() >= heartbeat_interval:
                        artifact = self._heartbeat_publication(
                            db, artifact.id, publication_token
                        )
                        last_heartbeat = heartbeat_now
                if size != artifact.size_bytes or not hmac.compare_digest(
                    digest.hexdigest(), artifact.sha256
                ):
                    raise ArtifactIntegrityError(
                        "source changed after the database claim"
                    )
                os.fchmod(partial_fd, 0o600)
                os.fsync(partial_fd)
                if _sniff_mime(partial_fd) != artifact.mime_type:
                    raise ArtifactIntegrityError(
                        "source MIME changed after the database claim"
                    )
                _, scan, _, _, artifact = self._lock_existing_artifact(db, artifact.id)
                assert artifact is not None
                self._validate_artifact_scan_type(artifact, scan.scan_type)
                self._require_publication_owner(artifact, publication_token)
                os.link(
                    partial_name,
                    final_name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                linked = True
                os.unlink(partial_name, dir_fd=directory_fd)
                os.fsync(directory_fd)
                published_at = datetime.now(timezone.utc)
                artifact.published_at = published_at
                artifact.publication_heartbeat_at = published_at
                db.flush()
                db.commit()
            except FileExistsError as exc:
                raise ArtifactIntegrityError(
                    "artifact destination already exists"
                ) from exc
            finally:
                if partial_fd >= 0:
                    os.close(partial_fd)
                if partial_created:
                    try:
                        os.unlink(partial_name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
                if linked:
                    os.fsync(directory_fd)

    def publish_claimed(
        self,
        db: Any,
        artifact: RemediationArtifact,
        *,
        publication_token: str,
        source_path: str | Path,
        trusted_temp_root: str | Path,
    ) -> None:
        self._require_publication_owner(artifact, publication_token)
        with self._open_source_fd(source_path, trusted_temp_root) as source_fd:
            size, checksum, mime_type = _inspect_fd(source_fd, self.max_bytes)
            if (
                size != artifact.size_bytes
                or checksum != artifact.sha256
                or mime_type != artifact.mime_type
            ):
                raise ArtifactIntegrityError("source does not match the staging claim")
            self._publish_fd(db, artifact, publication_token, source_fd)

    def finalize(
        self,
        db: Any,
        *,
        artifact_id: str,
        publication_token: str,
        commit: bool = False,
    ) -> RemediationArtifact:
        if not isinstance(publication_token, str) or len(publication_token) != 64:
            raise ArtifactAuthorizationError("publication token is invalid")
        _, scan, cloud_file, _, artifact = self._lock_existing_artifact(db, artifact_id)
        assert artifact is not None
        self._validate_artifact_scan_type(artifact, scan.scan_type)
        if not artifact.publication_token or not hmac.compare_digest(
            artifact.publication_token, publication_token
        ):
            raise ArtifactAuthorizationError("publication token does not match")
        if artifact.cleanup_claimed_at is not None:
            raise ArtifactAuthorizationError("artifact has a cleanup claim")
        if artifact.lifecycle_status != "staging":
            raise ArtifactAuthorizationError("artifact is not staging")
        if _utc(artifact.expires_at) <= datetime.now(timezone.utc):
            raise ArtifactExpiredError("staging artifact has expired")
        if artifact.published_at is None:
            raise ArtifactAuthorizationError("artifact is not published")
        with self._open_verified(artifact, allowed_lifecycle={"staging"}):
            pass
        artifact.lifecycle_status = "available"
        artifact.publication_token = None
        artifact.publication_heartbeat_at = None
        if cloud_file is not None:
            cloud_file.current_remediation_artifact_id = artifact.id
            cloud_file.has_remediated_version = True
            cloud_file.remediation_origin = "manual"
        else:
            previous_id = scan.current_remediation_artifact_id
            if previous_id and previous_id != artifact.id:
                previous = (
                    db.query(RemediationArtifact)
                    .filter(RemediationArtifact.id == previous_id)
                    .with_for_update()
                    .populate_existing()
                    .one_or_none()
                )
                if (
                    previous is not None
                    and previous.scan_id == scan.id
                    and previous.provider == "local"
                    and previous.lifecycle_status == "available"
                ):
                    previous.lifecycle_status = "superseded"
            scan.current_remediation_artifact_id = artifact.id
        db.flush()
        if commit:
            db.commit()
        return artifact

    def abort_staging(
        self, db: Any, *, artifact_id: str, publication_token: str
    ) -> bool:
        """Remove a known failed publication so the same job can retry cleanly."""
        _, _, cloud_file, _, artifact = self._lock_existing_artifact(db, artifact_id)
        assert artifact is not None
        self._require_publication_owner(artifact, publication_token)
        return self._abort_locked_staging(db, cloud_file, artifact)

    def abort_staging_for_job(
        self, db: Any, *, artifact_id: str, remediation_job_id: str
    ) -> bool:
        """Clean a retained retry claim using its locked internal token."""
        try:
            metadata = self._artifact_metadata(db, artifact_id)
        except ArtifactAuthorizationError:
            return False
        if metadata.remediation_job_id != remediation_job_id:
            raise ArtifactAuthorizationError(
                "artifact cleanup does not match remediation job"
            )
        _, _, cloud_file, job, artifact = self._lock_existing_artifact(db, artifact_id)
        assert artifact is not None
        if job is None or str(job.id) != str(remediation_job_id):
            raise ArtifactAuthorizationError(
                "artifact cleanup does not match remediation job"
            )
        token = artifact.publication_token
        if not isinstance(token, str):
            raise ArtifactAuthorizationError("artifact publication token is missing")
        self._require_publication_owner(artifact, token)
        return self._abort_locked_staging(db, cloud_file, artifact)

    def _abort_locked_staging(
        self, db: Any, cloud_file: Any, artifact: RemediationArtifact
    ) -> bool:
        removed = self.delete_known(artifact)
        if (
            cloud_file is not None
            and cloud_file.current_remediation_artifact_id == artifact.id
        ):
            cloud_file.current_remediation_artifact_id = None
            cloud_file.has_remediated_version = False
            cloud_file.remediation_origin = None
        db.delete(artifact)
        db.commit()
        return removed

    def _validate_record_state(
        self,
        artifact: RemediationArtifact | PreparedRemediationArtifact,
        *,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        require_approved: bool,
        approval_checksum: str | None,
        allowed_lifecycle: set[str],
    ) -> None:
        requested = (
            _canonical_uuid(department_id, "department_id"),
            _canonical_uuid(scan_id, "scan_id"),
            (
                _canonical_uuid(cloud_file_id, "cloud_file_id")
                if cloud_file_id is not None
                else None
            ),
        )
        actual = (artifact.department_id, artifact.scan_id, artifact.cloud_file_id)
        authority_matches = all(
            (left is None and right is None)
            or (
                isinstance(left, str)
                and isinstance(right, str)
                and hmac.compare_digest(left, right)
            )
            for left, right in zip(actual, requested)
        )
        if not authority_matches:
            raise ArtifactAuthorizationError(
                "artifact authority does not match request"
            )
        if artifact.cleanup_claimed_at is not None:
            raise ArtifactAuthorizationError("artifact has a cleanup claim")
        if artifact.lifecycle_status not in allowed_lifecycle:
            raise ArtifactAuthorizationError("artifact is not available")
        if _utc(artifact.expires_at) <= datetime.now(timezone.utc):
            raise ArtifactExpiredError("artifact has expired")
        if require_approved and (
            artifact.review_status != "approved"
            or not artifact.approval_checksum
            or not hmac.compare_digest(artifact.approval_checksum, artifact.sha256)
            or (
                approval_checksum is not None
                and not hmac.compare_digest(
                    artifact.approval_checksum, approval_checksum
                )
            )
        ):
            raise ArtifactAuthorizationError("artifact approval checksum is not valid")

    @contextmanager
    def _open_verified(
        self,
        artifact: RemediationArtifact | PreparedRemediationArtifact,
        *,
        allowed_lifecycle: set[str],
    ) -> Iterator[BinaryIO]:
        parts = self._storage_parts(artifact)
        with self._storage_directory(parts[:-1], create=False) as directory_fd:
            try:
                fd = os.open(parts[-1], _FILE_READ_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                raise ArtifactIntegrityError(
                    "artifact bytes are missing or unsafe"
                ) from exc
            stream: BinaryIO | None = None
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode):
                    raise ArtifactIntegrityError("artifact is not a regular file")
                try:
                    size, checksum, mime_type = _inspect_fd(fd, self.max_bytes)
                except ArtifactTooLargeError as exc:
                    raise ArtifactIntegrityError(
                        "stored artifact exceeds configured maximum"
                    ) from exc
                expected_mime = _MIME_BY_EXTENSION.get(
                    Path(artifact.filename).suffix.lower()
                )
                if (
                    size != artifact.size_bytes
                    or not hmac.compare_digest(checksum, artifact.sha256)
                    or mime_type != artifact.mime_type
                    or expected_mime != artifact.mime_type
                    or artifact.provider not in _PROVIDERS
                    or artifact.lifecycle_status not in allowed_lifecycle
                ):
                    raise ArtifactIntegrityError(
                        "artifact metadata and bytes do not match"
                    )
                os.lseek(fd, 0, os.SEEK_SET)
                stream = os.fdopen(fd, "rb")
                fd = -1
                yield stream
            finally:
                if stream is not None:
                    stream.close()
                if fd >= 0:
                    os.close(fd)

    @contextmanager
    def open_verified(
        self,
        db: Any,
        artifact: RemediationArtifact,
        *,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        require_approved: bool = False,
        approval_checksum: str | None = None,
    ) -> Iterator[BinaryIO]:
        """Lock current authority and yield a descriptor-bound verified stream."""
        _, locked_scan, locked_cloud, _, locked_artifact = self._lock_existing_artifact(
            db, artifact.id
        )
        assert locked_artifact is not None
        artifact = locked_artifact
        self._validate_artifact_scan_type(artifact, locked_scan.scan_type)
        if (
            locked_cloud is None
            and locked_scan.current_remediation_artifact_id != artifact.id
        ):
            raise ArtifactAuthorizationError("artifact is not the exact current output")
        self._validate_record_state(
            artifact,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
            require_approved=require_approved,
            approval_checksum=approval_checksum,
            allowed_lifecycle={"available"},
        )
        with self._open_verified(artifact, allowed_lifecycle={"available"}) as stream:
            yield stream

    def resolve_record(
        self,
        db: Any,
        artifact: RemediationArtifact,
        *,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
        require_approved: bool = False,
        approval_checksum: str | None = None,
    ) -> RemediationArtifact:
        """Validate and return metadata only; never return a trusted filesystem path."""
        with self.open_verified(
            db,
            artifact,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
            require_approved=require_approved,
            approval_checksum=approval_checksum,
        ):
            pass
        return artifact

    def reuse_available(
        self,
        db: Any,
        *,
        remediation_job_id: str,
        department_id: str,
        scan_id: str,
        cloud_file_id: str | None,
    ):
        remediation_job_id = _canonical_uuid(remediation_job_id, "remediation_job_id")
        discovered = (
            db.query(RemediationArtifact)
            .filter(RemediationArtifact.remediation_job_id == remediation_job_id)
            .one_or_none()
        )
        if discovered is None:
            return None
        _, _, _, _, artifact = self._lock_existing_artifact(db, discovered.id)
        assert artifact is not None
        self.resolve_record(
            db,
            artifact,
            department_id=department_id,
            scan_id=scan_id,
            cloud_file_id=cloud_file_id,
        )
        return artifact

    def _lock_mutable(self, db: Any, artifact_id: str) -> RemediationArtifact:
        _, scan, cloud_file, _, artifact = self._lock_existing_artifact(db, artifact_id)
        assert artifact is not None
        if artifact.cleanup_claimed_at is not None:
            raise ArtifactAuthorizationError("artifact has a cleanup claim")
        self._validate_artifact_scan_type(artifact, scan.scan_type)
        if (
            cloud_file is not None
            and cloud_file.current_remediation_artifact_id != artifact.id
        ):
            raise ArtifactAuthorizationError("artifact is not the exact current output")
        if cloud_file is None and scan.current_remediation_artifact_id != artifact.id:
            raise ArtifactAuthorizationError("artifact is not the exact current output")
        return artifact

    @staticmethod
    def _review_actor_ref(actor_ref: str) -> str:
        if (
            not isinstance(actor_ref, str)
            or not actor_ref.strip()
            or len(actor_ref) > 255
        ):
            raise ArtifactValidationError("review actor reference is invalid")
        return actor_ref.strip()

    def _require_approvable_review(
        self, db: Any, artifact: RemediationArtifact
    ) -> None:
        scan = (
            db.query(Scan)
            .filter(Scan.id == artifact.scan_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if (
            scan is None
            or scan.status != ScanStatus.COMPLETED
            or scan.remediation_outcome != RemediationOutcome.COMPLETED.value
        ):
            raise ArtifactAuthorizationError("artifact verification has not passed")
        fixes = (
            db.query(ScanFix)
            .filter(ScanFix.scan_id == artifact.scan_id)
            .with_for_update()
            .populate_existing()
            .all()
        )
        terminal = {"auto_approved", "approved", "rejected"}
        accepted = {"auto_approved", "approved"}
        if not fixes or any(fix.review_status not in terminal for fix in fixes):
            raise ArtifactAuthorizationError("scan fixes are not all terminal")
        if not any(fix.review_status in accepted for fix in fixes):
            raise ArtifactAuthorizationError("artifact has no accepted fix")

    def approve(
        self,
        db: Any,
        *,
        artifact_id: str,
        approved_by_ref: str,
        approved_by_id: str | None = None,
        now: datetime | None = None,
    ) -> RemediationArtifact:
        approved_by_ref = self._review_actor_ref(approved_by_ref)
        artifact = self._lock_mutable(db, artifact_id)
        effective_now = _utc(now or datetime.now(timezone.utc))
        if _utc(artifact.expires_at) <= effective_now:
            raise ArtifactExpiredError("artifact has expired")
        if artifact.review_status == "approved":
            if (
                artifact.lifecycle_status == "available"
                and artifact.approved_at is not None
                and artifact.approved_by_id == approved_by_id
                and artifact.approved_by_ref == approved_by_ref
                and artifact.approval_checksum is not None
                and hmac.compare_digest(artifact.approval_checksum, artifact.sha256)
                and artifact.rejected_by_id is None
                and artifact.rejected_by_ref is None
                and artifact.rejected_at is None
            ):
                return artifact
            raise ArtifactAuthorizationError(
                "approval retry conflicts with durable state"
            )
        if (
            artifact.lifecycle_status != "available"
            or artifact.review_status != "pending"
            or artifact.written_back_at is not None
            or artifact.approved_at is not None
            or artifact.approval_checksum is not None
            or artifact.approved_by_id is not None
            or artifact.approved_by_ref is not None
            or artifact.rejected_by_id is not None
            or artifact.rejected_by_ref is not None
            or artifact.rejected_at is not None
        ):
            raise ArtifactAuthorizationError("artifact is not pending approval")
        self._require_approvable_review(db, artifact)
        with self.open_verified(
            db,
            artifact,
            department_id=artifact.department_id,
            scan_id=artifact.scan_id,
            cloud_file_id=artifact.cloud_file_id,
        ):
            pass
        artifact.review_status = "approved"
        artifact.approval_checksum = artifact.sha256
        artifact.approved_by_id = approved_by_id
        artifact.approved_by_ref = approved_by_ref
        artifact.approved_at = effective_now
        artifact.expires_at = effective_now + timedelta(
            days=self.approved_retention_days
        )
        if artifact.cloud_file_id is not None:
            cloud_file = (
                db.query(CloudFile)
                .filter(CloudFile.id == artifact.cloud_file_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if (
                cloud_file is None
                or cloud_file.current_remediation_artifact_id != artifact.id
            ):
                raise ArtifactAuthorizationError(
                    "artifact is not the exact current output"
                )
            cloud_file.writeback_status = "approved"
            cloud_file.has_remediated_version = True
            cloud_file.remediation_origin = "manual"
        db.add(
            ReviewAuditLog(
                scan_id=artifact.scan_id,
                user_id=approved_by_id,
                action="artifact_approved",
                details={"artifact_id": artifact.id, "sha256": artifact.sha256},
            )
        )
        db.flush()
        return artifact

    def reject(
        self,
        db: Any,
        *,
        artifact_id: str,
        rejected_by_ref: str,
        rejected_by_id: str | None = None,
        now: datetime | None = None,
    ) -> RemediationArtifact:
        rejected_by_ref = self._review_actor_ref(rejected_by_ref)
        artifact = self._lock_mutable(db, artifact_id)
        effective_now = _utc(now or datetime.now(timezone.utc))
        if _utc(artifact.expires_at) <= effective_now:
            raise ArtifactExpiredError("artifact has expired")
        if artifact.review_status == "rejected":
            if (
                artifact.lifecycle_status == "available"
                and artifact.written_back_at is None
                and artifact.rejected_at is not None
                and artifact.rejected_by_id == rejected_by_id
                and artifact.rejected_by_ref == rejected_by_ref
                and artifact.approval_checksum is None
                and artifact.approved_by_id is None
                and artifact.approved_by_ref is None
                and artifact.approved_at is None
            ):
                return artifact
            raise ArtifactAuthorizationError(
                "rejection retry conflicts with durable state"
            )
        if (
            artifact.lifecycle_status != "available"
            or artifact.review_status != "pending"
            or artifact.written_back_at is not None
            or artifact.approval_checksum is not None
            or artifact.approved_by_id is not None
            or artifact.approved_by_ref is not None
            or artifact.approved_at is not None
            or artifact.rejected_by_id is not None
            or artifact.rejected_by_ref is not None
            or artifact.rejected_at is not None
        ):
            raise ArtifactAuthorizationError("artifact cannot be rejected")
        with self.open_verified(
            db,
            artifact,
            department_id=artifact.department_id,
            scan_id=artifact.scan_id,
            cloud_file_id=artifact.cloud_file_id,
        ):
            pass
        artifact.review_status = "rejected"
        artifact.approval_checksum = None
        artifact.approved_by_id = None
        artifact.approved_by_ref = None
        artifact.approved_at = None
        artifact.rejected_by_id = rejected_by_id
        artifact.rejected_by_ref = rejected_by_ref
        artifact.rejected_at = effective_now
        if artifact.cloud_file_id is not None:
            cloud_file = (
                db.query(CloudFile)
                .filter(CloudFile.id == artifact.cloud_file_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if cloud_file is None:
                raise ArtifactAuthorizationError("artifact cloud authority is missing")
            cloud_file.writeback_status = "rejected"
            cloud_file.has_remediated_version = False
            cloud_file.remediation_origin = None
        db.add(
            ReviewAuditLog(
                scan_id=artifact.scan_id,
                user_id=rejected_by_id,
                action="artifact_rejected",
                details={"artifact_id": artifact.id, "sha256": artifact.sha256},
            )
        )
        db.flush()
        return artifact

    def mark_written(
        self,
        db: Any,
        *,
        artifact_id: str,
        provider_result: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> RemediationArtifact:
        artifact = self._lock_mutable(db, artifact_id)
        sanitized_result = _sanitize_provider_result(provider_result)
        written_at = _utc(now or datetime.now(timezone.utc))
        if _utc(artifact.expires_at) <= written_at:
            raise ArtifactExpiredError("artifact has expired")
        if artifact.written_back_at is not None:
            if (
                artifact.lifecycle_status == "available"
                and artifact.review_status == "approved"
                and artifact.approval_checksum is not None
                and hmac.compare_digest(artifact.approval_checksum, artifact.sha256)
                and artifact.provider_result == sanitized_result
            ):
                return artifact
            raise ArtifactAuthorizationError(
                "writeback retry conflicts with durable state"
            )
        if (
            artifact.lifecycle_status != "available"
            or artifact.review_status != "approved"
            or not artifact.approval_checksum
            or not hmac.compare_digest(artifact.approval_checksum, artifact.sha256)
            or artifact.approved_at is None
            or not artifact.approved_by_ref
            or artifact.rejected_by_id is not None
            or artifact.rejected_by_ref is not None
            or artifact.rejected_at is not None
        ):
            raise ArtifactAuthorizationError("artifact is not approved for writeback")
        artifact.written_back_at = written_at
        artifact.provider_result = sanitized_result
        artifact.expires_at = written_at + timedelta(days=self.written_retention_days)
        db.flush()
        return artifact

    @staticmethod
    def _force_terminal_rejection(
        artifact: RemediationArtifact, *, actor_ref: str, now: datetime
    ) -> None:
        if artifact.review_status == "approved" and artifact.written_back_at is None:
            artifact.review_status = "rejected"
            artifact.approval_checksum = None
            artifact.approved_by_id = None
            artifact.approved_by_ref = None
            artifact.approved_at = None
            artifact.rejected_by_id = None
            artifact.rejected_by_ref = actor_ref
            artifact.rejected_at = now

    @staticmethod
    def _fence_cleanup_parents(db: Any, parents: list[Any]) -> str:
        """Set or resume one cryptographic fence while parent locks are held."""
        if not parents:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        existing = {
            getattr(parent, "artifact_cleanup_token", None)
            for parent in parents
            if getattr(parent, "artifact_cleanup_token", None) is not None
        }
        if len(existing) > 1 or (
            existing
            and any(
                getattr(parent, "artifact_cleanup_token", None) is None
                for parent in parents
            )
        ):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        token = next(iter(existing), None) or secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        for parent in parents:
            claimed_at = getattr(parent, "artifact_cleanup_claimed_at", None)
            current = getattr(parent, "artifact_cleanup_token", None)
            if (current is None) != (claimed_at is None):
                raise ArtifactAuthorizationError("artifact_cleanup_required")
            if current is None:
                parent.artifact_cleanup_token = token
                parent.artifact_cleanup_claimed_at = now
            elif not hmac.compare_digest(current, token):
                raise ArtifactAuthorizationError("artifact_cleanup_required")
        db.flush()
        return token

    def delete_for_cloud_file(
        self,
        db: Any,
        *,
        department_id: str,
        cloud_file_id: str,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        return self.delete_for_cloud_files(
            db,
            department_id=department_id,
            cloud_file_ids=[cloud_file_id],
            destructive_actor_ref=destructive_actor_ref,
        )

    def delete_for_cloud_files(
        self,
        db: Any,
        *,
        department_id: str,
        cloud_file_ids: list[str],
        allow_approved_unwritten: bool = False,
        destructive_actor_ref: str | None = None,
        cleanup_reason: str = "cloud_file_delete",
        cleanup_owner: str | None = None,
        _cleanup_token: str | None = None,
    ) -> ParentCleanupTransaction:
        """Claim all file artifacts, remove bytes, and stage their row deletes."""
        department_id = _canonical_uuid(department_id, "department_id")
        requested_ids = {
            _canonical_uuid(value, "cloud_file_id") for value in cloud_file_ids
        }
        if not requested_ids:
            return ParentCleanupTransaction(
                (),
                None,
                cleanup_reason,
                cleanup_owner or department_id,
                cleanup_token=_cleanup_token,
            )
        if len(requested_ids) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        self._locked(db, Department, department_id, "department")
        cloud_files = (
            db.query(CloudFile)
            .filter(
                CloudFile.id.in_(requested_ids),
                CloudFile.department_id == department_id,
            )
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        if {row.id for row in cloud_files} != requested_ids:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        if _cleanup_token is None:
            cleanup_token = self._fence_cleanup_parents(db, cloud_files)
        else:
            cleanup_token = _cleanup_token
            now = datetime.now(timezone.utc)
            for cloud_file in cloud_files:
                current = getattr(cloud_file, "artifact_cleanup_token", None)
                claimed_at = getattr(cloud_file, "artifact_cleanup_claimed_at", None)
                if (current is None) != (claimed_at is None):
                    raise ArtifactAuthorizationError("artifact_cleanup_required")
                if current is None:
                    cloud_file.artifact_cleanup_token = cleanup_token
                    cloud_file.artifact_cleanup_claimed_at = now
                elif not hmac.compare_digest(current, cleanup_token):
                    raise ArtifactAuthorizationError("artifact_cleanup_required")
            db.flush()
        artifacts = (
            db.query(RemediationArtifact)
            .filter(RemediationArtifact.cloud_file_id.in_(requested_ids))
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        current_ids = {
            row.current_remediation_artifact_id
            for row in cloud_files
            if row.current_remediation_artifact_id
        }
        effective_owner = (
            cleanup_owner
            or hashlib.sha256(",".join(sorted(requested_ids)).encode()).hexdigest()
        )
        resuming = bool(artifacts) and all(
            artifact.cleanup_claimed_at is not None
            and artifact.cleanup_reason == cleanup_reason
            and artifact.cleanup_owner == effective_owner
            for artifact in artifacts
        )
        if len(artifacts) > _PARENT_CLEANUP_LIMIT or any(
            artifact.department_id != department_id
            or (
                destructive_actor_ref is None
                and not resuming
                and (
                    artifact.id in current_ids
                    or (
                        artifact.review_status == "approved"
                        and artifact.written_back_at is None
                        and not allow_approved_unwritten
                    )
                )
            )
            for artifact in artifacts
        ):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self._prepare_parent_cleanup(
            db,
            artifacts=artifacts,
            reason=cleanup_reason,
            owner=effective_owner,
            force=destructive_actor_ref is not None,
            destructive_actor_ref=destructive_actor_ref,
            allow_approved_unwritten=allow_approved_unwritten,
            cleanup_token=cleanup_token,
        )

    def delete_for_scan(
        self,
        db: Any,
        *,
        department_id: str,
        scan_id: str,
        allow_approved_unwritten: bool = False,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        """Claim scan artifacts and stage them with the caller's scan delete."""
        department_id = _canonical_uuid(department_id, "department_id")
        scan_id = _canonical_uuid(scan_id, "scan_id")
        self._locked(db, Department, department_id, "department")
        scan = self._locked(db, Scan, scan_id, "scan")
        if scan.department_id != department_id:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        cleanup_token = self._fence_cleanup_parents(db, [scan])
        artifacts = (
            db.query(RemediationArtifact)
            .filter(
                RemediationArtifact.scan_id == scan_id,
                RemediationArtifact.department_id == department_id,
            )
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        cloud_ids = [
            artifact.cloud_file_id for artifact in artifacts if artifact.cloud_file_id
        ]
        cloud_files = (
            db.query(CloudFile)
            .filter(CloudFile.id.in_(cloud_ids))
            .with_for_update()
            .populate_existing()
            .all()
            if cloud_ids
            else []
        )
        cloud_current_hold = any(
            cloud_file.current_remediation_artifact_id is not None
            for cloud_file in cloud_files
        )
        resuming = bool(artifacts) and all(
            artifact.cleanup_claimed_at is not None
            and artifact.cleanup_reason == "scan_delete"
            and artifact.cleanup_owner == scan_id
            for artifact in artifacts
        )
        if len(artifacts) > _PARENT_CLEANUP_LIMIT or (
            destructive_actor_ref is None
            and not resuming
            and (
                scan.current_remediation_artifact_id is not None
                or cloud_current_hold
                or any(
                    artifact.review_status == "approved"
                    and artifact.written_back_at is None
                    and not allow_approved_unwritten
                    for artifact in artifacts
                )
            )
        ):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self._prepare_parent_cleanup(
            db,
            artifacts=artifacts,
            reason="scan_delete",
            owner=scan_id,
            force=destructive_actor_ref is not None,
            destructive_actor_ref=destructive_actor_ref,
            allow_approved_unwritten=allow_approved_unwritten,
            cleanup_token=cleanup_token,
        )

    def delete_for_credential(
        self,
        db: Any,
        *,
        department_id: str,
        credential_id: str,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        """Delete managed children for one exact provider credential."""
        department_id = _canonical_uuid(department_id, "department_id")
        credential_id = _canonical_uuid(credential_id, "credential_id")
        self._locked(db, Department, department_id, "department")
        credential = self._locked(
            db, CloudOAuthCredentials, credential_id, "credential"
        )
        if credential.department_id != department_id:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        cleanup_token = self._fence_cleanup_parents(db, [credential])
        files = (
            db.query(CloudFile)
            .filter(CloudFile.credential_id == credential_id)
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        if len(files) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self.delete_for_cloud_files(
            db,
            department_id=department_id,
            cloud_file_ids=[row.id for row in files],
            destructive_actor_ref=destructive_actor_ref,
            cleanup_reason="credential_disconnect",
            cleanup_owner=credential_id,
            _cleanup_token=cleanup_token,
        )

    def delete_for_provider(
        self,
        db: Any,
        *,
        department_id: str,
        provider: str,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        """Delete managed children for a supported provider in one tenant."""
        if provider not in _PROVIDERS - {"local"}:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        department_id = _canonical_uuid(department_id, "department_id")
        self._locked(db, Department, department_id, "department")
        credentials = (
            db.query(CloudOAuthCredentials)
            .filter(
                CloudOAuthCredentials.department_id == department_id,
                CloudOAuthCredentials.provider == provider,
            )
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        if len(credentials) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        cleanup_token = (
            self._fence_cleanup_parents(db, credentials)
            if credentials
            else secrets.token_urlsafe(32)
        )
        credential_ids = [credential.id for credential in credentials]
        files = (
            db.query(CloudFile)
            .filter(CloudFile.credential_id.in_(credential_ids))
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
            if credential_ids
            else []
        )
        if len(files) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self.delete_for_cloud_files(
            db,
            department_id=department_id,
            cloud_file_ids=[row.id for row in files],
            destructive_actor_ref=destructive_actor_ref,
            cleanup_reason="provider_disconnect",
            cleanup_owner=f"{provider}:{department_id}",
            _cleanup_token=cleanup_token,
        )

    def delete_for_department(
        self,
        db: Any,
        *,
        department_id: str,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        """Delete all tenant artifacts under explicit bounded authority."""
        department_id = _canonical_uuid(department_id, "department_id")
        department = self._locked(db, Department, department_id, "department")
        cleanup_token = self._fence_cleanup_parents(db, [department])
        scans = (
            db.query(Scan)
            .filter(Scan.department_id == department_id)
            .order_by(Scan.id)
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        if len(scans) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self._cleanup_for_scans(
            db,
            department_id=department_id,
            scans=scans,
            reason="department_delete",
            owner=department_id,
            destructive_actor_ref=destructive_actor_ref,
            cleanup_token=cleanup_token,
        )

    def cleanup_for_user(
        self,
        db: Any,
        *,
        department_id: str,
        user_id: str,
        destructive_actor_ref: str | None = None,
    ) -> ParentCleanupTransaction:
        """Stage only artifacts belonging to scans owned by one tenant user."""
        department_id = _canonical_uuid(department_id, "department_id")
        user_id = _canonical_uuid(user_id, "user_id")
        self._locked(db, Department, department_id, "department")
        user = self._locked(db, User, user_id, "user")
        if user.department_id != department_id:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        cleanup_token = self._fence_cleanup_parents(db, [user])
        scans = (
            db.query(Scan)
            .filter(Scan.user_id == user_id, Scan.department_id == department_id)
            .order_by(Scan.id)
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
        )
        if len(scans) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self._cleanup_for_scans(
            db,
            department_id=department_id,
            scans=scans,
            reason="account_deletion",
            owner=user_id,
            destructive_actor_ref=destructive_actor_ref,
            cleanup_token=cleanup_token,
        )

    def _cleanup_for_scans(
        self,
        db: Any,
        *,
        department_id: str,
        scans: list[Any],
        reason: str,
        owner: str,
        destructive_actor_ref: str | None,
        cleanup_token: str,
    ) -> ParentCleanupTransaction:
        scan_ids = [scan.id for scan in scans]
        artifacts = (
            db.query(RemediationArtifact)
            .filter(
                RemediationArtifact.scan_id.in_(scan_ids),
                RemediationArtifact.department_id == department_id,
            )
            .with_for_update()
            .populate_existing()
            .limit(_PARENT_CLEANUP_LIMIT + 1)
            .all()
            if scan_ids
            else []
        )
        if len(artifacts) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        return self._prepare_parent_cleanup(
            db,
            artifacts=artifacts,
            reason=reason,
            owner=owner,
            force=destructive_actor_ref is not None,
            destructive_actor_ref=destructive_actor_ref,
            cleanup_token=cleanup_token,
        )

    def _claim_parent_artifacts(
        self,
        db: Any,
        *,
        artifacts: list[Any],
        reason: str,
        owner: str,
        force: bool,
        destructive_actor_ref: str | None,
        now: datetime,
    ) -> tuple[list[Any], datetime | None]:
        claimed_at: datetime | None = None
        for artifact in artifacts:
            if artifact.cleanup_claimed_at is None:
                continue
            if artifact.cleanup_reason != reason or artifact.cleanup_owner != owner:
                raise ArtifactAuthorizationError("artifact_cleanup_required")
            existing = _utc(artifact.cleanup_claimed_at)
            if claimed_at is not None and existing != claimed_at:
                raise ArtifactAuthorizationError("artifact_cleanup_required")
            claimed_at = existing
        claimed_at = claimed_at or now
        for artifact in artifacts:
            if artifact.cleanup_claimed_at is not None:
                continue
            if force and destructive_actor_ref is not None:
                self._force_terminal_rejection(
                    artifact, actor_ref=destructive_actor_ref, now=now
                )
            artifact.cleanup_claimed_at = claimed_at
            artifact.cleanup_reason = reason
            artifact.cleanup_owner = owner
        db.flush()
        return artifacts, claimed_at if artifacts else None

    def _prepare_parent_cleanup(
        self,
        db: Any,
        *,
        artifacts: list[Any],
        reason: str,
        owner: str,
        force: bool,
        destructive_actor_ref: str | None = None,
        allow_approved_unwritten: bool = False,
        cleanup_token: str | None = None,
    ) -> ParentCleanupTransaction:
        if not reason or len(reason) > 64 or not owner or len(owner) > 255:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        if len(artifacts) > _PARENT_CLEANUP_LIMIT:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        artifact_ids = [artifact.id for artifact in artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        resuming = bool(artifacts) and all(
            artifact.cleanup_claimed_at is not None
            and artifact.cleanup_reason == reason
            and artifact.cleanup_owner == owner
            for artifact in artifacts
        )
        if (
            not force
            and not resuming
            and any(
                artifact.review_status == "approved"
                and artifact.written_back_at is None
                and not allow_approved_unwritten
                for artifact in artifacts
            )
        ):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        if artifact_ids and not force and not resuming:
            unresolved = (
                db.query(ContentWritebackLog)
                .filter(
                    ContentWritebackLog.artifact_id.in_(artifact_ids),
                    ContentWritebackLog.reconciliation_status
                    == "reconciliation_required",
                )
                .first()
            )
            if unresolved is not None:
                raise ArtifactAuthorizationError("artifact_cleanup_required")
        if cleanup_token is None:
            cleanup_token = secrets.token_urlsafe(32)
        if not isinstance(cleanup_token, str) or not (43 <= len(cleanup_token) <= 64):
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        now = datetime.now(timezone.utc)
        claimed, claimed_at = self._claim_parent_artifacts(
            db,
            artifacts=artifacts,
            reason=reason,
            owner=owner,
            force=force,
            destructive_actor_ref=destructive_actor_ref,
            now=now,
        )
        if not claimed:
            try:
                db.commit()
            except Exception:
                db.rollback()
                raise
            return ParentCleanupTransaction(
                (), None, reason, owner, cleanup_token=cleanup_token
            )
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        removed = 0
        missing = 0
        for artifact in claimed:
            if self.delete_known(artifact):
                removed += 1
            else:
                missing += 1
        self._stage_claimed_parent_cleanup(
            db,
            artifact_ids=tuple(artifact_ids),
            claimed_at=claimed_at,
            reason=reason,
            owner=owner,
            cleanup_token=cleanup_token,
        )
        return ParentCleanupTransaction(
            tuple(artifact_ids),
            claimed_at,
            reason,
            owner,
            removed,
            missing,
            cleanup_token,
        )

    def _stage_claimed_parent_cleanup(
        self,
        db: Any,
        *,
        artifact_ids: tuple[str, ...],
        claimed_at: datetime | None,
        reason: str,
        owner: str,
        cleanup_token: str,
    ) -> None:
        # Discover coordinates without locks, then use the canonical helper so
        # finalization has no Artifact→parent acquisition path.
        metadata = [
            self._artifact_metadata(db, artifact_id) for artifact_id in artifact_ids
        ]
        artifacts = []
        locked_scans: dict[str, Any] = {}
        locked_cloud_files: dict[str, Any] = {}
        for item in sorted(metadata, key=lambda value: value.id):
            _, scan, cloud_file, _, artifact = self._lock_existing_artifact(
                db, item.id, expected_cleanup_token=cleanup_token
            )
            assert artifact is not None
            artifacts.append(artifact)
            locked_scans[scan.id] = scan
            if cloud_file is not None:
                locked_cloud_files[cloud_file.id] = cloud_file
        if {artifact.id for artifact in artifacts} != set(artifact_ids) or any(
            artifact.cleanup_claimed_at is None
            or _utc(artifact.cleanup_claimed_at) != claimed_at
            or artifact.cleanup_reason != reason
            or artifact.cleanup_owner != owner
            for artifact in artifacts
        ):
            db.rollback()
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        db.query(ContentWritebackLog).filter(
            ContentWritebackLog.artifact_id.in_(artifact_ids)
        ).delete(synchronize_session=False)
        cloud_ids = {
            artifact.cloud_file_id for artifact in artifacts if artifact.cloud_file_id
        }
        if cloud_ids:
            if set(locked_cloud_files) != cloud_ids:
                raise ArtifactAuthorizationError("artifact_cleanup_required")
            for cloud_file in locked_cloud_files.values():
                if cloud_file.current_remediation_artifact_id in artifact_ids:
                    cloud_file.current_remediation_artifact_id = None
                    cloud_file.has_remediated_version = False
                    cloud_file.remediation_origin = None
                    if cloud_file.writeback_status == "approved":
                        cloud_file.writeback_status = "rejected"
        scan_ids = {artifact.scan_id for artifact in artifacts}
        if set(locked_scans) != scan_ids:
            raise ArtifactAuthorizationError("artifact_cleanup_required")
        for scan in locked_scans.values():
            if scan.current_remediation_artifact_id in artifact_ids:
                scan.current_remediation_artifact_id = None
        for artifact in artifacts:
            db.delete(artifact)
        db.flush()

    def delete_known(
        self, artifact: PreparedRemediationArtifact | RemediationArtifact
    ) -> bool:
        parts = self._storage_parts(artifact)
        try:
            with self._storage_directory(parts[:-1], create=False) as directory_fd:
                names = (parts[-1], f"{parts[-1]}.partial")
                present: list[str] = []
                for name in names:
                    try:
                        state = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        continue
                    if not stat.S_ISREG(state.st_mode):
                        raise ArtifactIntegrityError(
                            "refusing to delete a nonregular artifact"
                        )
                    present.append(name)
                for name in present:
                    try:
                        os.unlink(name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
                if present:
                    os.fsync(directory_fd)
                return bool(present)
        except FileNotFoundError:
            return False
        except ArtifactIntegrityError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return False
            raise

    def _storage_parts(
        self, artifact: PreparedRemediationArtifact | RemediationArtifact
    ) -> tuple[str, ...]:
        if artifact.storage_backend != "local":
            raise ArtifactIntegrityError("artifact storage backend is unsupported")
        try:
            artifact_id = _canonical_uuid(artifact.id, "artifact_id")
            department_id = _canonical_uuid(artifact.department_id, "department_id")
            scan_id = _canonical_uuid(artifact.scan_id, "scan_id")
        except ArtifactValidationError as exc:
            raise ArtifactIntegrityError("artifact identity is invalid") from exc
        key = artifact.storage_key
        if not isinstance(key, str) or "\\" in key:
            raise ArtifactIntegrityError("artifact storage key is invalid")
        relative = PurePosixPath(key)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 4:
            raise ArtifactIntegrityError("artifact storage key is not confined")
        if tuple(relative.parts[:3]) != (department_id, scan_id, artifact_id):
            raise ArtifactIntegrityError(
                "artifact storage layout does not match authority"
            )
        leaf = Path(relative.parts[-1])
        try:
            _canonical_uuid(leaf.stem, "artifact filename")
        except ArtifactValidationError as exc:
            raise ArtifactIntegrityError(
                "artifact storage filename is not opaque"
            ) from exc
        if leaf.suffix.lower() not in _MIME_BY_EXTENSION:
            raise ArtifactIntegrityError("artifact storage extension is unsupported")
        return relative.parts


@dataclass(frozen=True)
class _CleanupTarget:
    id: str
    claimed_at: datetime


class RemediationArtifactCleanup:
    """Durably claim, re-lock/revalidate, then descriptor-confined delete."""

    def __init__(
        self,
        *,
        service: RemediationArtifactService,
        batch_size: int,
        staging_grace_seconds: int,
    ) -> None:
        if batch_size < 1 or staging_grace_seconds < 1:
            raise ValueError("cleanup bounds must be positive")
        self.service = service
        self.batch_size = batch_size
        self.staging_grace_seconds = staging_grace_seconds

    @staticmethod
    def _eligible_after_select(
        artifact: RemediationArtifact, *, now: datetime, claim_cutoff: datetime
    ) -> bool:
        if (
            artifact.review_status == "approved"
            and artifact.written_back_at is None
            and _utc(artifact.expires_at) > now
        ):
            return False
        claimed_at = artifact.cleanup_claimed_at
        if claimed_at is not None and (
            artifact.cleanup_reason != "scheduled_cleanup"
            or artifact.cleanup_owner != "scheduler"
        ):
            return False
        if claimed_at is not None and _utc(claimed_at) > claim_cutoff:
            return False
        if artifact.lifecycle_status == "staging":
            heartbeat = artifact.publication_heartbeat_at
            return heartbeat is not None and _utc(heartbeat) <= claim_cutoff
        if artifact.lifecycle_status in {"expired", "superseded"}:
            return True
        return (
            artifact.lifecycle_status == "available"
            and _utc(artifact.expires_at) <= now
        )

    def run_batch(self, db: Any, *, now: datetime | None = None) -> dict[str, int]:
        now = _utc(now or datetime.now(timezone.utc))
        claim_cutoff = now - timedelta(seconds=self.staging_grace_seconds)
        candidates = (
            db.query(RemediationArtifact)
            .filter(
                or_(
                    and_(
                        RemediationArtifact.lifecycle_status == "staging",
                        RemediationArtifact.publication_heartbeat_at <= claim_cutoff,
                    ),
                    RemediationArtifact.lifecycle_status.in_(("expired", "superseded")),
                    and_(
                        RemediationArtifact.lifecycle_status == "available",
                        RemediationArtifact.expires_at <= now,
                    ),
                ),
                or_(
                    RemediationArtifact.review_status != "approved",
                    RemediationArtifact.written_back_at.isnot(None),
                    and_(
                        RemediationArtifact.review_status == "approved",
                        RemediationArtifact.written_back_at.is_(None),
                        RemediationArtifact.expires_at <= now,
                    ),
                ),
                or_(
                    RemediationArtifact.cleanup_claimed_at.is_(None),
                    and_(
                        RemediationArtifact.cleanup_claimed_at <= claim_cutoff,
                        RemediationArtifact.cleanup_reason == "scheduled_cleanup",
                        RemediationArtifact.cleanup_owner == "scheduler",
                    ),
                ),
            )
            .order_by(
                RemediationArtifact.expires_at.asc(),
                RemediationArtifact.created_at.asc(),
            )
            .limit(self.batch_size)
            .all()
        )
        targets: list[_CleanupTarget] = []
        for candidate in candidates:
            try:
                _, _, _, _, artifact = self.service._lock_existing_artifact(
                    db, candidate.id, skip_locked=True
                )
                if artifact is not None and self._eligible_after_select(
                    artifact, now=now, claim_cutoff=claim_cutoff
                ):
                    artifact.cleanup_claimed_at = now
                    artifact.cleanup_reason = "scheduled_cleanup"
                    artifact.cleanup_owner = "scheduler"
                    targets.append(_CleanupTarget(artifact.id, now))
            except ArtifactAuthorizationError:
                continue
        result = {"claimed": len(targets), "deleted": 0, "missing": 0, "failed": 0}
        if not targets:
            if candidates:
                db.rollback()
            return result
        try:
            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            result["failed"] = len(targets)
            result["claimed"] = 0
            return result

        for target in targets:
            try:
                _, scan, cloud_file, _, artifact = self.service._lock_existing_artifact(
                    db, target.id
                )
                if artifact is None:
                    db.rollback()
                    result["failed"] += 1
                    continue
                exact_claim = (
                    artifact.cleanup_claimed_at is not None
                    and _utc(artifact.cleanup_claimed_at) == target.claimed_at
                    and artifact.cleanup_reason == "scheduled_cleanup"
                    and artifact.cleanup_owner == "scheduler"
                )
                eligible = self._eligible_after_select(
                    artifact, now=now, claim_cutoff=now
                )
                if not exact_claim:
                    db.rollback()
                    continue
                if not eligible:
                    artifact.cleanup_claimed_at = None
                    artifact.cleanup_reason = None
                    artifact.cleanup_owner = None
                    db.commit()
                    continue
                removed = self.service.delete_known(artifact)
                artifact.lifecycle_status = "deleted"
                artifact.publication_token = None
                artifact.publication_heartbeat_at = None
                artifact.deleted_at = now
                artifact.cleanup_claimed_at = None
                artifact.cleanup_reason = None
                artifact.cleanup_owner = None
                if (
                    cloud_file is not None
                    and cloud_file.current_remediation_artifact_id == artifact.id
                ):
                    cloud_file.current_remediation_artifact_id = None
                    cloud_file.has_remediated_version = False
                    cloud_file.remediation_origin = None
                if (
                    cloud_file is None
                    and scan.current_remediation_artifact_id == artifact.id
                ):
                    scan.current_remediation_artifact_id = None
                db.delete(artifact)
                db.commit()
            except Exception:
                db.rollback()
                result["failed"] += 1
                continue
            if removed:
                result["deleted"] += 1
            else:
                result["missing"] += 1
        return result
