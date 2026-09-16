"""Exercise shared upload storage as the quickstart API and worker users."""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
from io import BytesIO
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.file_storage import (  # noqa: E402
    UPLOAD_BASE_DIR,
    get_scan_storage_dir,
    save_uploaded_file,
)

PAYLOAD = b"Aelira quickstart shared storage probe\n"
DEPARTMENT = "quickstart-storage-smoke"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("upload", "worker", "verify"))
    parser.add_argument("--probe-id", required=True, type=UUID)
    args = parser.parse_args()

    if os.geteuid() == 0:
        raise RuntimeError("Storage smoke must run as the non-root runtime user")
    if UPLOAD_BASE_DIR.stat().st_mode & stat.S_IWOTH:
        raise RuntimeError("Upload storage must not be world-writable")

    scan_id = str(args.probe_id)
    directory = get_scan_storage_dir(DEPARTMENT, scan_id)
    upload_path = directory / "upload.txt"
    worker_path = directory / "worker.txt"
    if args.phase == "upload":
        if directory.exists():
            raise RuntimeError("Use a fresh probe ID to avoid overwriting storage")
        upload = UploadFile(filename="upload.txt", file=BytesIO(PAYLOAD))
        try:
            saved = asyncio.run(save_uploaded_file(upload, DEPARTMENT, scan_id))
        finally:
            upload.file.close()
        if Path(saved) != upload_path:
            raise RuntimeError("Upload was saved outside the expected probe directory")

    if upload_path.read_bytes() != PAYLOAD:
        raise RuntimeError("Uploaded bytes did not survive the shared volume")
    if args.phase == "worker":
        with worker_path.open("xb") as output:
            output.write(PAYLOAD)
    if args.phase in {"worker", "verify"} and worker_path.read_bytes() != PAYLOAD:
        raise RuntimeError("Worker bytes did not survive the shared volume")
    print(f"PASS storage {args.phase}: uid={os.geteuid()}")


if __name__ == "__main__":
    main()
