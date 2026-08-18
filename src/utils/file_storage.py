"""
File Storage Utilities

Handles persistent storage of uploaded files for remediation.
"""

import os
import shutil
import hashlib
import logging
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


def get_scan_storage_dir(department_id: str, scan_id: str) -> Path:
    """
    Get the storage directory for a scan.

    Args:
        department_id: Department ID
        scan_id: Scan ID

    Returns:
        Path to scan directory
    """
    scan_dir = UPLOAD_BASE_DIR / department_id / scan_id
    return scan_dir


def ensure_storage_dir(directory: Path) -> None:
    """
    Ensure a storage directory exists.

    Args:
        directory: Directory path to create
    """
    directory.mkdir(parents=True, exist_ok=True)
    logger.info(f"Ensured storage directory exists: {directory}")


async def save_uploaded_file(
    file: UploadFile,
    department_id: str,
    scan_id: str,
    original_filename: Optional[str] = None,
) -> str:
    """
    Save uploaded file to persistent storage.

    Args:
        file: FastAPI UploadFile object
        department_id: Department ID
        scan_id: Scan ID
        original_filename: Optional original filename (defaults to file.filename)

    Returns:
        Absolute path to saved file
    """
    # Get storage directory
    scan_dir = get_scan_storage_dir(department_id, scan_id)
    ensure_storage_dir(scan_dir)

    # Use original filename or file.filename
    filename = original_filename or file.filename
    if not filename:
        raise ValueError("Filename is required")

    # Sanitize filename to prevent path traversal attacks
    filename = os.path.basename(filename)
    if not filename or filename.startswith("."):
        raise ValueError("Invalid filename")

    file_path = scan_dir / filename

    # Verify resolved path is within the scan directory
    if not file_path.resolve().is_relative_to(scan_dir.resolve()):
        raise ValueError("Invalid filename: path traversal detected")

    # Save file
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        logger.info(
            f"Saved uploaded file: {filename} -> {file_path} ({len(content)} bytes)"
        )

        return str(file_path)

    except Exception as e:
        logger.error(f"Error saving uploaded file {filename}: {e}")
        raise


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

    logger.info(f"Created working copy for remediation: {work_copy_path}")

    return str(work_copy_path)


def cleanup_scan_files(department_id: str, scan_id: str) -> None:
    """
    Clean up all files for a scan (optional, for storage management).

    Args:
        department_id: Department ID
        scan_id: Scan ID
    """
    scan_dir = get_scan_storage_dir(department_id, scan_id)

    if scan_dir.exists():
        try:
            shutil.rmtree(scan_dir)
            logger.info(f"Cleaned up scan directory: {scan_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up scan directory {scan_dir}: {e}")


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
    "get_file_hash",
    "get_remediated_file_path",
    "copy_file_for_remediation",
    "cleanup_scan_files",
    "get_file_size",
]
