"""
Public Demo API Endpoints

These endpoints allow unauthenticated users to try the scanning features
with strict rate limiting and security validation.

Features:
- IP-based rate limiting (3 scans total per user, not daily)
- Magic bytes file validation
- Document security scanning
- Processing queue throttling
- No authentication required
"""

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    BackgroundTasks,
    Request,
    Depends,
    Query,
)
from pydantic import BaseModel
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import asyncio
import tempfile
import os
import logging
import re
import shutil
import uuid

from fastapi.responses import FileResponse

# Document processors
from ..education.pdf_processor import PDFProcessor
from ..education.pptx_processor import PowerPointProcessor
from ..education.docx_processor import DocxProcessor
from ..education.xlsx_processor import XlsxProcessor

# LaTeX processor
from ..education.latex_processor import LaTeXProcessor

# Multimedia processor
from ..education.multimedia_processor import MultimediaProcessor

# Code scanner
from ..education.code_scanner import CodeScanner

# Web scanner
from ..education.web_scanner import WebScanner

# Note: ImageAltTextGenerator is used internally by document processors, not for standalone demo scans
# Remediation
from ..remediation.auto_remediator import AutoRemediator
from ..db.database import get_db_dependency
from ..db.models import Scan, ScanType, ScanStatus, ScanResult, SecurityScanResult
from ..security.document_validator import (
    validate_document,
    ThreatLevel,
)
from ..auth.redis_rate_limiter import get_redis_client

# Temp storage for demo remediated files (keyed by scan_id)
# Format: {scan_id: {"path": str, "created_at": datetime}}
_demo_remediated_files: Dict[str, Dict[str, Any]] = {}

# Remediated file expiry (1 hour - enough time to download)
REMEDIATED_FILE_EXPIRY_SECONDS = 3600

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["Demo (Public)"])

# Anything outside this set is replaced in an uploaded filename. The name is
# stored on the scan, echoed back in the report, and used as the temp file's
# name, so it has to be safe to join onto a path.
_UPLOAD_NAME_SAFE = re.compile(r"[^A-Za-z0-9._ ()-]")
_MAX_UPLOAD_NAME_LEN = 200


def _redis_int(value: Any) -> int:
    """Read a redis counter as an int, treating anything unusable as zero.

    The shared client is typed as possibly-async, so its return is a union that
    int() will not accept directly. A missing key, or a value some other writer
    left in a shape we cannot parse, means "no scans recorded".
    """
    if value is None:
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _safe_upload_name(raw: Optional[str]) -> str:
    """Reduce a client-supplied filename to a harmless basename.

    Strips any directory component, so a crafted "../../etc/passwd" cannot
    escape the temp directory the upload is written into, and drops leading
    dots so the result can never be a dotfile or a bare "..".
    """
    name = os.path.basename((raw or "").replace("\\", "/").strip())
    name = _UPLOAD_NAME_SAFE.sub("_", name).lstrip(". ")
    name = name[:_MAX_UPLOAD_NAME_LEN].strip()
    return name or "unknown"


# =============================================================================
# Demo Rate Limiting Configuration
# =============================================================================

DEMO_LIMITS = {
    "max_scans_total": 3,  # Max scans per device (secondary limit)
    # Primary limit. The device fingerprint is derived from a random UUID the
    # browser mints into localStorage, so clearing site data produces a brand
    # new device and a brand new allowance — it cannot be the primary control.
    # The IP counter is the one an ordinary user cannot reset.
    #
    # Set well above max_scans_total because the target market sits behind
    # institutional NAT: a whole university shares one address, and a ceiling
    # of 3 would lock out everyone after the first keen evaluator. Tune with
    # DEMO_MAX_SCANS_PER_IP without a redeploy.
    "max_scans_per_ip": int(os.getenv("DEMO_MAX_SCANS_PER_IP", "15")),
    "max_fingerprints_per_ip": 50,  # Distinct devices tracked per IP (telemetry)
    "max_file_size_mb": 50,  # Default max file size in MB (fallback)
    "max_pages": 10,  # Max pages per document
    "max_concurrent_processing": 10,  # Global concurrent processing limit
    "rate_limit_ttl_days": 365,  # How long to remember demo users (1 year)
}

# IPs that bypass rate limiting (for testing/development)
# Set DEMO_WHITELIST_IPS env var as comma-separated IPs
DEMO_WHITELIST_IPS = set(
    ip.strip() for ip in os.getenv("DEMO_WHITELIST_IPS", "").split(",") if ip.strip()
)

# File type-specific size limits (in MB)
# Different file types have different reasonable sizes
FILE_SIZE_LIMITS_MB = {
    # Documents - can be large with embedded images
    "pdf": 50,
    "docx": 25,
    "pptx": 50,  # Presentations often have many images
    "xlsx": 25,
    # LaTeX - mostly text
    "tex": 5,
    # Code files - should be small
    "html": 2,
    "css": 1,
    "js": 2,
    "zip": 10,  # Code archive
    # Multimedia - can be large
    "mp4": 100,
    "webm": 100,
    "mov": 100,
    "mp3": 25,
    "wav": 50,
    "m4a": 25,
}

# In-memory fallback for rate limiting (when Redis is unavailable)
_demo_rate_limits: Dict[str, Dict[str, Any]] = {}

# Global processing queue counter (thread-safe)
import threading

_processing_count = 0
_processing_lock = threading.Lock()
_MAX_PROCESSING = DEMO_LIMITS["max_concurrent_processing"]


# =============================================================================
# Device Fingerprint + IP Rate Limiter
# =============================================================================


class DemoRateLimiter:
    """
    Rate limiter for demo endpoints using device fingerprint + IP fallback.

    Fingerprint-based identification allows multiple users on the same network
    (like university campuses) to each have their own demo quota.
    """

    @staticmethod
    def get_client_ip(request: Request) -> str:
        """Extract client IP from request, handling proxies.

        Only trusts proxy headers (X-Forwarded-For, X-Real-IP) when
        TRUST_PROXY=true, since these headers can be spoofed by clients
        to bypass rate limiting.
        """
        import ipaddress as _ipaddress

        trust_proxy = os.getenv("TRUST_PROXY", "false").lower() == "true"

        if trust_proxy:
            # Check X-Forwarded-For header (set by Traefik/Nginx)
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                first_ip = forwarded.split(",")[0].strip()
                # Validate it's a real IP address
                try:
                    _ipaddress.ip_address(first_ip)
                    return first_ip
                except ValueError:
                    pass

            # Check X-Real-IP header
            real_ip = request.headers.get("x-real-ip")
            if real_ip:
                try:
                    _ipaddress.ip_address(real_ip.strip())
                    return real_ip.strip()
                except ValueError:
                    pass

        # Fallback to direct client IP
        if request.client:
            return request.client.host

        return "unknown"

    @staticmethod
    def get_fingerprint(request: Request) -> Tuple[Optional[str], str]:
        """
        Extract device fingerprint from request headers.

        Returns:
            Tuple of (fingerprint_hash, quality_level)
            fingerprint_hash is None if not provided or invalid
        """
        fingerprint = request.headers.get("x-device-fingerprint")
        quality = request.headers.get("x-fingerprint-quality", "none")

        # Validate fingerprint format (should be 64-char hex SHA-256)
        if fingerprint and len(fingerprint) == 64:
            try:
                int(fingerprint, 16)  # Verify it's valid hex
                return fingerprint, quality
            except ValueError:
                pass

        return None, "none"

    @staticmethod
    def _unlimited_headers() -> dict:
        return {
            "X-RateLimit-Limit": "unlimited",
            "X-RateLimit-Remaining": "unlimited",
            "X-RateLimit-Method": "whitelist",
        }

    @staticmethod
    def _headers(remaining: int, method: str) -> dict:
        return {
            "X-RateLimit-Limit": str(DEMO_LIMITS["max_scans_total"]),
            "X-RateLimit-Remaining": str(max(0, remaining)),
            "X-RateLimit-Method": method,
        }

    @staticmethod
    def check_rate_limit(
        client_ip: str,
        fingerprint: Optional[str] = None,
        fingerprint_quality: str = "none",
    ) -> Tuple[bool, dict]:
        """Report whether another scan is allowed, without consuming one.

        Read-only by design. Consumption is a separate step (`consume_scan`) so
        that an upload rejected by validation does not cost the caller a scan,
        and so the quota can be displayed without spending it.

        Two counters must both have room:

        - IP (primary). The one a user cannot reset. Ceiling is
          `max_scans_per_ip`, set high enough that a shared institutional
          address is not exhausted by a single evaluator.
        - Device fingerprint (secondary, when quality is full/partial). Keeps
          one person from consuming the whole IP allowance. It is derived from
          a random localStorage UUID, so it is a courtesy limit, not a control.

        Returns:
            Tuple of (allowed: bool, headers: dict). Headers carry the smaller
            of the two remaining counts — the number the caller can actually use.
        """
        if client_ip in DEMO_WHITELIST_IPS:
            logger.debug(f"Demo rate limit bypassed for whitelisted IP: {client_ip}")
            return True, DemoRateLimiter._unlimited_headers()

        ip_count, fp_count = DemoRateLimiter._read_counts(
            client_ip, fingerprint, fingerprint_quality
        )

        ip_limit = DEMO_LIMITS["max_scans_per_ip"]
        scan_limit = DEMO_LIMITS["max_scans_total"]

        ip_remaining = ip_limit - ip_count
        if ip_remaining <= 0:
            logger.info(f"Demo limit reached on IP: ip={client_ip}, count={ip_count}")
            return False, DemoRateLimiter._headers(0, "ip")

        if fp_count is None:
            return True, DemoRateLimiter._headers(ip_remaining, "ip")

        fp_remaining = scan_limit - fp_count
        if fp_remaining <= 0:
            return False, DemoRateLimiter._headers(0, "fingerprint")

        return True, DemoRateLimiter._headers(
            min(ip_remaining, fp_remaining),
            "fingerprint" if fp_remaining <= ip_remaining else "ip",
        )

    @staticmethod
    def consume_scan(
        client_ip: str,
        fingerprint: Optional[str] = None,
        fingerprint_quality: str = "none",
    ) -> dict:
        """Charge one scan against both counters. Call only once work is committed.

        Returns the headers describing what is left afterwards.
        """
        if client_ip in DEMO_WHITELIST_IPS:
            return DemoRateLimiter._unlimited_headers()

        redis_client = get_redis_client()
        use_fingerprint = DemoRateLimiter._use_fingerprint(
            fingerprint, fingerprint_quality
        )

        if redis_client is not None:
            try:
                return DemoRateLimiter._consume_redis(
                    redis_client, client_ip, fingerprint, use_fingerprint
                )
            except Exception as e:
                logger.error(f"Redis error consuming demo scan: {e}, using memory")

        return DemoRateLimiter._consume_memory(client_ip, fingerprint, use_fingerprint)

    # -- shared helpers -----------------------------------------------------

    @staticmethod
    def _use_fingerprint(fingerprint: Optional[str], quality: str) -> bool:
        return bool(fingerprint) and quality in ("full", "partial")

    @staticmethod
    def _read_counts(
        client_ip: str, fingerprint: Optional[str], quality: str
    ) -> Tuple[int, Optional[int]]:
        """Return (ip_count, fp_count). fp_count is None when not tracked."""
        use_fingerprint = DemoRateLimiter._use_fingerprint(fingerprint, quality)
        redis_client = get_redis_client()

        if redis_client is not None:
            try:
                ip_count = _redis_int(redis_client.get(f"demo_scans_ip:{client_ip}"))
                fp_count = None
                if use_fingerprint:
                    fp_count = _redis_int(
                        redis_client.get(f"demo_scans_fp:{fingerprint}")
                    )
                return ip_count, fp_count
            except Exception as e:
                logger.error(f"Redis error reading demo counts: {e}, using memory")

        current_time = datetime.utcnow()

        def _memory_count(key: str) -> int:
            entry = _demo_rate_limits.get(key)
            if not entry or current_time >= entry["expires_at"]:
                return 0
            return entry["count"]

        ip_count = _memory_count(f"ip:{client_ip}")
        fp_count = _memory_count(f"fp:{fingerprint}") if use_fingerprint else None
        return ip_count, fp_count

    # -- redis --------------------------------------------------------------

    @staticmethod
    def _consume_redis(
        redis_client, client_ip: str, fingerprint: Optional[str], use_fingerprint: bool
    ) -> dict:
        ttl_seconds = DEMO_LIMITS["rate_limit_ttl_days"] * 86400
        ip_key = f"demo_scans_ip:{client_ip}"

        # INCR is atomic, so two concurrent uploads cannot both read the same
        # count and write the same successor.
        ip_count = redis_client.incr(ip_key)
        if ip_count == 1:
            redis_client.expire(ip_key, ttl_seconds)

        ip_remaining = DEMO_LIMITS["max_scans_per_ip"] - ip_count

        if not use_fingerprint:
            return DemoRateLimiter._headers(ip_remaining, "ip")

        fp_key = f"demo_scans_fp:{fingerprint}"
        fp_count = redis_client.incr(fp_key)
        if fp_count == 1:
            redis_client.expire(fp_key, ttl_seconds)

        # Device pool is kept for observability — how many distinct devices an
        # address has presented. The IP counter above is what enforces.
        pool_key = f"demo_ip_fingerprints:{client_ip}"
        if redis_client.scard(pool_key) < DEMO_LIMITS["max_fingerprints_per_ip"]:
            redis_client.sadd(pool_key, fingerprint)
            redis_client.expire(pool_key, ttl_seconds)

        fp_remaining = DEMO_LIMITS["max_scans_total"] - fp_count
        logger.debug(
            f"Demo scan consumed: ip={client_ip} ({ip_count}/"
            f"{DEMO_LIMITS['max_scans_per_ip']}), fp={fingerprint[:8]} "
            f"({fp_count}/{DEMO_LIMITS['max_scans_total']})"
        )
        return DemoRateLimiter._headers(
            min(ip_remaining, fp_remaining),
            "fingerprint" if fp_remaining <= ip_remaining else "ip",
        )

    # -- in-memory fallback -------------------------------------------------

    @staticmethod
    def _consume_memory(
        client_ip: str, fingerprint: Optional[str], use_fingerprint: bool
    ) -> dict:
        current_time = datetime.utcnow()
        expires_at = current_time + timedelta(days=DEMO_LIMITS["rate_limit_ttl_days"])

        def _bump(key: str) -> int:
            entry = _demo_rate_limits.get(key)
            count = (
                entry["count"] if entry and current_time < entry["expires_at"] else 0
            ) + 1
            _demo_rate_limits[key] = {"count": count, "expires_at": expires_at}
            return count

        ip_count = _bump(f"ip:{client_ip}")
        ip_remaining = DEMO_LIMITS["max_scans_per_ip"] - ip_count

        if not use_fingerprint:
            DemoRateLimiter._cleanup_old_entries()
            return DemoRateLimiter._headers(ip_remaining, "ip")

        fp_count = _bump(f"fp:{fingerprint}")
        fp_remaining = DEMO_LIMITS["max_scans_total"] - fp_count

        DemoRateLimiter._cleanup_old_entries()
        return DemoRateLimiter._headers(
            min(ip_remaining, fp_remaining),
            "fingerprint" if fp_remaining <= ip_remaining else "ip",
        )

    @staticmethod
    def _cleanup_old_entries():
        """Remove expired entries from in-memory storage."""
        global _demo_rate_limits
        current_time = datetime.utcnow()

        keys_to_delete = [
            k for k, v in _demo_rate_limits.items() if v["expires_at"] < current_time
        ]

        for key in keys_to_delete:
            del _demo_rate_limits[key]


# =============================================================================
# Processing Queue Throttling
# =============================================================================


def check_and_increment_processing() -> bool:
    """Atomically check capacity and increment. Returns True if slot acquired."""
    global _processing_count
    with _processing_lock:
        if _processing_count < _MAX_PROCESSING:
            _processing_count += 1
            return True
        return False


def decrement_processing():
    """Atomically decrement the processing counter."""
    global _processing_count
    with _processing_lock:
        _processing_count = max(0, _processing_count - 1)


def cleanup_expired_remediated_files():
    """
    Clean up expired remediated files from disk and memory.

    This should be called periodically to prevent disk/memory exhaustion.
    Files expire after REMEDIATED_FILE_EXPIRY_SECONDS (default 1 hour).
    """
    global _demo_remediated_files
    current_time = datetime.utcnow()
    expired_keys = []

    for scan_id, file_info in _demo_remediated_files.items():
        created_at = file_info.get("created_at")
        if (
            created_at
            and (current_time - created_at).total_seconds()
            > REMEDIATED_FILE_EXPIRY_SECONDS
        ):
            expired_keys.append(scan_id)
            # Delete the file from disk
            file_path = file_info.get("path")
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up expired remediated file: {file_path}")
                except Exception as e:
                    logger.warning(
                        f"Failed to delete expired remediated file {file_path}: {e}"
                    )

    # Remove from tracking dict
    for key in expired_keys:
        del _demo_remediated_files[key]

    if expired_keys:
        logger.info(f"Cleaned up {len(expired_keys)} expired remediated files")


# =============================================================================
# Request/Response Models
# =============================================================================


class DemoScanResponse(BaseModel):
    scan_id: str
    status: str
    message: str
    rate_limit_remaining: int


class DemoScanResultResponse(BaseModel):
    scan_id: str
    status: str
    file_name: str
    file_type: str
    compliance_score: Optional[int] = None
    issues: Optional[list] = None
    processing_time_seconds: Optional[float] = None
    progress: int = 0
    progress_message: str = ""
    error: Optional[str] = None
    # Remediation results
    remediation_available: bool = False
    remediated_score: Optional[int] = None
    fixed_count: int = 0
    manual_count: int = 0
    fixed_issues: Optional[list] = None
    manual_issues: Optional[list] = None


class DemoQuotaResponse(BaseModel):
    """The caller's demo allowance, as the server sees it."""

    scans_used: int
    scans_remaining: int
    max_scans: int
    unlimited: bool = False
    exhausted: bool = False


# =============================================================================
# Demo Endpoints
# =============================================================================


@router.get("/quota", response_model=DemoQuotaResponse)
async def get_demo_quota(request: Request):
    """Report how many demo scans the caller has left.

    The UI used to keep its own count in localStorage under a 24-hour expiry,
    while the server remembered for a year. Once the local copy expired the
    page cheerfully offered scans that the server then refused. This endpoint
    is the single source of truth; it reads without consuming.
    """
    client_ip = DemoRateLimiter.get_client_ip(request)
    fingerprint, fp_quality = DemoRateLimiter.get_fingerprint(request)

    allowed, headers = DemoRateLimiter.check_rate_limit(
        client_ip, fingerprint, fp_quality
    )

    if headers.get("X-RateLimit-Remaining") == "unlimited":
        return DemoQuotaResponse(
            scans_used=0,
            scans_remaining=-1,
            max_scans=DEMO_LIMITS["max_scans_total"],
            unlimited=True,
        )

    max_scans = DEMO_LIMITS["max_scans_total"]
    remaining = int(headers.get("X-RateLimit-Remaining", "0"))

    # Remaining can be limited by the IP counter rather than this device, so
    # derive "used" from it instead of reading a second counter — otherwise the
    # two numbers can disagree on screen.
    return DemoQuotaResponse(
        scans_used=max(0, min(max_scans, max_scans - remaining)),
        scans_remaining=remaining,
        max_scans=max_scans,
        exhausted=not allowed,
    )


@router.post("/scan", response_model=DemoScanResponse)
async def demo_scan_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_dependency),
):
    """
    Scan a document for accessibility issues (public demo endpoint).

    This endpoint allows unauthenticated users to try the scanning feature
    with strict rate limiting:
    - 3 scans total per identified user (does not reset daily)
    - Max file size: 50MB
    - Max pages: 10
    - Processing queue throttling

    Supported file types: PDF, DOCX, PPTX, XLSX
    """
    # 0. Opportunistic cleanup of expired remediated files
    cleanup_expired_remediated_files()

    # 1. Get client identifiers (IP + fingerprint)
    client_ip = DemoRateLimiter.get_client_ip(request)
    fingerprint, fp_quality = DemoRateLimiter.get_fingerprint(request)

    # 2. Check the rate limit. This only reads — the scan is charged further
    # down, once validation has passed, so a rejected upload costs nothing.
    allowed, rate_headers = DemoRateLimiter.check_rate_limit(
        client_ip, fingerprint, fp_quality
    )

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Demo limit reached. The demo allows {DEMO_LIMITS['max_scans_total']} free scans. Sign up for a free account to continue scanning.",
                "upgrade_url": "https://dashboard.example.com/signup",
            },
            headers=rate_headers,
        )

    # 3. Check processing capacity (atomically reserves a slot)
    if not check_and_increment_processing():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "processing_queue_full",
                "message": "Our servers are busy. Please try again in a few moments.",
            },
        )

    # 4. Get file extension first (needed for type-specific size limits)
    filename = _safe_upload_name(file.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # 5. Validate file size (using type-specific limits)
    content = await file.read()
    await file.seek(0)

    # Get size limit for this file type (fallback to default)
    max_size_mb = FILE_SIZE_LIMITS_MB.get(extension, DEMO_LIMITS["max_file_size_mb"])
    max_size = max_size_mb * 1024 * 1024
    if len(content) > max_size:
        decrement_processing()  # Release slot reserved in step 3
        raise HTTPException(
            status_code=400,
            detail={
                "error": "file_too_large",
                "message": f"File size exceeds {max_size_mb}MB limit for {extension.upper()} files.",
                "max_size_mb": max_size_mb,
            },
        )

    # 6. Validate file type and magic bytes

    # All supported file types
    # Note: Images are NOT supported as standalone uploads - they are scanned within documents
    # ZIP files are security-validated for zip bombs, path traversal attacks, etc.
    SUPPORTED_EXTENSIONS = {
        # Documents
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        # LaTeX
        "tex",
        # Multimedia (video/audio)
        "mp4",
        "webm",
        "mov",
        "mp3",
        "wav",
        "m4a",
        # Code (including ZIP for project bundles)
        "html",
        "css",
        "js",
        "zip",
    }

    if extension not in SUPPORTED_EXTENSIONS:
        decrement_processing()  # Release slot reserved in step 3
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_file_type",
                "message": "Please upload a supported file type: documents (PDF, Word, PowerPoint, Excel), LaTeX (.tex), multimedia (MP4, MP3, WAV), or code (HTML, CSS, JS, ZIP).",
                "supported_types": list(SUPPORTED_EXTENSIONS),
            },
        )

    # 6. Security validation (magic bytes, threats)
    try:
        validation_result = await validate_document(filename, content)

        # Generate scan ID
        scan_id = str(uuid.uuid4())

        # Log security scan result
        security_result = SecurityScanResult(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            department_id="demo",
            filename=filename,
            file_hash=validation_result.file_hash,
            file_type=validation_result.file_type,
            file_size=len(content),
            is_safe=validation_result.is_safe,
            threat_level=validation_result.threat_level.value,
            findings=(
                [
                    {
                        "category": f.category,
                        "description": f.description,
                        "threat_level": f.threat_level.value,
                        "details": f.details,
                    }
                    for f in validation_result.findings
                ]
                if validation_result.findings
                else None
            ),
            was_sanitized=False,
            was_blocked=not validation_result.is_safe,
            blocked_reason=(
                validation_result.findings[0].description
                if validation_result.findings and not validation_result.is_safe
                else None
            ),
        )
        db.add(security_result)

        # Block critical/high threats
        if validation_result.threat_level in [ThreatLevel.CRITICAL, ThreatLevel.HIGH]:
            db.commit()
            logger.warning(
                f"Demo security threat blocked from {client_ip}: {filename} - {validation_result.threat_level.value}"
            )
            decrement_processing()  # Release slot reserved in step 3
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "security_threat_detected",
                    "message": "This file contains potential security threats and cannot be processed.",
                    "threat_level": validation_result.threat_level.value,
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        decrement_processing()  # Release slot reserved in step 3
        logger.error(f"Security validation error: {e}")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "Failed to validate file. Please ensure it's a valid document.",
            },
        )

    # 7. Map extension to scan type
    scan_type_map = {
        # Documents
        "pdf": ScanType.PDF,
        "docx": ScanType.WORD,
        "pptx": ScanType.POWERPOINT,
        "xlsx": ScanType.EXCEL,
        # LaTeX
        "tex": ScanType.LATEX,
        # Multimedia (both video and audio use VIDEO scan type)
        "mp4": ScanType.VIDEO,
        "webm": ScanType.VIDEO,
        "mov": ScanType.VIDEO,
        "mp3": ScanType.VIDEO,
        "wav": ScanType.VIDEO,
        "m4a": ScanType.VIDEO,
        # Code (including ZIP project bundles)
        "html": ScanType.CODE,
        "css": ScanType.CODE,
        "js": ScanType.CODE,
        "zip": ScanType.CODE,
    }
    scan_type = scan_type_map.get(extension, ScanType.PDF)

    # 8. Create scan record (use fixed demo user to avoid foreign key issues)
    scan = Scan(
        id=scan_id,
        department_id="demo",
        user_id="demo-user",  # Fixed demo user ID (created in database)
        scan_type=scan_type,
        status=ScanStatus.PENDING,
        file_name=filename,
        file_size_bytes=len(content),
        progress=0,
        progress_message="Queued for processing...",
    )
    db.add(scan)
    db.commit()

    # 9. Save file temporarily and start processing (slot already reserved in step 3)
    tmp_path = None
    try:
        # Keep the uploaded name on the temp copy. Remediation falls back to the
        # file stem when a document has no title and no readable first-page text
        # — true of any screenshot — so a NamedTemporaryFile name would ship a
        # PDF titled "tmpj1hjj0ch" to the user. The random directory keeps
        # concurrent scans of the same filename apart.
        tmp_dir = tempfile.mkdtemp(prefix="aelira-demo-")
        tmp_path = os.path.join(tmp_dir, filename)
        with open(tmp_path, "wb") as tmp:
            tmp.write(content)

        # Queue background processing
        background_tasks.add_task(
            process_demo_scan,
            scan_id=scan_id,
            file_path=tmp_path,
            extension=extension,
            db_session_maker=get_db_dependency,
        )

        # Charge the scan only now. Everything above can reject the upload —
        # size, magic bytes, page count, threat scanning — and none of those
        # should cost the caller one of their three.
        rate_headers = DemoRateLimiter.consume_scan(client_ip, fingerprint, fp_quality)

        remaining_str = rate_headers.get("X-RateLimit-Remaining", "0")
        if remaining_str == "unlimited":
            remaining = -1  # -1 indicates unlimited
            message = "Scan started. Unlimited demo scans (whitelisted)."
        else:
            remaining = int(remaining_str)
            message = f"Scan started. {remaining} demo scan{'s' if remaining != 1 else ''} remaining."

        return DemoScanResponse(
            scan_id=scan_id,
            status="processing",
            message=message,
            rate_limit_remaining=remaining,
        )

    except Exception as e:
        decrement_processing()
        # Clean up temp file if it was created
        if tmp_path:
            shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
        logger.error(f"Failed to start demo scan: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_failed",
                "message": "Failed to start document scan. Please try again.",
            },
        )


@router.get("/scan/{scan_id}", response_model=DemoScanResultResponse)
async def get_demo_scan_result(
    scan_id: str,
    db: Session = Depends(get_db_dependency),
):
    """
    Get the status and results of a demo scan.

    Returns progress updates while processing, and full results when complete.
    """
    # Find scan
    scan = (
        db.query(Scan)
        .filter(
            Scan.id == scan_id,
            Scan.department_id == "demo",
        )
        .first()
    )

    if not scan:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "scan_not_found",
                "message": "Scan not found or expired.",
            },
        )

    # Build response based on status
    # Note: Use lowercase status/file_type values for frontend compatibility
    response = DemoScanResultResponse(
        scan_id=scan_id,
        status=scan.status.value.lower() if scan.status else "unknown",
        file_name=scan.file_name or "unknown",
        file_type=scan.scan_type.value.lower() if scan.scan_type else "unknown",
        progress=scan.progress or 0,
        progress_message=scan.progress_message or "",
    )

    if scan.status == ScanStatus.COMPLETED:
        # Get full results
        result = db.query(ScanResult).filter(ScanResult.scan_id == scan_id).first()

        if result:
            response.compliance_score = result.compliance_score
            response.issues = result.issues  # JSON list of issues
            # Calculate processing time from scan timestamps
            if scan.completed_at and scan.created_at:
                response.processing_time_seconds = (
                    scan.completed_at - scan.created_at
                ).total_seconds()
            elif scan.processing_time_ms:
                response.processing_time_seconds = scan.processing_time_ms / 1000.0

            # Include remediation data from suggestions field
            remediation_data = result.suggestions or {}
            response.remediation_available = remediation_data.get(
                "remediation_available", False
            )
            response.remediated_score = remediation_data.get("remediated_score")
            response.fixed_count = remediation_data.get("fixed_count", 0)
            response.manual_count = remediation_data.get("manual_count", 0)
            response.fixed_issues = remediation_data.get("fixed_issues", [])
            response.manual_issues = remediation_data.get("manual_issues", [])

    elif scan.status == ScanStatus.FAILED:
        response.error = scan.error_message or "Processing failed"

    return response


@router.get("/scan/{scan_id}/download")
async def download_demo_remediated_file(
    scan_id: str,
    file_type: Optional[str] = Query(
        None,
        description="Specific file type to download. Options: captions_vtt, captions_srt, "
        "audio_descriptions_text, audio_descriptions_audio. If not specified, downloads primary file.",
    ),
    db: Session = Depends(get_db_dependency),
):
    """
    Download accessibility files from a demo scan.

    For multimedia files, multiple accessibility files may be available:
    - captions_vtt: WebVTT captions (for deaf users)
    - captions_srt: SRT captions (for deaf users)
    - audio_descriptions_text: Text descriptions of visual content
    - audio_descriptions_audio: Spoken audio descriptions MP3 (for blind users)

    Use the file_type query parameter to download a specific file,
    or omit it to download the primary/best file.
    """
    # Verify the scan exists and is a demo scan
    scan = (
        db.query(Scan)
        .filter(
            Scan.id == scan_id,
            Scan.department_id == "demo",
        )
        .first()
    )

    if not scan:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "scan_not_found",
                "message": "Scan not found.",
            },
        )

    # Check if remediated files exist
    file_info = _demo_remediated_files.get(scan_id)
    if not file_info:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "remediated_file_not_found",
                "message": "Remediated file not available. The file may have expired.",
            },
        )

    # Get all available files
    all_files = file_info.get("all_files", {})
    primary_path = file_info.get("path")

    # Determine which file to download
    if file_type:
        # User requested specific file type
        if file_type not in all_files:
            available = list(all_files.keys())
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_file_type",
                    "message": f"File type '{file_type}' not available for this scan.",
                    "available_types": available,
                },
            )
        remediated_path = all_files[file_type]
    else:
        # Use primary/default file
        remediated_path = primary_path

    # Validate path is within temp directory (defense-in-depth against path traversal)
    if remediated_path:
        try:
            resolved = os.path.realpath(remediated_path)
            temp_dir = os.path.realpath(tempfile.gettempdir())
            if not resolved.startswith(temp_dir + os.sep) and resolved != temp_dir:
                logger.warning(
                    f"Path traversal attempt in demo download: {remediated_path}"
                )
                remediated_path = None
        except (ValueError, OSError):
            remediated_path = None

    if not remediated_path or not os.path.exists(remediated_path):
        raise HTTPException(
            status_code=404,
            detail={
                "error": "remediated_file_not_found",
                "message": "Remediated file not available. The file may have expired.",
                "available_types": list(all_files.keys()),
            },
        )

    # Determine content type
    extension = os.path.splitext(remediated_path)[1].lower()
    content_type_map = {
        # Documents
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        # LaTeX
        ".tex": "application/x-tex",
        ".html": "text/html",  # LaTeX converts to HTML
        # Multimedia
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".vtt": "text/vtt",  # Caption files
        ".srt": "application/x-subrip",
        # Code
        ".js": "application/javascript",
        ".css": "text/css",
        ".zip": "application/zip",
        # Images
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    content_type = content_type_map.get(extension, "application/octet-stream")

    # Generate download filename
    original_name = scan.file_name or "document"
    base_name = os.path.splitext(original_name)[0]
    download_name = f"{base_name}_accessible{extension}"

    # Sanitize filename for Content-Disposition header (remove non-ASCII chars)
    safe_download_name = download_name.encode("ascii", "ignore").decode("ascii")
    if not safe_download_name:
        safe_download_name = f"document_accessible{extension}"

    return FileResponse(
        path=remediated_path,
        media_type=content_type,
        filename=safe_download_name,
    )


class WebsiteScanRequest(BaseModel):
    url: str


@router.post("/scan/website", response_model=DemoScanResponse)
async def demo_scan_website(
    request: Request,
    body: WebsiteScanRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_dependency),
):
    """
    Scan a website URL for accessibility issues (public demo endpoint).

    This endpoint allows unauthenticated users to try website scanning
    with strict rate limiting:
    - 3 scans total per identified user (does not reset daily)
    - Max 5 pages scanned per website
    """
    # 0. Opportunistic cleanup of expired remediated files
    cleanup_expired_remediated_files()

    # 1. Get client identifiers (IP + fingerprint)
    client_ip = DemoRateLimiter.get_client_ip(request)
    fingerprint, fp_quality = DemoRateLimiter.get_fingerprint(request)

    # 2. Check the rate limit. This only reads — the scan is charged further
    # down, once validation has passed, so a rejected upload costs nothing.
    allowed, rate_headers = DemoRateLimiter.check_rate_limit(
        client_ip, fingerprint, fp_quality
    )

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Demo limit reached. The demo allows {DEMO_LIMITS['max_scans_total']} free scans. Sign up for a free account to continue scanning.",
                "upgrade_url": "https://dashboard.example.com/signup",
            },
            headers=rate_headers,
        )

    # 3. Check processing capacity (atomically reserves a slot)
    if not check_and_increment_processing():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "processing_queue_full",
                "message": "Our servers are busy. Please try again in a few moments.",
            },
        )

    # 4. Validate URL
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    # Basic URL format validation
    import re

    url_pattern = re.compile(
        r"^https?://"  # http:// or https://
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}\.?|"  # domain (TLDs up to 63 chars)
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IP
        r"(?::\d+)?"  # optional port
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )

    if not url_pattern.match(url):
        decrement_processing()  # Release slot reserved in step 3
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_url",
                "message": "Please enter a valid website URL (e.g., example.com or https://example.com)",
            },
        )

    # SSRF protection: block private/reserved IP ranges
    from ..utils.security import validate_url_not_private

    try:
        validate_url_not_private(url)
    except ValueError:
        decrement_processing()  # Release slot reserved in step 3
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_url",
                "message": "Cannot scan private or internal URLs.",
            },
        )

    # 5. Generate scan ID
    scan_id = str(uuid.uuid4())

    # 6. Create scan record
    scan = Scan(
        id=scan_id,
        department_id="demo",
        user_id="demo-user",
        scan_type=ScanType.WEBSITE,
        status=ScanStatus.PENDING,
        file_name=url,  # Store URL as filename
        file_size_bytes=0,
        progress=0,
        progress_message="Queued for scanning...",
    )
    db.add(scan)
    db.commit()

    # 7. Start background processing (slot already reserved in step 3)
    try:
        background_tasks.add_task(
            process_demo_website_scan,
            scan_id=scan_id,
            url=url,
            db_session_maker=get_db_dependency,
        )

        # Charge only once the scan is queued — URL and SSRF validation above
        # can reject the request, and those must not cost the caller a scan.
        rate_headers = DemoRateLimiter.consume_scan(client_ip, fingerprint, fp_quality)

        remaining_str = rate_headers.get("X-RateLimit-Remaining", "0")
        if remaining_str == "unlimited":
            remaining = -1  # -1 indicates unlimited
            message = "Website scan started. Unlimited demo scans (whitelisted)."
        else:
            remaining = int(remaining_str)
            message = f"Website scan started. {remaining} demo scan{'s' if remaining != 1 else ''} remaining."

        return DemoScanResponse(
            scan_id=scan_id,
            status="processing",
            message=message,
            rate_limit_remaining=remaining,
        )

    except Exception as e:
        decrement_processing()
        logger.error(f"Failed to start demo website scan: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "processing_failed",
                "message": "Failed to start website scan. Please try again.",
            },
        )


async def process_demo_website_scan(
    scan_id: str,
    url: str,
    db_session_maker,
):
    """Background task to process a demo website scan."""
    from ..db.database import SessionLocal

    db = None
    try:
        db = SessionLocal()

        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            return

        scan.status = ScanStatus.PROCESSING
        scan.progress = 5
        scan.progress_message = "Initializing web scanner..."
        db.commit()

        # Create progress callback to update scan status during Playwright scan
        # IMPORTANT: Use separate DB session for thread safety when called from asyncio.to_thread
        def progress_callback(current: int, total: int, message: str):
            """Update scan progress in database during WebScanner operations"""
            from ..db.database import SessionLocal

            progress_db = None
            try:
                # Create new session for thread safety
                progress_db = SessionLocal()
                progress_scan = (
                    progress_db.query(Scan).filter(Scan.id == scan_id).first()
                )

                if progress_scan:
                    # Calculate progress: 5-85% for scanning, 85-100% for final processing
                    if total > 0:
                        raw_progress = 5 + (current / total) * 80
                        progress_scan.progress = min(int(raw_progress), 85)
                    else:
                        progress_scan.progress = 5
                    progress_scan.progress_message = message
                    progress_db.commit()
                    logger.debug(
                        f"[DEMO SCAN] Progress: {progress_scan.progress}% - {message}"
                    )
            except Exception as e:
                logger.warning(f"[DEMO SCAN] Failed to update progress: {e}")
            finally:
                if progress_db:
                    progress_db.close()

        # Initialize web scanner with demo limits and progress callback
        # Single page only — faster scan, fewer Gemini API calls, AI fixes on the page that matters
        scanner = WebScanner(
            max_pages=1,
            max_depth=0,
            progress_callback=progress_callback,
            scan_images=True,
            validate_alt_text=True,
        )

        scan.progress = 10
        scan.progress_message = "Loading website..."
        db.commit()

        # Run sync Playwright scanner in thread pool to avoid asyncio conflict
        result = await asyncio.to_thread(scanner.scan_website, url)

        scan.progress = 90
        scan.progress_message = "Processing scan results..."
        db.commit()

        # Convert web scan result to standard issues format
        # WebScanResult has pages, each page has issues
        issues = []
        issue_idx = 0
        for page in result.pages:
            for issue in page.issues:
                # Get AI-generated code fix if available
                code_fix = getattr(issue, "generated_code_fix", None)
                # Get element screenshot if available (base64 encoded)
                screenshot = getattr(issue, "screenshot", None)

                issues.append(
                    {
                        "id": f"web-{issue_idx}",
                        "category": _map_web_issue_category(issue.criterion),
                        "severity": _map_web_severity(issue.impact),
                        "title": issue.description,
                        "description": issue.fix or issue.description,
                        "location": issue.page_url or page.url,
                        "wcagCriterion": issue.criterion,
                        "suggestedFix": issue.fix
                        or "",  # Human-readable fix description
                        "codeSnippet": code_fix,  # AI-generated code fix
                        "helpUrl": issue.help_url,  # Deque reference link
                        "screenshot": screenshot,  # Base64-encoded element screenshot
                        "aiGenerated": bool(code_fix),
                    }
                )
                issue_idx += 1

        scan.progress = 95
        scan.progress_message = "Calculating compliance score..."
        db.commit()

        # Calculate compliance score
        compliance_score = (
            result.overall_compliance_score
            if hasattr(result, "overall_compliance_score")
            else _calculate_web_compliance_score(issues)
        )

        # Store results
        scan_result = ScanResult(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            compliance_score=compliance_score,
            wcag_level="AA",
            issues=issues,
            critical_issues=sum(1 for i in issues if i.get("severity") == "critical"),
            high_issues=sum(1 for i in issues if i.get("severity") == "high"),
            medium_issues=sum(1 for i in issues if i.get("severity") == "medium"),
            low_issues=sum(1 for i in issues if i.get("severity") == "low"),
            suggestions={
                "pages_scanned": (
                    result.pages_scanned
                    if hasattr(result, "pages_scanned")
                    else len(result.pages) if hasattr(result, "pages") else 1
                ),
                "remediation_available": False,  # Website remediation not available in demo
            },
        )
        db.add(scan_result)

        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Website scan complete"
        scan.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            f"Demo website scan {scan_id} completed: {len(issues)} issues found"
        )

    except Exception as e:
        logger.error(f"Demo website scan {scan_id} failed: {e}")
        if db:
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan:
                scan.status = ScanStatus.FAILED
                scan.error_message = (
                    "Website scan encountered an error. Please try again."
                )
                scan.progress_message = "Scan failed"
                db.commit()
    finally:
        decrement_processing()
        if db:
            db.close()


def _map_web_issue_category(issue_type: str) -> str:
    """Map axe-core issue types to demo categories."""
    issue_type_lower = issue_type.lower()
    if "alt" in issue_type_lower or "image" in issue_type_lower:
        return "alt_text"
    elif "heading" in issue_type_lower:
        return "heading"
    elif "contrast" in issue_type_lower or "color" in issue_type_lower:
        return "contrast"
    elif "table" in issue_type_lower:
        return "table"
    elif "link" in issue_type_lower or "anchor" in issue_type_lower:
        return "link"
    elif "list" in issue_type_lower:
        return "list"
    elif "lang" in issue_type_lower:
        return "language"
    elif "focus" in issue_type_lower or "order" in issue_type_lower:
        return "reading_order"
    return "alt_text"


def _map_web_severity(impact: str) -> str:
    """Map axe-core impact to demo severity."""
    mapping = {
        "critical": "critical",
        "serious": "high",
        "moderate": "medium",
        "minor": "low",
    }
    return mapping.get(impact.lower(), "medium")


def _calculate_web_compliance_score(issues: list) -> int:
    """Calculate compliance score from issues."""
    base_score = 100
    for issue in issues:
        severity = issue.get("severity", "medium")
        if severity == "critical":
            base_score -= 15
        elif severity == "high":
            base_score -= 8
        elif severity == "medium":
            base_score -= 3
        else:
            base_score -= 1
    return max(0, base_score)


# =============================================================================
# Issue Normalization (Backend → Frontend field mapping)
# =============================================================================


def _infer_category_from_rule(rule: str, message: str) -> str:
    """
    Infer accessibility category from WCAG rule and message.

    Maps common WCAG criteria to accessibility categories:
    - 1.1.x → alt_text (Non-text Content)
    - 1.3.x → structure/heading (Info and Relationships)
    - 1.4.x → contrast/color (Distinguishable)
    - 2.1.x → keyboard
    - 2.4.x → navigation/heading (Navigable)
    - 3.1.x → language (Readable)
    - 4.1.x → aria (Compatible)
    """
    rule_lower = rule.lower() if rule else ""
    message_lower = message.lower() if message else ""

    # Check message keywords first (more specific)
    if "heading" in message_lower or "h1" in message_lower:
        return "heading"
    if "alt" in message_lower and ("text" in message_lower or "image" in message_lower):
        return "alt_text"
    if "contrast" in message_lower or "color" in message_lower:
        return "contrast"
    if "list" in message_lower and ("item" in message_lower or "l/li" in message_lower):
        return "list"
    if "table" in message_lower:
        return "table"
    if "link" in message_lower:
        return "link"
    if "language" in message_lower or "lang" in message_lower:
        return "language"
    if "keyboard" in message_lower or "focus" in message_lower:
        return "keyboard"
    if "form" in message_lower or "label" in message_lower:
        return "form"
    if "title" in message_lower and (
        "document" in message_lower
        or "pdf" in message_lower
        or "metadata" in message_lower
    ):
        return "title"
    if (
        "structure tree" in message_lower
        or "untagged" in message_lower
        or "tagged" in message_lower
    ):
        return "structure"
    if "bookmark" in message_lower or "outline" in message_lower:
        return "navigation"

    # Fall back to WCAG rule number mapping
    if "1.1" in rule_lower:
        return "alt_text"
    if "1.3" in rule_lower:
        return "structure"
    if "1.4" in rule_lower:
        return "contrast"
    if "2.1" in rule_lower:
        return "keyboard"
    if "2.4" in rule_lower:
        return "navigation"
    if "3.1" in rule_lower:
        return "language"
    if "4.1" in rule_lower:
        return "aria"
    if "pdf/ua" in rule_lower:
        return "structure"

    return "other"


def _normalize_issue(issue: dict, index: int) -> dict:
    """
    Normalize issue fields from backend format to frontend DemoIssue format.

    Backend fields → Frontend fields:
    - message → title
    - impact → description
    - rule → wcagCriterion
    - suggested_fix → suggestedFix
    """
    # Infer category if not explicitly set
    explicit_category = issue.get("category")
    if explicit_category:
        category = explicit_category
    else:
        rule = issue.get("rule", issue.get("wcagCriterion", ""))
        message = issue.get("message", issue.get("title", ""))
        category = _infer_category_from_rule(rule, message)

    # Build metadata dict for remediator (includes page_number, issue_type, etc.)
    # These fields are checked by remediator.can_auto_fix() to determine if issues are fixable
    category_lower = category.lower()
    metadata = {
        "page_number": issue.get("page_number", 1),
        "issue_type": issue.get("issue_type"),
        "element": issue.get("element"),
        "text": issue.get("text", ""),
        "bbox": issue.get("bbox"),
        "image_index": issue.get("image_index", 0),
        "generated_alt_text": issue.get("generated_alt_text") or issue.get("alt_text"),
        # Paragraph location: required for DOCX fixes (alt text, headings, lists, links)
        "paragraph_index": issue.get("paragraph_index"),
        "paragraph_indices": issue.get(
            "paragraph_indices"
        ),  # For list fixes (multiple paragraphs)
        # Heading-specific: needed for can_auto_fix check
        "suggested_level": issue.get("suggested_level")
        or issue.get("expected_level", 1),
        "current_level": issue.get("current_level"),
        "expected_level": issue.get("expected_level"),
        # List-specific: mark fake lists as fixable
        "is_fake_list": category_lower == "list"
        or "fake" in str(issue.get("title", "")).lower(),
        # Table-specific: assume tables have data rows
        "has_data_rows": True,
        "table_index": issue.get("table_index"),
        # Link text for link fixes
        "link_text": issue.get("link_text"),
        "link_url": issue.get("link_url"),
        # Title-specific: for document title fixes
        "suggested_title": issue.get("suggested_title"),
        "existing_title": issue.get("existing_title"),
        # PPTX-specific: required for slide-level fixes (alt text, structure, reading order)
        "slide_index": issue.get("slide_index"),
        "shape_name": issue.get("shape_name"),
        "shape_index": issue.get("shape_index"),
        # Image classification: decorative images get empty alt text per WCAG 1.1.1
        "is_decorative": issue.get("is_decorative", False),
        "image_type": issue.get("image_type"),
    }

    # Get the suggested fix from either field name
    suggested_fix = issue.get("suggestedFix") or issue.get("suggested_fix", "")

    return {
        "id": issue.get("id", f"issue-{index}"),
        "category": category,
        "severity": issue.get("severity", "medium"),
        "title": issue.get("title") or issue.get("message", "Accessibility issue"),
        "description": issue.get("description") or issue.get("impact", ""),
        "location": issue.get("location", "Unknown"),
        "wcagCriterion": issue.get("wcagCriterion")
        or issue.get("rule", "").replace("WCAG ", ""),
        "suggestedFix": suggested_fix,  # Frontend format (camelCase)
        "fix_suggestion": suggested_fix,  # Remediator format (snake_case)
        "codeSnippet": issue.get("codeSnippet") or issue.get("code_snippet"),
        "screenshot": issue.get("screenshot"),
        "selector": issue.get("selector"),
        "aiGenerated": issue.get("aiGenerated", bool(suggested_fix)),
        "metadata": metadata,  # Include metadata for remediator
    }


def _normalize_issues(issues: list) -> list:
    """Normalize a list of issues to frontend format."""
    return [_normalize_issue(issue, i) for i, issue in enumerate(issues)]


def _resolve_remediated_score(
    original_score: float,
    total_issues: int,
    fixed_count: int,
    remediation_result: Optional[dict],
) -> float:
    """Pick the post-remediation score to report to the user.

    Prefers the verified re-scan score, which comes from re-running the full
    scanner pipeline against the actual output file and therefore accounts for
    issues that only become detectable after remediation (a structure tree has
    to exist before its content marking and parent tree can be checked).

    The fix-ratio estimate is a fallback for when verification did not run. It
    assumes every fix landed and that nothing new was found, so fixing every
    detected issue always yields 100 regardless of the real output — which
    overstates results on exactly the documents that need the most work.
    """
    if remediation_result and remediation_result.get("score_verified"):
        verified = remediation_result.get("remediated_compliance_score")
        if verified is not None:
            return float(verified)

    if total_issues > 0 and fixed_count > 0:
        improvement = (fixed_count / total_issues) * (100 - original_score)
        return min(100.0, original_score + improvement)
    return float(original_score)


# =============================================================================
# Background Processing
# =============================================================================


async def process_demo_scan(
    scan_id: str,
    file_path: str,
    extension: str,
    db_session_maker,
):
    """Background task to process a demo scan with auto-remediation."""
    global _demo_remediated_files
    from ..db.database import SessionLocal

    db = None
    remediated_path = None
    try:
        # Get a new database session for background task
        db = SessionLocal()

        # Update status to processing
        scan = db.query(Scan).filter(Scan.id == scan_id).first()
        if not scan:
            return

        scan.status = ScanStatus.PROCESSING
        scan.progress = 10
        scan.progress_message = "Analyzing document structure..."
        db.commit()

        # Process based on file type
        start_time = datetime.utcnow()

        if extension == "pdf":
            result_data = _process_pdf(file_path, scan, db)
        elif extension == "docx":
            result_data = _process_docx(file_path, scan, db)
        elif extension == "pptx":
            result_data = _process_pptx(file_path, scan, db)
        elif extension == "xlsx":
            result_data = _process_xlsx(file_path, scan, db)
        elif extension == "tex":
            result_data = _process_latex(file_path, scan, db)
        elif extension in {"mp4", "webm", "mov", "mp3", "wav", "m4a"}:
            result_data = _process_multimedia(file_path, scan, db)
        elif extension in {"html", "css", "js", "zip"}:
            result_data = _process_code(file_path, scan, db)
        else:
            raise ValueError(f"Unsupported file type: {extension}")

        # =====================================================
        # Phase 2: Auto-Remediation
        # =====================================================
        scan.progress = 75
        scan.progress_message = "Applying AI-powered fixes..."
        db.commit()

        # Normalize issues before remediation
        # The remediator expects issues with 'category' and 'id' fields
        normalized_issues_for_remediation = _normalize_issues(
            result_data.get("issues", [])
        )
        logger.debug(
            f"Passing {len(normalized_issues_for_remediation)} normalized issues to remediator"
        )

        remediation_result = None
        try:
            # Create output path for remediated file
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            remediated_path = os.path.join(
                tempfile.gettempdir(), f"{base_name}_remediated_{scan_id}.{extension}"
            )

            # Run auto-remediation
            remediator = AutoRemediator()
            remediation_result = remediator.remediate(
                file_path=file_path,
                output_path=remediated_path,
                issues=normalized_issues_for_remediation,
            )

            fixed_count_result = remediation_result.get("fixed_count", 0)
            if (
                remediation_result.get("success")
                and fixed_count_result > 0
                and os.path.exists(remediated_path)
            ):
                # Only store remediated file if issues were actually fixed
                _demo_remediated_files[scan_id] = {
                    "path": remediated_path,
                    "created_at": datetime.utcnow(),
                }
                logger.info(
                    f"Demo scan {scan_id} remediated: {fixed_count_result} issues fixed"
                )
            else:
                # Don't keep reference if no fixes were made or remediation failed
                if os.path.exists(remediated_path):
                    try:
                        os.remove(remediated_path)
                        logger.debug(
                            f"Removed empty remediated file: {remediated_path}"
                        )
                    except Exception:
                        pass
                remediated_path = None
                if fixed_count_result == 0:
                    logger.info(
                        f"Demo scan {scan_id}: No issues auto-fixable, skipping remediated file"
                    )

        except Exception as e:
            logger.warning(f"Remediation failed for demo scan {scan_id}: {e}")
            remediation_result = {"success": False, "error": "Remediation failed"}
            remediated_path = None

        # Calculate processing time
        processing_time = (datetime.utcnow() - start_time).total_seconds()

        # Calculate remediated compliance score (estimate)
        original_score = result_data.get("compliance_score", 0)

        # Get fix counts from AutoRemediator
        auto_fixed_count = (
            remediation_result.get("fixed_count", 0) if remediation_result else 0
        )
        manual_count = (
            remediation_result.get("manual_count", 0) if remediation_result else 0
        )

        # For multimedia, also count fixes from caption/description generation
        multimedia_fixed_count = result_data.get("fixed_count", 0)
        fixed_count = auto_fixed_count + multimedia_fixed_count

        # Check if multimedia accessibility files are available
        multimedia_remediation_available = result_data.get(
            "remediation_available", False
        )

        total_issues = len(result_data.get("issues", []))

        remediated_score = _resolve_remediated_score(
            original_score=original_score,
            total_issues=total_issues,
            fixed_count=fixed_count,
            remediation_result=remediation_result,
        )

        # Use already normalized issues (reuse from remediation step)
        # This avoids double normalization and ensures consistency
        normalized_issues = normalized_issues_for_remediation

        # Store results with remediation data
        scan_result = ScanResult(
            id=str(uuid.uuid4()),
            scan_id=scan_id,
            compliance_score=result_data.get("compliance_score", 0),
            wcag_level="AA",
            issues=normalized_issues,
            critical_issues=sum(
                1 for i in normalized_issues if i.get("severity") == "critical"
            ),
            high_issues=sum(
                1 for i in normalized_issues if i.get("severity") == "high"
            ),
            medium_issues=sum(
                1 for i in normalized_issues if i.get("severity") == "medium"
            ),
            low_issues=sum(1 for i in normalized_issues if i.get("severity") == "low"),
            # Store remediation info in suggestions field (JSON)
            # Include both document remediation and multimedia accessibility files
            suggestions={
                "remediation_available": remediated_path is not None
                or multimedia_remediation_available,
                "remediated_score": remediated_score,
                "fixed_count": fixed_count,
                "manual_count": manual_count,
                "fixed_issues": (
                    remediation_result.get("fixed_issues", [])
                    if remediation_result
                    else []
                ),
                "manual_issues": (
                    remediation_result.get("manual_issues", [])
                    if remediation_result
                    else []
                ),
                # Multimedia-specific data
                "download_files": result_data.get("download_files", []),
            },
        )
        db.add(scan_result)

        # Update scan status
        scan.status = ScanStatus.COMPLETED
        scan.progress = 100
        scan.progress_message = "Scan and remediation complete"
        scan.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            f"Demo scan {scan_id} completed in {processing_time:.2f}s (fixed {fixed_count}/{total_issues} issues)"
        )

    except Exception as e:
        logger.error(f"Demo scan {scan_id} failed: {e}", exc_info=True)
        if db:
            scan = db.query(Scan).filter(Scan.id == scan_id).first()
            if scan:
                scan.status = ScanStatus.FAILED
                scan.error_message = (
                    "Processing encountered an error. Please try again."
                )
                scan.progress_message = "Processing failed"
                db.commit()
    finally:
        # Cleanup original upload and the directory holding it. The remediated
        # file lives elsewhere (tempfile.gettempdir()) and is kept for download.
        decrement_processing()
        shutil.rmtree(os.path.dirname(file_path), ignore_errors=True)
        if db:
            db.close()


def _process_pdf(file_path: str, scan: Scan, db: Session) -> dict:
    """Process PDF document with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during PDF processing."""
        try:
            # Progress range: 15-65% for PDF processing (leaves room for remediation at 75%)
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO PDF] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO PDF] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing PDF processor..."
    db.commit()

    processor = PDFProcessor(
        progress_callback=progress_callback,
        generate_alt_text=True,  # AI-generate alt text for images
        validate_alt_text=True,  # Validate existing alt text accuracy
    )
    result = processor.process_pdf(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert Pydantic model to dict
    return result.model_dump() if hasattr(result, "model_dump") else result.dict()


def _process_docx(file_path: str, scan: Scan, db: Session) -> dict:
    """Process Word document with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during DOCX processing."""
        try:
            # Progress range: 15-65% for DOCX processing
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO DOCX] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO DOCX] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing Word processor..."
    db.commit()

    processor = DocxProcessor(
        progress_callback=progress_callback,
        generate_alt_text=True,  # AI-generate alt text for images
        validate_alt_text=True,  # Validate existing alt text accuracy
    )
    result = processor.process_docx(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert Pydantic model to dict
    return result.model_dump() if hasattr(result, "model_dump") else result.dict()


def _process_pptx(file_path: str, scan: Scan, db: Session) -> dict:
    """Process PowerPoint document with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during PPTX processing."""
        try:
            # Progress range: 15-65% for PPTX processing
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO PPTX] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO PPTX] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing PowerPoint processor..."
    db.commit()

    processor = PowerPointProcessor(
        progress_callback=progress_callback,
        generate_alt_text=True,  # AI-generate alt text for images
        validate_alt_text=True,  # Validate existing alt text accuracy
    )
    result = processor.process_pptx(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert Pydantic model to dict
    return result.model_dump() if hasattr(result, "model_dump") else result.dict()


def _process_xlsx(file_path: str, scan: Scan, db: Session) -> dict:
    """Process Excel spreadsheet with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during XLSX processing."""
        try:
            # Progress range: 15-65% for XLSX processing
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO XLSX] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO XLSX] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing Excel processor..."
    db.commit()

    processor = XlsxProcessor(
        progress_callback=progress_callback,
        generate_alt_text=True,  # AI-generate alt text for embedded images
        validate_alt_text=True,  # Validate existing alt text accuracy
        generate_chart_descriptions=True,  # AI-generate descriptions for charts
    )
    result = processor.process_xlsx(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert Pydantic model to dict
    return result.model_dump() if hasattr(result, "model_dump") else result.dict()


def _process_latex(file_path: str, scan: Scan, db: Session) -> dict:
    """Process LaTeX document for MathML accessibility with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during LaTeX processing."""
        try:
            # Progress range: 15-65% for LaTeX processing
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO LATEX] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO LATEX] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing LaTeX processor..."
    db.commit()

    processor = LaTeXProcessor(progress_callback=progress_callback)
    result = processor.process_document(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert result to standard issues format
    issues = []
    for i, eq in enumerate(result.equations):
        if not eq.conversion_success:
            issues.append(
                {
                    "id": f"latex-{i}",
                    "category": "alt_text",
                    "severity": "high",
                    "title": "Equation needs accessible MathML",
                    "description": f"LaTeX equation could not be fully converted: {eq.original_latex[:50]}...",
                    "location": f"Equation {i + 1}",
                    "wcagCriterion": "1.1.1",
                    "suggestedFix": (
                        f"MathML: {eq.mathml[:100]}..."
                        if eq.mathml
                        else "Manual conversion needed"
                    ),
                    "aiGenerated": True,
                }
            )
        else:
            # Successfully converted - mark as accessible but include for reference
            if not eq.aria_label:
                issues.append(
                    {
                        "id": f"latex-aria-{i}",
                        "category": "alt_text",
                        "severity": "medium",
                        "title": "Equation missing ARIA label",
                        "description": "Equation converted to MathML but needs ARIA label for screen readers",
                        "location": f"Equation {i + 1}",
                        "wcagCriterion": "1.1.1",
                        "suggestedFix": f"Add aria-label describing: {eq.original_latex[:50]}",
                        "aiGenerated": True,
                    }
                )

    return {
        "compliance_score": int(result.compliance_score),
        "issues": issues,
        "total_equations": result.total_equations,
        "successful_conversions": result.successful_conversions,
        "html_output": result.html_output,
    }


def _process_multimedia(file_path: str, scan: Scan, db: Session) -> dict:
    """Process video/audio for accessibility with progress updates."""
    global _demo_remediated_files

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during multimedia processing."""
        try:
            # Progress range: 15-65% for multimedia processing
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO MEDIA] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO MEDIA] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing multimedia processor..."
    db.commit()

    processor = MultimediaProcessor(progress_callback=progress_callback)

    result = processor.process_media(
        file_path,
        generate_captions=True,
        generate_audio_descriptions=True,
        generate_spoken_descriptions=True,  # Convert descriptions to actual audio for blind users
        detect_flashing=True,
        enhance_captions=True,
        generate_transcript=True,
    )

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Determine what accessibility content was generated
    has_generated_captions = result.caption_formats is not None
    has_audio_descriptions = (
        result.audio_descriptions is not None and len(result.audio_descriptions) > 0
    )
    has_spoken_audio = (
        result.audio_descriptions_audio_path is not None
        and os.path.exists(result.audio_descriptions_audio_path or "")
    )
    has_remediated_video = result.remediated_video_path is not None and os.path.exists(
        result.remediated_video_path or ""
    )

    # Save generated accessibility files for download
    scan_id = scan.id
    download_files = {}
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    output_dir = os.path.dirname(file_path)

    # === CAPTIONS (for deaf/hard-of-hearing users) ===
    if has_generated_captions:
        if result.caption_formats.get("webvtt"):
            vtt_path = os.path.join(output_dir, f"{base_name}_captions.vtt")
            with open(vtt_path, "w", encoding="utf-8") as f:
                f.write(result.caption_formats["webvtt"])
            download_files["captions_vtt"] = vtt_path
            logger.info(f"[DEMO MEDIA] Saved VTT captions: {vtt_path}")

        if result.caption_formats.get("srt"):
            srt_path = os.path.join(output_dir, f"{base_name}_captions.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(result.caption_formats["srt"])
            download_files["captions_srt"] = srt_path
            logger.info(f"[DEMO MEDIA] Saved SRT captions: {srt_path}")

    # === AUDIO DESCRIPTIONS (for blind/visually impaired users) ===
    if has_audio_descriptions:
        # Save text version (for reference/editing)
        desc_text_path = os.path.join(output_dir, f"{base_name}_audio_descriptions.txt")
        with open(desc_text_path, "w", encoding="utf-8") as f:
            f.write("AUDIO DESCRIPTIONS FOR VISUAL CONTENT\n")
            f.write("=" * 50 + "\n\n")
            f.write("These descriptions help blind and visually impaired users\n")
            f.write("understand the visual content of this video.\n\n")
            for desc in result.audio_descriptions:
                timestamp_str = (
                    f"{int(desc.timestamp // 60):02d}:{int(desc.timestamp % 60):02d}"
                )
                f.write(f"[{timestamp_str}] {desc.description}\n\n")
        download_files["audio_descriptions_text"] = desc_text_path
        logger.info(f"[DEMO MEDIA] Saved audio descriptions text: {desc_text_path}")

    # === SPOKEN AUDIO DESCRIPTIONS (actual audio for blind users) ===
    if has_spoken_audio:
        # The TTS-generated MP3 file with spoken descriptions
        download_files["audio_descriptions_audio"] = (
            result.audio_descriptions_audio_path
        )
        logger.info(
            f"[DEMO MEDIA] Spoken audio descriptions available: {result.audio_descriptions_audio_path}"
        )

    # === FULLY REMEDIATED VIDEO (best option - complete accessible package) ===
    has_remediated_video = result.remediated_video_path is not None and os.path.exists(
        result.remediated_video_path or ""
    )
    if has_remediated_video:
        download_files["accessible_video"] = result.remediated_video_path
        logger.info(
            f"[DEMO MEDIA] Fully accessible video available: {result.remediated_video_path}"
        )

    # Store for download
    # Priority:
    # 1. Fully remediated video (complete package with embedded subtitles + audio descriptions)
    # 2. Individual files as fallback
    if download_files:
        primary_file = (
            download_files.get("accessible_video")  # Complete accessible video (BEST)
            or download_files.get(
                "audio_descriptions_audio"
            )  # Spoken audio for blind users
            or download_files.get("captions_vtt")  # Captions for deaf users
            or download_files.get("audio_descriptions_text")  # Text fallback
        )
        if primary_file:
            _demo_remediated_files[scan_id] = {
                "path": primary_file,
                "all_files": download_files,
                "created_at": datetime.utcnow(),
            }
            logger.info(
                f"[DEMO MEDIA] Stored accessibility files for download: {list(download_files.keys())}"
            )

    # Convert result to standard issues format
    issues = []

    # Check for missing captions (WCAG 1.2.2)
    if result.media_type == "video":
        if has_generated_captions:
            # We generated captions - show as available fix
            issues.append(
                {
                    "id": "captions-available",
                    "category": "media",
                    "severity": "info",  # Not a problem - it's a fix!
                    "title": "AI-generated captions available",
                    "description": "Captions have been automatically generated for this video, making it accessible to deaf and hard-of-hearing users.",
                    "location": "Full video",
                    "wcagCriterion": "1.2.2",
                    "suggestedFix": "Download captions in VTT or SRT format"
                    + (
                        ", or download the fully accessible video with embedded subtitles"
                        if has_remediated_video
                        else ""
                    ),
                    "aiGenerated": True,
                }
            )
        elif not result.has_captions:
            # Check if video has audio that needs captioning
            has_audio = result.transcription and len(result.transcription.strip()) > 0
            if has_audio:
                # Video has audio but no captions - this is a real issue
                issues.append(
                    {
                        "id": "caption-missing",
                        "category": "media",
                        "severity": "critical",
                        "title": "Video missing captions",
                        "description": "This video has audio content but no captions, making it inaccessible to deaf and hard-of-hearing users.",
                        "location": "Full video",
                        "wcagCriterion": "1.2.2",
                        "suggestedFix": "Add captions to transcribe the spoken content",
                        "aiGenerated": False,
                    }
                )
            else:
                # Silent video - not a captions issue, but may need audio descriptions
                extra_context = ""
                if has_spoken_audio:
                    extra_context = " AI-generated audio descriptions are available to help blind users understand the visual content."
                elif has_audio_descriptions:
                    extra_context = (
                        " Text descriptions of visual content are available."
                    )

                issues.append(
                    {
                        "id": "silent-video",
                        "category": "media",
                        "severity": "low",  # Silent video is not a critical accessibility issue
                        "title": "Silent video detected",
                        "description": f"This video has no audio track.{extra_context}",
                        "location": "Full video",
                        "wcagCriterion": "1.2.1",  # Pre-recorded audio/video alternatives
                        "suggestedFix": (
                            "Download the fully accessible video with embedded audio descriptions"
                            if has_remediated_video
                            else (
                                "Download the AI-generated audio descriptions"
                                if has_spoken_audio
                                else "Consider adding narration to describe the visual content"
                            )
                        ),
                        "aiGenerated": has_spoken_audio or has_audio_descriptions,
                    }
                )

    # Check for flashing content
    if result.flashing_analysis and result.flashing_analysis.has_flashing:
        flash_freq = result.flashing_analysis.max_flash_frequency or "unknown"
        flash_times = result.flashing_analysis.timestamps or ["Unknown"]
        issues.append(
            {
                "id": "flashing-content",
                "category": "media",
                "severity": "critical",
                "title": "Potential seizure-triggering content",
                "description": f"Video contains rapid flashing at {flash_freq} Hz",
                "location": flash_times,
                "wcagCriterion": "2.3.1",
                "suggestedFix": "Reduce flash frequency to below 3 flashes per second",
                "aiGenerated": False,
            }
        )

    # Check for audio descriptions (WCAG 1.2.5)
    # === AUDIO DESCRIPTIONS ISSUE FLAGGING ===
    # Audio descriptions help BLIND users understand VISUAL content
    # This is separate from captions (which help DEAF users understand AUDIO content)
    # ALL videos need audio descriptions, regardless of whether they have audio
    if result.media_type == "video":
        if has_spoken_audio:
            # Best case: We generated spoken audio descriptions
            # This is a FIX, not an issue - but we report it so user knows it's available
            issues.append(
                {
                    "id": "audio-desc-available",
                    "category": "media",
                    "severity": "info",  # Not a problem - it's a fix!
                    "title": "Audio descriptions generated",
                    "description": "AI-generated spoken audio descriptions are available for blind users. These describe visual content including who is speaking, actions, scene changes, and on-screen text.",
                    "location": "Full video",
                    "wcagCriterion": "1.2.5",
                    "suggestedFix": "Download the audio descriptions MP3 file",
                    "aiGenerated": True,
                }
            )
        elif has_audio_descriptions:
            # We have text descriptions but couldn't generate spoken audio
            # Still useful but not ideal for blind users
            issues.append(
                {
                    "id": "audio-desc-text-only",
                    "category": "media",
                    "severity": "medium",
                    "title": "Audio descriptions (text only)",
                    "description": "Text descriptions of visual content are available, but spoken audio could not be generated. Blind users may need assistance reading these descriptions.",
                    "location": "Full video",
                    "wcagCriterion": "1.2.5",
                    "suggestedFix": "Download text descriptions and consider recording them manually",
                    "aiGenerated": True,
                }
            )
        else:
            # Could not generate any audio descriptions - this is an issue
            issues.append(
                {
                    "id": "audio-desc-missing",
                    "category": "media",
                    "severity": "high",
                    "title": "No audio descriptions",
                    "description": "Video lacks audio descriptions for visual content. Blind users cannot understand what is happening visually (who is speaking, actions, scene changes, on-screen text).",
                    "location": "Full video",
                    "wcagCriterion": "1.2.5",
                    "suggestedFix": "Add audio descriptions manually describing visual content",
                    "aiGenerated": False,
                }
            )

    # Calculate compliance score based on issues
    base_score = 100
    for issue in issues:
        if issue["severity"] == "critical":
            base_score -= 25
        elif issue["severity"] == "high":
            base_score -= 15
        elif issue["severity"] == "medium":
            base_score -= 5
        # "info" severity doesn't penalize - it's a successful fix

    # Determine fixed count and remediation availability
    fixed_count = 0
    if has_generated_captions:
        fixed_count += 1
    if has_spoken_audio:
        fixed_count += 1  # Spoken audio is the real fix for blind users
    elif has_audio_descriptions:
        fixed_count += 0.5  # Text-only is partial fix (cast to int later)
    if has_remediated_video:
        fixed_count += 1  # Complete accessible video package

    return {
        "compliance_score": max(0, base_score),
        "issues": issues,
        "transcription": result.transcription,
        "caption_formats": result.caption_formats,
        "audio_descriptions": [
            {
                "timestamp": desc.timestamp,
                "description": desc.description,
                "scene_type": desc.scene_type,
            }
            for desc in (result.audio_descriptions or [])
        ],
        "duration": result.duration,
        "media_type": result.media_type,
        "fixed_count": int(fixed_count),  # Cast to int (0.5 becomes 0)
        "remediation_available": fixed_count >= 1,
        "download_files": list(download_files.keys()) if download_files else [],
        # Detailed accessibility file info for frontend
        "accessibility_files": {
            "captions": {
                "available": has_generated_captions,
                "formats": ["vtt", "srt"] if has_generated_captions else [],
                "purpose": "For deaf and hard-of-hearing users",
            },
            "audio_descriptions": {
                "available": has_audio_descriptions or has_spoken_audio,
                "has_spoken_audio": has_spoken_audio,
                "has_text": has_audio_descriptions,
                "purpose": "For blind and visually impaired users",
            },
            "accessible_video": {
                "available": has_remediated_video,
                "format": (
                    "mkv" if has_spoken_audio else "mp4"
                ),  # MKV for multiple audio tracks
                "includes_subtitles": has_generated_captions,
                "includes_audio_descriptions": has_spoken_audio,
                "purpose": "Complete accessible video with embedded subtitles and audio description track",
            },
        },
    }


def _process_code(file_path: str, scan: Scan, db: Session) -> dict:
    """Process HTML/CSS/JS code for accessibility issues with progress updates."""

    def progress_callback(current: int, total: int, message: str):
        """Update scan progress during code scanning."""
        try:
            # Progress range: 15-65% for code scanning
            if total > 0:
                raw_progress = 15 + (current / total) * 50
                scan.progress = min(int(raw_progress), 65)
            else:
                scan.progress = 15
            scan.progress_message = message
            db.commit()
            logger.debug(f"[DEMO CODE] Progress: {scan.progress}% - {message}")
        except Exception as e:
            logger.warning(f"[DEMO CODE] Failed to update progress: {e}")

    scan.progress = 15
    scan.progress_message = "Initializing code scanner..."
    db.commit()

    scanner = CodeScanner(
        scan_images=True,
        generate_fixes=True,
        validate_alt_text=True,
        scan_cvd=True,
        progress_callback=progress_callback,
    )
    result = scanner.scan_uploaded_code(file_path)

    scan.progress = 70
    scan.progress_message = "Compiling accessibility report..."
    db.commit()

    # Convert code issues to standard format
    issues = []
    for i, issue in enumerate(result.issues):
        # CodeIssue is a Pydantic model - use attribute access
        # suggestedFix = human-readable description of the fix
        # codeSnippet = AI-generated corrected code (not the original problematic code)
        issues.append(
            {
                "id": f"code-{i}",
                "category": _map_code_issue_category(issue.category),
                "severity": issue.severity.lower() if issue.severity else "medium",
                "title": issue.rule or "Accessibility issue",
                "description": issue.description or "",
                "location": f"{issue.file_path or 'Unknown'}:{issue.line_number or '?'}",
                "wcagCriterion": issue.wcag_criterion or "",
                "suggestedFix": issue.fix_suggestion
                or "",  # Human-readable description
                "codeSnippet": issue.ai_generated_fix,  # AI-generated corrected code
                "aiGenerated": bool(issue.ai_generated_fix),
            }
        )

    return {
        "compliance_score": result.compliance_score,
        "issues": issues,
        "files_analyzed": result.files_analyzed,
        "total_lines": result.total_lines,
    }


def _map_code_issue_category(issue_type: str) -> str:
    """Map code scanner issue types to demo categories."""
    mapping = {
        "missing-alt": "alt_text",
        "empty-alt": "alt_text",
        "decorative-alt": "alt_text",
        "heading-order": "heading",
        "missing-heading": "heading",
        "skip-heading": "heading",
        "contrast": "contrast",
        "color-only": "contrast",
        "missing-label": "link",
        "empty-link": "link",
        "generic-link": "link",
        "missing-lang": "language",
        "table-header": "table",
        "table-caption": "table",
        "list-semantics": "list",
        "focus-order": "reading_order",
    }
    return mapping.get(issue_type, "alt_text")


# Note: Standalone image scanning is not exposed in the demo.
# Images are scanned within documents (PDFs, PPTX, DOCX) using ImageAltTextGenerator internally.
