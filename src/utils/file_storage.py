"""
File Storage Utilities

Handles persistent storage of uploaded files for remediation.
"""

import os
import shutil
import hashlib
import logging
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from fastapi import UploadFile

logger = logging.getLogger(__name__)

# Base directory for uploaded files.
#
# The default used to be the absolute /app/uploads, which is correct inside
# the container and wrong everywhere else: running from source, the process
# tried to create a directory at the filesystem root and failed with a
# permission error. Deriving it from the working directory keeps the
# container behaviour identical, because the container works out of /app,
# and gives every other environment a writable path it owns.
UPLOAD_BASE_DIR = Path(os.environ.get("UPLOAD_DIR") or Path.cwd() / "uploads")

_STORAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _validate_storage_id(value: str) -> str:
    if not isinstance(value, str) or not _STORAGE_ID.fullmatch(value):
        raise ValueError("Invalid storage identifier")
    return value


@contextmanager
def _storage_directory(directory: Path, *, create: bool = False):
    """Open confined directories without following tenant or scan symlinks.

    The configured root is operator-controlled. All descendants are opened
    relative to directory descriptors so replacing a parent with a symlink
    cannot redirect a write after validation.
    """
    root = UPLOAD_BASE_DIR.resolve()
    try:
        parts = directory.absolute().relative_to(root).parts
    except ValueError as exc:
        raise ValueError("Storage directory is outside the upload root") from exc
    if not parts or any(not _STORAGE_ID.fullmatch(part) for part in parts):
        raise ValueError("Invalid storage directory")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def get_scan_storage_dir(department_id: str, scan_id: str) -> Path:
    """
    Get the storage directory for a scan.

    Args:
        department_id: Department ID
        scan_id: Scan ID

    Returns:
        Path to scan directory
    """
    scan_dir = (
        UPLOAD_BASE_DIR.resolve()
        / _validate_storage_id(department_id)
        / _validate_storage_id(scan_id)
    )
    return scan_dir


def ensure_storage_dir(directory: Path) -> None:
    """
    Ensure a storage directory exists.

    Args:
        directory: Directory path to create
    """
    with _storage_directory(directory, create=True):
        pass
    logger.info("Upload storage directory ready")


def save_scan_bytes(
    content: bytes,
    department_id: str,
    scan_id: str,
    filename: str,
    *,
    mode: int = 0o600,
) -> str:
    """Create a new scan file through confined directory descriptors."""
    if not filename:
        raise ValueError("Filename is required")

    # Sanitize filename to prevent path traversal attacks
    filename = os.path.basename(filename.replace("\\", "/"))
    if (
        not filename
        or filename.startswith(".")
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        raise ValueError("Invalid filename")

    scan_dir = get_scan_storage_dir(department_id, scan_id)
    ensure_storage_dir(scan_dir)

    file_path = scan_dir / filename

    # Verify resolved path is within the scan directory
    if not file_path.resolve().is_relative_to(scan_dir.resolve()):
        raise ValueError("Invalid filename: path traversal detected")

    # Save file
    try:
        with _storage_directory(scan_dir) as directory_fd:
            descriptor = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                os.fchmod(stream.fileno(), mode)

        logger.info("Uploaded file saved (%s bytes)", len(content))

        return str(file_path)

    except Exception as e:
        logger.error("Uploaded file save failed (%s)", type(e).__name__)
        raise


async def save_uploaded_file(
    file: UploadFile,
    department_id: str,
    scan_id: str,
    original_filename: Optional[str] = None,
) -> str:
    """Save an uploaded file without following storage symlinks."""
    return save_scan_bytes(
        await file.read(), department_id, scan_id, original_filename or file.filename
    )


def get_file_hash(file_path: str) -> str:
    """
    Calculate SHA-256 hash of a file.

    Args:
        file_path: Path to file

    Returns:
        Hex digest of SHA-256 hash
    """
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as f:
        while True:
            data = f.read(65536)  # 64KB chunks
            if not data:
                break
            sha256.update(data)

    return sha256.hexdigest()


def get_remediated_file_path(original_path: str) -> str:
    """
    Get path for remediated version of a file.

    Args:
        original_path: Path to original file

    Returns:
        Path to remediated file (with _remediated suffix)
    """
    path = Path(original_path)
    remediated_name = f"{path.stem}_remediated{path.suffix}"
    return str(path.parent / remediated_name)


def copy_file_for_remediation(original_path: str) -> str:
    """
    Create a copy of file for remediation (leaves original intact).

    Args:
        original_path: Path to original file

    Returns:
        Path to copied file for remediation
    """
    path = Path(original_path)
    work_copy_name = f"{path.stem}_working{path.suffix}"
    work_copy_path = path.parent / work_copy_name

    shutil.copy2(original_path, work_copy_path)

    logger.info("Working copy created for remediation")

    return str(work_copy_path)


def cleanup_scan_files(department_id: str, scan_id: str) -> None:
    """
    Clean up all files for a scan (optional, for storage management).

    Args:
        department_id: Department ID
        scan_id: Scan ID
    """
    scan_dir = get_scan_storage_dir(department_id, scan_id)

    try:
        with _storage_directory(scan_dir.parent) as department_fd:
            # rmtree uses its descriptor-relative, symlink-safe implementation.
            shutil.rmtree(scan_dir.name, dir_fd=department_fd)
        logger.info("Scan storage cleaned up")
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error("Scan storage cleanup failed (%s)", type(e).__name__)


def get_file_size(file_path: str) -> int:
    """
    Get file size in bytes.

    Args:
        file_path: Path to file

    Returns:
        File size in bytes
    """
    return Path(file_path).stat().st_size


__all__ = [
    "UPLOAD_BASE_DIR",
    "get_scan_storage_dir",
    "ensure_storage_dir",
    "save_uploaded_file",
    "save_scan_bytes",
    "get_file_hash",
    "get_remediated_file_path",
    "copy_file_for_remediation",
    "cleanup_scan_files",
    "get_file_size",
]
