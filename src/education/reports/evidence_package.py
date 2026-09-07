"""Portable, deterministic review-evidence packages and offline verification."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import zipfile
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import PurePosixPath
from typing import Any

SCHEMA_VERSION = "1.0.0"
SUPPORTED_SCHEMA_MAJOR = 1
MANIFEST_PATH = "manifest.json"

_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_MEMBERS = 3
_MAX_MEMBER_NAME = 128
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MISSING_VALUES = frozenset({"", "unavailable", "not recorded", "not_recorded"})
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_key|authorization|cookie|password|secret|token|"
    r"storage_path|storage_key|server_path|filesystem_path)(?:$|_)",
    re.IGNORECASE,
)
_SERVER_PATH = re.compile(
    r"(?:^|[\s=:])/(?!/)|(?:^|\s)[A-Za-z]:[\\/]|file://",
    re.IGNORECASE,
)


class EvidencePackageError(ValueError):
    """Raised when evidence-package construction or verification fails closed."""


@dataclass(frozen=True)
class EvidenceFile:
    """Explicitly authorized document bytes to include in a package."""

    filename: str
    media_type: str | None
    content: bytes


def _tool_version() -> str:
    try:
        return version("aelira-core")
    except PackageNotFoundError:
        return "not_recorded"


def _present(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in _MISSING_VALUES:
        return None
    return value


def _safe_filename(value: Any, *, fallback: str, maximum: int) -> str:
    raw = str(_present(value) or fallback).replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).lstrip(".") or fallback
    if len(safe) <= maximum:
        return safe
    suffix = PurePosixPath(safe).suffix[:16]
    stem_limit = max(1, maximum - len(suffix))
    return f"{safe[:stem_limit]}{suffix}"


def _member_name(role: str, filename: str) -> str:
    prefix = f"files/{role}-"
    component = _safe_filename(
        filename,
        fallback=f"{role}.bin",
        maximum=_MAX_MEMBER_NAME - len(prefix),
    )
    return f"{prefix}{component}"


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidencePackageError(
            "manifest evidence is not JSON serializable"
        ) from exc


def _bounded_package_value(value: Any) -> Any:
    """Remove server coordinates and credential-shaped fields from evidence."""
    if isinstance(value, dict):
        bounded = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            bounded[key] = (
                "redacted"
                if _SENSITIVE_KEY.search(key)
                else _bounded_package_value(item)
            )
        return bounded
    if isinstance(value, (list, tuple)):
        return [_bounded_package_value(item) for item in value]
    if isinstance(value, str) and _SERVER_PATH.search(value):
        return "redacted"
    return value


def _evidence_node(
    role: str,
    raw: Any,
    *,
    scan: dict[str, Any],
    file: EvidenceFile | None,
) -> dict[str, Any]:
    record = raw if isinstance(raw, dict) else {}
    is_source = role == "source"
    identity = _present(record.get("document_id" if is_source else "id"))
    recorded_filename = _present(record.get("filename")) or (
        _present(scan.get("file_name")) if is_source else None
    )
    filename = (
        _safe_filename(recorded_filename, fallback=f"{role}.bin", maximum=120)
        if recorded_filename is not None
        else None
    )
    media_type = _present(record.get("media_type" if is_source else "mime_type"))
    size_bytes = _present(record.get("size_bytes"))
    sha256 = _present(record.get("sha256"))
    raw_availability = record.get("availability")
    availability = (
        raw_availability.strip()
        if isinstance(raw_availability, str) and raw_availability.strip()
        else None
    )
    if availability is None:
        availability = (
            "recorded"
            if any(
                value is not None
                for value in (identity, filename, media_type, size_bytes, sha256)
            )
            else "not_recorded"
        )

    node: dict[str, Any] = {
        "availability": availability,
        "identity": identity,
        "source": _present(record.get("document_source")) if is_source else None,
        "filename": filename,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "timestamps": {
            "created_at": _present(record.get("created_at"))
            or (_present(scan.get("created_at")) if is_source else None),
            "completed_at": (
                _present(record.get("completed_at"))
                or _present(scan.get("completed_at"))
                if is_source
                else None
            ),
            "updated_at": _present(record.get("updated_at")),
            "expires_at": _present(record.get("expires_at")),
            "written_back_at": _present(record.get("written_back_at")),
        },
        "review_status": (None if is_source else _present(record.get("review_status"))),
        "approval_review_digest": (
            None if is_source else _present(record.get("approval_review_digest"))
        ),
        "included": file is not None,
        "path": None,
    }
    if file is None:
        return node
    if not isinstance(file.content, bytes):
        raise EvidencePackageError(f"{role} content must be bytes")

    actual_size = len(file.content)
    actual_sha256 = hashlib.sha256(file.content).hexdigest()
    if size_bytes is not None and (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes != actual_size
    ):
        raise EvidencePackageError(f"{role} size mismatch")
    if sha256 is not None and (
        not isinstance(sha256, str)
        or not _SHA256.fullmatch(sha256)
        or sha256 != actual_sha256
    ):
        raise EvidencePackageError(f"{role} checksum mismatch")

    node.update(
        filename=_safe_filename(file.filename, fallback=f"{role}.bin", maximum=120),
        media_type=media_type or _present(file.media_type),
        size_bytes=actual_size,
        sha256=actual_sha256,
        path=_member_name(role, file.filename),
    )
    return node


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def build_evidence_package(
    evidence: dict[str, Any],
    *,
    source_file: EvidenceFile | None = None,
    output_file: EvidenceFile | None = None,
    generated_at: str | None = None,
    tool_version: str | None = None,
) -> bytes:
    """Build a deterministic ZIP package from bounded review evidence."""
    if not isinstance(evidence, dict):
        raise EvidencePackageError("evidence report must be an object")
    scan = evidence.get("scan")
    if not isinstance(scan, dict):
        raise EvidencePackageError("evidence report scan must be an object")

    source = _evidence_node(
        "source", evidence.get("source"), scan=scan, file=source_file
    )
    output = _evidence_node(
        "output", evidence.get("artifact"), scan=scan, file=output_file
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package": {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            "tool": {
                "name": "aelira-core",
                "version": tool_version or _tool_version(),
            },
        },
        "scan": _bounded_package_value(scan),
        "source": source,
        "output": output,
        "evidence": {
            "summary": _bounded_package_value(evidence.get("summary", {})),
            "machine_observations": _bounded_package_value(
                evidence.get("machine_observations", [])
            ),
            "reviewer_decisions": _bounded_package_value(
                evidence.get("reviewer_decisions", [])
            ),
            "audit_trail": _bounded_package_value(evidence.get("audit_trail", [])),
            "validator_results": _bounded_package_value(
                evidence.get("validator_observations", [])
            ),
            "limitations": _bounded_package_value(evidence.get("limitations")),
        },
    }
    members: list[tuple[str, bytes]] = [(MANIFEST_PATH, _canonical_json(manifest))]
    if source_file is not None:
        members.append((source["path"], source_file.content))
    if output_file is not None:
        members.append((output["path"], output_file.content))
    if sum(len(content) for _, content in members) > _MAX_ARCHIVE_BYTES:
        raise EvidencePackageError("evidence package content is too large")

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        for name, content in members:
            archive.writestr(_zip_info(name), content)
    return package.getvalue()


def _safe_member_name(name: str) -> bool:
    if not name or len(name) > _MAX_MEMBER_NAME or "\\" in name:
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and ":" not in path.parts[0]
    )


def _manifest_error(message: str) -> EvidencePackageError:
    return EvidencePackageError(f"malformed manifest: {message}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _required_object(manifest: dict[str, Any], field: str) -> dict[str, Any]:
    value = manifest.get(field)
    if not isinstance(value, dict):
        raise _manifest_error(f"{field} must be an object")
    return value


def _validate_manifest_contract(manifest: dict[str, Any]) -> None:
    package = _required_object(manifest, "package")
    if not isinstance(package.get("generated_at"), str) or not package["generated_at"]:
        raise _manifest_error("package.generated_at is required")
    tool = package.get("tool")
    if not isinstance(tool, dict) or any(
        not isinstance(tool.get(field), str) or not tool[field]
        for field in ("name", "version")
    ):
        raise _manifest_error("package.tool is invalid")
    scan = _required_object(manifest, "scan")
    if not isinstance(scan.get("id"), str) or not scan["id"]:
        raise _manifest_error("scan.id is required")
    evidence = _required_object(manifest, "evidence")
    if not isinstance(evidence.get("summary"), dict):
        raise _manifest_error("evidence.summary must be an object")
    if evidence.get("limitations") is not None and not isinstance(
        evidence["limitations"], str
    ):
        raise _manifest_error("evidence.limitations is invalid")
    for field in (
        "machine_observations",
        "reviewer_decisions",
        "audit_trail",
        "validator_results",
    ):
        if not isinstance(evidence.get(field), list):
            raise _manifest_error(f"evidence.{field} must be an array")


def _validate_evidence_node(role: str, node: dict[str, Any]) -> None:
    availability = node.get("availability")
    if not isinstance(availability, str) or not availability:
        raise _manifest_error(f"{role}.availability is required")
    for field in (
        "identity",
        "source",
        "filename",
        "media_type",
        "review_status",
        "approval_review_digest",
    ):
        if node.get(field) is not None and not isinstance(node[field], str):
            raise _manifest_error(f"{role}.{field} is invalid")
    filename = node.get("filename")
    if isinstance(filename, str) and (
        len(filename) > 120 or "/" in filename or "\\" in filename
    ):
        raise _manifest_error(f"{role}.filename is invalid")
    size_bytes = node.get("size_bytes")
    if size_bytes is not None and (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 0
    ):
        raise _manifest_error(f"{role}.size_bytes is invalid")
    sha256 = node.get("sha256")
    if sha256 is not None and (
        not isinstance(sha256, str) or not _SHA256.fullmatch(sha256)
    ):
        raise _manifest_error(f"{role}.sha256 is invalid")
    timestamps = node.get("timestamps")
    if not isinstance(timestamps, dict):
        raise _manifest_error(f"{role}.timestamps must be an object")
    if any(
        value is not None and not isinstance(value, str)
        for value in timestamps.values()
    ):
        raise _manifest_error(f"{role}.timestamps is invalid")


def verify_evidence_package(package: bytes) -> dict[str, Any]:
    """Verify an evidence package offline and return its trusted manifest."""
    if not isinstance(package, bytes) or len(package) > _MAX_ARCHIVE_BYTES:
        raise EvidencePackageError("malformed evidence package")
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(members) > _MAX_MEMBERS:
                raise EvidencePackageError(
                    "malformed evidence package: too many members"
                )
            if sum(member.file_size for member in members) > _MAX_ARCHIVE_BYTES:
                raise EvidencePackageError(
                    "malformed evidence package: content too large"
                )
            if len(set(names)) != len(names):
                raise EvidencePackageError("duplicate archive member")
            if any(not _safe_member_name(name) for name in names):
                raise EvidencePackageError("unsafe archive member")
            if MANIFEST_PATH not in names:
                raise _manifest_error("manifest.json is missing")
            manifest_info = archive.getinfo(MANIFEST_PATH)
            if manifest_info.file_size > _MAX_MANIFEST_BYTES:
                raise _manifest_error("manifest is too large")
            try:
                manifest = json.loads(
                    archive.read(MANIFEST_PATH),
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise _manifest_error("invalid JSON") from exc
            if not isinstance(manifest, dict):
                raise _manifest_error("root must be an object")

            schema_version = manifest.get("schema_version")
            match = (
                _SEMVER.fullmatch(schema_version)
                if isinstance(schema_version, str)
                else None
            )
            if match is None:
                raise _manifest_error("schema_version must be semantic version")
            if int(match.group(1)) != SUPPORTED_SCHEMA_MAJOR:
                raise EvidencePackageError("unsupported schema major")
            _validate_manifest_contract(manifest)

            referenced = {MANIFEST_PATH}
            for role in ("source", "output"):
                node = _required_object(manifest, role)
                _validate_evidence_node(role, node)
                included = node.get("included")
                if not isinstance(included, bool):
                    raise _manifest_error(f"{role}.included must be boolean")
                path = node.get("path")
                if not included:
                    if path is not None:
                        raise _manifest_error(
                            f"{role}.path must be null when bytes are excluded"
                        )
                    continue
                if (
                    not isinstance(path, str)
                    or not path.startswith(f"files/{role}-")
                    or not _safe_member_name(path)
                ):
                    raise _manifest_error(f"{role}.path is invalid")
                referenced.add(path)
                if path not in names:
                    raise EvidencePackageError(f"missing included file: {role}")
                size_bytes = node.get("size_bytes")
                if (
                    not isinstance(size_bytes, int)
                    or isinstance(size_bytes, bool)
                    or size_bytes < 0
                ):
                    raise _manifest_error(f"{role}.size_bytes is invalid")
                sha256 = node.get("sha256")
                if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
                    raise _manifest_error(f"{role}.sha256 is invalid")
                content = archive.read(path)
                if len(content) != size_bytes:
                    raise EvidencePackageError(f"included file size mismatch: {role}")
                if hashlib.sha256(content).hexdigest() != sha256:
                    raise EvidencePackageError(
                        f"included file checksum mismatch: {role}"
                    )
            if set(names) != referenced:
                raise EvidencePackageError("unexpected archive member")
            return manifest
    except EvidencePackageError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise EvidencePackageError("malformed evidence package") from exc


def main(argv: list[str] | None = None) -> int:
    """Verify one package from the command line without extracting its members."""
    parser = ArgumentParser(description="Verify an Aelira evidence package")
    parser.add_argument("package", help="path to the evidence-package ZIP")
    args = parser.parse_args(argv)
    try:
        with open(args.package, "rb") as package_file:
            manifest = verify_evidence_package(
                package_file.read(_MAX_ARCHIVE_BYTES + 1)
            )
    except (OSError, EvidencePackageError) as exc:
        print(f"invalid evidence package: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "valid": True,
                "schema_version": manifest["schema_version"],
                "scan_id": manifest.get("scan", {}).get("id"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
