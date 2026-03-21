"""
Document Security Validator

Validates uploaded documents for security threats including:
- Malicious macros in Office documents
- Suspicious PDF elements (JavaScript, auto-actions)
- File type spoofing (magic byte validation)
- Zip bombs and archive attacks
- Prompt injection patterns

Uses oletools for Office analysis and custom PDF inspection.
"""

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple, Union
import hashlib

logger = logging.getLogger(__name__)


class ThreatLevel(Enum):
    """Threat severity levels."""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class SecurityFinding:
    """A single security finding."""

    category: str
    description: str
    threat_level: ThreatLevel
    details: Optional[str] = None
    remediation: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of document security validation."""

    is_safe: bool
    threat_level: ThreatLevel
    file_type: str
    file_hash: str
    findings: List[SecurityFinding] = field(default_factory=list)
    sanitized: bool = False
    sanitized_path: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "is_safe": self.is_safe,
            "threat_level": self.threat_level.value,
            "file_type": self.file_type,
            "file_hash": self.file_hash,
            "findings": [
                {
                    "category": f.category,
                    "description": f.description,
                    "threat_level": f.threat_level.value,
                    "details": f.details,
                    "remediation": f.remediation,
                }
                for f in self.findings
            ],
            "sanitized": self.sanitized,
        }


# Magic bytes for file type detection
MAGIC_BYTES = {
    # Office Open XML (docx, xlsx, pptx)
    b"PK\x03\x04": "zip_archive",  # Could be OOXML or regular ZIP
    # Legacy Office (doc, xls, ppt)
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "ole_compound",
    # PDF
    b"%PDF": "pdf",
    # Images
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "webp",  # WebP starts with RIFF
    # HTML/XML
    b"<!DOCTYPE": "html",
    b"<html": "html",
    b"<?xml": "xml",
    # LaTeX
    b"\\documentclass": "latex",
    b"\\begin{document}": "latex",
}

# Allowed file extensions and their expected magic types
ALLOWED_EXTENSIONS = {
    ".pdf": ["pdf"],
    ".docx": ["zip_archive"],
    ".xlsx": ["zip_archive"],
    ".pptx": ["zip_archive"],
    ".doc": ["ole_compound"],
    ".xls": ["ole_compound"],
    ".ppt": ["ole_compound"],
    ".png": ["png"],
    ".jpg": ["jpeg"],
    ".jpeg": ["jpeg"],
    ".gif": ["gif"],
    ".webp": ["webp"],
    # Code files (text-based, may not have magic bytes)
    ".html": ["html", "text", "unknown"],
    ".htm": ["html", "text", "unknown"],
    ".js": ["text", "unknown"],
    ".css": ["text", "unknown"],
    ".tex": ["latex", "text", "unknown"],
    ".zip": ["zip_archive"],
}

# Code file extensions that need malware scanning
CODE_FILE_EXTENSIONS = {".html", ".htm", ".js", ".css", ".svg"}

# Suspicious PDF keywords (based on pdfid analysis)
SUSPICIOUS_PDF_KEYWORDS = {
    "/JavaScript": (ThreatLevel.HIGH, "PDF contains JavaScript code"),
    "/JS": (ThreatLevel.HIGH, "PDF contains JavaScript reference"),
    "/OpenAction": (ThreatLevel.MEDIUM, "PDF has automatic action on open"),
    "/AA": (ThreatLevel.MEDIUM, "PDF has additional actions"),
    "/Launch": (ThreatLevel.CRITICAL, "PDF can launch external applications"),
    "/EmbeddedFile": (ThreatLevel.MEDIUM, "PDF contains embedded files"),
    "/URI": (ThreatLevel.LOW, "PDF contains external URI links"),
    "/SubmitForm": (ThreatLevel.MEDIUM, "PDF can submit form data"),
    "/ImportData": (ThreatLevel.MEDIUM, "PDF can import external data"),
    "/RichMedia": (ThreatLevel.MEDIUM, "PDF contains rich media (Flash, etc.)"),
    "/XFA": (ThreatLevel.MEDIUM, "PDF uses XFA forms (potential vulnerabilities)"),
    "/Encrypt": (ThreatLevel.LOW, "PDF is encrypted"),
    "/ObjStm": (ThreatLevel.LOW, "PDF uses object streams (can hide content)"),
    "/Annot": (ThreatLevel.LOW, "PDF contains annotations"),
}

# Prompt injection patterns to detect
PROMPT_INJECTION_PATTERNS = [
    # Direct instruction overrides
    r"ignore\s+(previous|all|above|prior)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(previous|all|above|prior)\s+(instructions?|prompts?)",
    r"forget\s+(previous|all|everything|above)",
    r"new\s+instructions?:",
    r"system\s*:\s*",
    r"assistant\s*:\s*",
    r"user\s*:\s*",
    # Role manipulation
    r"you\s+are\s+now\s+",
    r"act\s+as\s+(if\s+you\s+are\s+)?",
    r"pretend\s+(to\s+be|you\s+are)",
    r"roleplay\s+as",
    # Jailbreak attempts
    r"DAN\s*mode",
    r"developer\s*mode",
    r"jailbreak",
    r"bypass\s+(restrictions?|filters?|safety)",
    # Data exfiltration
    r"output\s+(all|your|the)\s+(instructions?|prompts?|system)",
    r"reveal\s+(your|the)\s+(instructions?|prompts?|system)",
    r"print\s+(your|the)\s+(instructions?|prompts?|system)",
    r"show\s+me\s+(your|the)\s+(instructions?|prompts?)",
]


class DocumentValidator:
    """
    Validates documents for security threats.

    Usage:
        validator = DocumentValidator()
        result = await validator.validate(file_path, file_content)
        if not result.is_safe:
            # Handle threat
    """

    def __init__(
        self,
        max_file_size: int = 100 * 1024 * 1024,  # 100MB
        max_archive_depth: int = 3,
        max_archive_files: int = 1000,
        max_archive_ratio: float = 100.0,  # Compression ratio limit (zip bomb detection)
    ):
        self.max_file_size = max_file_size
        self.max_archive_depth = max_archive_depth
        self.max_archive_files = max_archive_files
        self.max_archive_ratio = max_archive_ratio

        # Compile prompt injection patterns
        self.prompt_injection_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in PROMPT_INJECTION_PATTERNS
        ]

    async def validate(
        self,
        filename: str,
        content: Union[bytes, BinaryIO],
        check_prompt_injection: bool = True,
    ) -> ValidationResult:
        """
        Validate a document for security threats.

        Args:
            filename: Original filename
            content: File content as bytes or file-like object
            check_prompt_injection: Whether to check for prompt injection

        Returns:
            ValidationResult with findings
        """
        findings: List[SecurityFinding] = []

        # Convert to bytes if needed
        if hasattr(content, "read"):
            content = content.read()
            if hasattr(content, "seek"):
                content.seek(0)

        # Calculate file hash
        file_hash = hashlib.sha256(content).hexdigest()

        # Get file extension
        ext = Path(filename).suffix.lower()

        # 1. Check file size
        if len(content) > self.max_file_size:
            findings.append(
                SecurityFinding(
                    category="file_size",
                    description=f"File exceeds maximum size ({len(content)} > {self.max_file_size})",
                    threat_level=ThreatLevel.HIGH,
                    remediation="Upload a smaller file",
                )
            )

        # 2. Validate magic bytes
        detected_type = self._detect_file_type(content)
        if ext in ALLOWED_EXTENSIONS:
            if detected_type not in ALLOWED_EXTENSIONS[ext]:
                findings.append(
                    SecurityFinding(
                        category="file_type_mismatch",
                        description=f"File extension '{ext}' doesn't match actual content type '{detected_type}'",
                        threat_level=ThreatLevel.HIGH,
                        details="Possible file type spoofing attempt",
                        remediation="Upload a file with correct extension",
                    )
                )
        elif ext:
            findings.append(
                SecurityFinding(
                    category="unsupported_extension",
                    description=f"File extension '{ext}' is not in the allowed list",
                    threat_level=ThreatLevel.MEDIUM,
                    remediation="Upload a supported file type (PDF, DOCX, XLSX, PPTX, images)",
                )
            )

        # 3. Type-specific validation
        if detected_type == "pdf":
            pdf_findings = self._analyze_pdf(content)
            findings.extend(pdf_findings)
        elif detected_type == "zip_archive":
            # Check for OOXML (Office) documents
            zip_findings = self._analyze_zip_archive(content, ext)
            findings.extend(zip_findings)

            # Check for Office macros if it's an Office file
            if ext in [".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"]:
                macro_findings = await self._analyze_office_macros(content, ext)
                findings.extend(macro_findings)
        elif detected_type == "ole_compound":
            # Legacy Office format - always check for macros
            macro_findings = await self._analyze_ole_macros(content)
            findings.extend(macro_findings)

        # 4. Scan standalone code files for malicious patterns
        if ext in CODE_FILE_EXTENSIONS:
            code_findings = self._scan_standalone_code(content, filename)
            findings.extend(code_findings)

        # 5. Check for prompt injection in text content
        if check_prompt_injection:
            injection_findings = self._check_prompt_injection(content, detected_type)
            findings.extend(injection_findings)

        # Determine overall threat level
        max_threat = ThreatLevel.SAFE
        for finding in findings:
            if finding.threat_level.value > max_threat.value:
                max_threat = finding.threat_level

        # Determine if safe (allow LOW threats through)
        is_safe = max_threat in [ThreatLevel.SAFE, ThreatLevel.LOW]

        return ValidationResult(
            is_safe=is_safe,
            threat_level=max_threat,
            file_type=detected_type,
            file_hash=file_hash,
            findings=findings,
        )

    def _detect_file_type(self, content: bytes) -> str:
        """Detect file type from magic bytes."""
        for magic, file_type in MAGIC_BYTES.items():
            if content.startswith(magic):
                return file_type
        return "unknown"

    def _analyze_pdf(self, content: bytes) -> List[SecurityFinding]:
        """Analyze PDF for suspicious elements."""
        findings = []

        try:
            # Convert to string for keyword search
            # PDF can have binary content, so we use latin-1 encoding
            pdf_text = content.decode("latin-1", errors="ignore")

            for keyword, (threat_level, description) in SUSPICIOUS_PDF_KEYWORDS.items():
                # Count occurrences
                count = pdf_text.count(keyword)
                if count > 0:
                    findings.append(
                        SecurityFinding(
                            category="pdf_suspicious_element",
                            description=description,
                            threat_level=threat_level,
                            details=f"Found {count} occurrence(s) of {keyword}",
                            remediation="Consider using a PDF sanitizer or reviewing the PDF manually",
                        )
                    )

            # Check for extremely long strings (potential buffer overflow attempts)
            if len(max(pdf_text.split(), key=len, default="")) > 10000:
                findings.append(
                    SecurityFinding(
                        category="pdf_suspicious_content",
                        description="PDF contains unusually long strings",
                        threat_level=ThreatLevel.MEDIUM,
                        details="Potential buffer overflow or obfuscation attempt",
                    )
                )

            # Check for excessive object count (potential DoS)
            obj_count = pdf_text.count(" obj")
            if obj_count > 50000:
                findings.append(
                    SecurityFinding(
                        category="pdf_excessive_objects",
                        description=f"PDF contains excessive objects ({obj_count})",
                        threat_level=ThreatLevel.MEDIUM,
                        details="Potential denial of service attempt",
                    )
                )

        except Exception as e:
            logger.warning(f"PDF analysis error: {e}")
            findings.append(
                SecurityFinding(
                    category="pdf_parse_error",
                    description="Failed to fully analyze PDF structure",
                    threat_level=ThreatLevel.LOW,
                    details=str(e),
                )
            )

        return findings

    def _analyze_zip_archive(self, content: bytes, ext: str) -> List[SecurityFinding]:
        """Analyze ZIP archive for threats (zip bombs, etc.)."""
        findings = []

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Check file count
                file_count = len(zf.namelist())
                if file_count > self.max_archive_files:
                    findings.append(
                        SecurityFinding(
                            category="archive_file_count",
                            description=f"Archive contains too many files ({file_count})",
                            threat_level=ThreatLevel.HIGH,
                            details="Potential zip bomb or archive attack",
                            remediation="Reduce the number of files in the archive",
                        )
                    )

                # Check compression ratio (zip bomb detection)
                compressed_size = len(content)
                total_uncompressed = sum(info.file_size for info in zf.infolist())

                if compressed_size > 0:
                    ratio = total_uncompressed / compressed_size
                    if ratio > self.max_archive_ratio:
                        findings.append(
                            SecurityFinding(
                                category="archive_compression_ratio",
                                description=f"Suspicious compression ratio ({ratio:.1f}x)",
                                threat_level=ThreatLevel.CRITICAL,
                                details="Potential zip bomb detected",
                                remediation="Use a standard compression ratio",
                            )
                        )

                # Check for path traversal attempts
                for name in zf.namelist():
                    if ".." in name or name.startswith("/"):
                        findings.append(
                            SecurityFinding(
                                category="archive_path_traversal",
                                description="Archive contains path traversal attempt",
                                threat_level=ThreatLevel.CRITICAL,
                                details=f"Suspicious path: {name}",
                                remediation="Remove malicious paths from archive",
                            )
                        )
                        break

                # Check nesting depth
                max_depth = max((name.count("/") for name in zf.namelist()), default=0)
                if max_depth > self.max_archive_depth:
                    findings.append(
                        SecurityFinding(
                            category="archive_depth",
                            description=f"Archive nesting too deep ({max_depth} levels)",
                            threat_level=ThreatLevel.MEDIUM,
                            remediation="Flatten the archive structure",
                        )
                    )

                # Scan code files inside ZIP for malicious content
                code_findings = self._scan_zip_code_content(zf)
                findings.extend(code_findings)

        except zipfile.BadZipFile:
            findings.append(
                SecurityFinding(
                    category="archive_corrupt",
                    description="Invalid or corrupt ZIP archive",
                    threat_level=ThreatLevel.MEDIUM,
                )
            )
        except Exception as e:
            logger.warning(f"ZIP analysis error: {e}")

        return findings

    def _scan_zip_code_content(self, zf: zipfile.ZipFile) -> List[SecurityFinding]:
        """Scan code files (HTML, JS, CSS) inside ZIP for malicious patterns."""
        findings = []
        code_extensions = {
            ".html",
            ".htm",
            ".js",
            ".css",
            ".svg",
            ".php",
            ".asp",
            ".jsp",
        }

        # Malicious JavaScript patterns
        js_malware_patterns = [
            (r"\beval\s*\(", "eval() execution - potential code injection"),
            (r"\bFunction\s*\(", "Function constructor - potential code injection"),
            (
                r"document\.write\s*\([^)]*<script",
                "document.write with script - potential XSS",
            ),
            (
                r"innerHTML\s*=\s*[^;]*\+",
                "innerHTML with concatenation - potential XSS",
            ),
            (r"fromCharCode\s*\([^)]{50,}", "Obfuscated code via fromCharCode"),
            (
                r"\\x[0-9a-f]{2}\\x[0-9a-f]{2}\\x[0-9a-f]{2}",
                "Hex-encoded strings - potential obfuscation",
            ),
            (r"atob\s*\([^)]{100,}", "Large base64 decode - potential hidden payload"),
            (
                r"WebSocket\s*\([^)]*['\"]ws[s]?://(?!localhost)",
                "External WebSocket connection",
            ),
            (
                r"crypto\.subtle",
                "Cryptographic operations - check for ransomware patterns",
            ),
            (r"navigator\.sendBeacon", "Data exfiltration via sendBeacon"),
            (
                r"XMLHttpRequest|fetch\s*\([^)]*['\"]https?://(?!localhost)",
                "External data transmission",
            ),
            (r"document\.cookie", "Cookie access - potential session theft"),
            (r"localStorage|sessionStorage", "Storage access - check context"),
            (
                r"window\.location\s*=\s*[^;]*\+",
                "Dynamic redirect - potential phishing",
            ),
            (
                r"coinhive|cryptoloot|coin-hive|minero|webminer",
                "Cryptocurrency miner detected",
            ),
            (
                r"keylogger|keystroke|onkeypress|onkeydown|onkeyup.*value",
                "Potential keylogger",
            ),
        ]

        # Malicious HTML patterns
        html_malware_patterns = [
            (
                r"<script[^>]*src\s*=\s*['\"]https?://(?!localhost|cdn\.|cdnjs\.|unpkg\.com|jsdelivr\.)",
                "External script from non-CDN",
            ),
            (
                r"<iframe[^>]*src\s*=\s*['\"]https?://",
                "External iframe - potential clickjacking",
            ),
            (r"<object[^>]*data\s*=", "Object embed - potential malicious content"),
            (r"<embed[^>]*src\s*=", "Embed tag - potential malicious content"),
            (r"javascript:", "javascript: URI - potential XSS"),
            (r"data:text/html", "data: URI with HTML - potential XSS"),
            (r"vbscript:", "vbscript: URI - potential code execution"),
            (r"on\w+\s*=\s*['\"]", "Inline event handler - review for XSS"),
        ]

        max_file_size = 10 * 1024 * 1024  # 10MB max per file

        for name in zf.namelist():
            ext = Path(name).suffix.lower()
            if ext not in code_extensions:
                continue

            try:
                info = zf.getinfo(name)
                if info.file_size > max_file_size:
                    findings.append(
                        SecurityFinding(
                            category="code_file_too_large",
                            description=f"Code file too large: {name}",
                            threat_level=ThreatLevel.MEDIUM,
                            details=f"File size: {info.file_size} bytes",
                        )
                    )
                    continue

                content = zf.read(name).decode("utf-8", errors="ignore")

                # Scan JavaScript files and inline scripts
                if ext in {".js"} or ext in {".html", ".htm", ".svg"}:
                    for pattern, desc in js_malware_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            findings.append(
                                SecurityFinding(
                                    category="malicious_code",
                                    description=f"Suspicious code pattern in {name}",
                                    threat_level=ThreatLevel.HIGH,
                                    details=desc,
                                    remediation="Review and remove malicious code",
                                )
                            )
                            break  # One finding per file is enough

                # Scan HTML files
                if ext in {".html", ".htm", ".svg"}:
                    for pattern, desc in html_malware_patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            findings.append(
                                SecurityFinding(
                                    category="malicious_html",
                                    description=f"Suspicious HTML pattern in {name}",
                                    threat_level=ThreatLevel.HIGH,
                                    details=desc,
                                    remediation="Review and sanitize HTML content",
                                )
                            )
                            break  # One finding per file is enough

            except Exception as e:
                logger.warning(f"Error scanning {name} in ZIP: {e}")

        return findings

    def _scan_standalone_code(
        self, content: bytes, filename: str
    ) -> List[SecurityFinding]:
        """Scan standalone code files (HTML, JS, CSS) for malicious patterns."""
        findings = []
        ext = Path(filename).suffix.lower()

        # Malicious JavaScript patterns (same as ZIP scanning)
        js_malware_patterns = [
            (r"\beval\s*\(", "eval() execution - potential code injection"),
            (r"\bFunction\s*\(", "Function constructor - potential code injection"),
            (
                r"document\.write\s*\([^)]*<script",
                "document.write with script - potential XSS",
            ),
            (
                r"innerHTML\s*=\s*[^;]*\+",
                "innerHTML with concatenation - potential XSS",
            ),
            (r"fromCharCode\s*\([^)]{50,}", "Obfuscated code via fromCharCode"),
            (
                r"\\x[0-9a-f]{2}\\x[0-9a-f]{2}\\x[0-9a-f]{2}",
                "Hex-encoded strings - potential obfuscation",
            ),
            (r"atob\s*\([^)]{100,}", "Large base64 decode - potential hidden payload"),
            (
                r"WebSocket\s*\([^)]*['\"]ws[s]?://(?!localhost)",
                "External WebSocket connection",
            ),
            (
                r"crypto\.subtle",
                "Cryptographic operations - check for ransomware patterns",
            ),
            (r"navigator\.sendBeacon", "Data exfiltration via sendBeacon"),
            (
                r"XMLHttpRequest|fetch\s*\([^)]*['\"]https?://(?!localhost)",
                "External data transmission",
            ),
            (r"document\.cookie", "Cookie access - potential session theft"),
            (r"localStorage|sessionStorage", "Storage access - check context"),
            (
                r"window\.location\s*=\s*[^;]*\+",
                "Dynamic redirect - potential phishing",
            ),
            (
                r"coinhive|cryptoloot|coin-hive|minero|webminer",
                "Cryptocurrency miner detected",
            ),
            (
                r"keylogger|keystroke|onkeypress|onkeydown|onkeyup.*value",
                "Potential keylogger",
            ),
        ]

        # Malicious HTML patterns
        html_malware_patterns = [
            (
                r"<script[^>]*src\s*=\s*['\"]https?://(?!localhost|cdn\.|cdnjs\.|unpkg\.com|jsdelivr\.)",
                "External script from non-CDN",
            ),
            (
                r"<iframe[^>]*src\s*=\s*['\"]https?://",
                "External iframe - potential clickjacking",
            ),
            (r"<object[^>]*data\s*=", "Object embed - potential malicious content"),
            (r"<embed[^>]*src\s*=", "Embed tag - potential malicious content"),
            (r"javascript:", "javascript: URI - potential XSS"),
            (r"data:text/html", "data: URI with HTML - potential XSS"),
            (r"vbscript:", "vbscript: URI - potential code execution"),
            (r"on\w+\s*=\s*['\"]", "Inline event handler - review for XSS"),
        ]

        # Malicious CSS patterns
        css_malware_patterns = [
            (
                r"@import\s+url\s*\(['\"]https?://(?!localhost|cdn\.|cdnjs\.|fonts\.googleapis\.)",
                "External CSS import from non-CDN",
            ),
            (r"expression\s*\(", "CSS expression - potential code execution (IE)"),
            (r"behavior\s*:", "CSS behavior property - potential code execution"),
            (r"-moz-binding\s*:", "Mozilla binding - potential code execution"),
            (r"url\s*\(['\"]?javascript:", "JavaScript in CSS url() - potential XSS"),
        ]

        try:
            text_content = content.decode("utf-8", errors="ignore")

            # Check file size (avoid processing huge files)
            if len(content) > 10 * 1024 * 1024:  # 10MB
                findings.append(
                    SecurityFinding(
                        category="code_file_too_large",
                        description=f"Code file too large: {filename}",
                        threat_level=ThreatLevel.MEDIUM,
                        details=f"File size: {len(content)} bytes",
                    )
                )
                return findings

            # Scan JavaScript files
            if ext == ".js":
                for pattern, desc in js_malware_patterns:
                    if re.search(pattern, text_content, re.IGNORECASE):
                        findings.append(
                            SecurityFinding(
                                category="malicious_code",
                                description=f"Suspicious code pattern in {filename}",
                                threat_level=ThreatLevel.HIGH,
                                details=desc,
                                remediation="Review and remove malicious code",
                            )
                        )
                        break  # One finding per file

            # Scan HTML files (both HTML-specific and JS patterns for inline scripts)
            elif ext in {".html", ".htm", ".svg"}:
                for pattern, desc in html_malware_patterns:
                    if re.search(pattern, text_content, re.IGNORECASE):
                        findings.append(
                            SecurityFinding(
                                category="malicious_html",
                                description=f"Suspicious HTML pattern in {filename}",
                                threat_level=ThreatLevel.HIGH,
                                details=desc,
                                remediation="Review and sanitize HTML content",
                            )
                        )
                        break

                # Also check for malicious JS in inline scripts
                if not findings:
                    for pattern, desc in js_malware_patterns:
                        if re.search(pattern, text_content, re.IGNORECASE):
                            findings.append(
                                SecurityFinding(
                                    category="malicious_code",
                                    description=f"Suspicious inline script in {filename}",
                                    threat_level=ThreatLevel.HIGH,
                                    details=desc,
                                    remediation="Review and remove malicious code",
                                )
                            )
                            break

            # Scan CSS files
            elif ext == ".css":
                for pattern, desc in css_malware_patterns:
                    if re.search(pattern, text_content, re.IGNORECASE):
                        findings.append(
                            SecurityFinding(
                                category="malicious_css",
                                description=f"Suspicious CSS pattern in {filename}",
                                threat_level=ThreatLevel.HIGH,
                                details=desc,
                                remediation="Review and sanitize CSS content",
                            )
                        )
                        break

        except Exception as e:
            logger.warning(f"Error scanning standalone code file {filename}: {e}")

        return findings

    async def _analyze_office_macros(
        self, content: bytes, ext: str
    ) -> List[SecurityFinding]:
        """Analyze Office Open XML documents for macros using oletools."""
        findings = []

        try:
            # Try to import oletools
            from oletools.olevba import VBA_Parser, detect_vba_macros

            # Check for VBA macros
            vba_detected = detect_vba_macros(content)

            if vba_detected:
                findings.append(
                    SecurityFinding(
                        category="office_macros_detected",
                        description="Document contains VBA macros",
                        threat_level=ThreatLevel.HIGH,
                        details="Macros can execute arbitrary code",
                        remediation="Remove macros or verify document source",
                    )
                )

                # Analyze macro content
                try:
                    vba_parser = VBA_Parser(filename=f"document{ext}", data=content)

                    if vba_parser.detect_vba_macros():
                        for (
                            filename,
                            stream_path,
                            vba_filename,
                            vba_code,
                        ) in vba_parser.extract_macros():
                            # Check for suspicious VBA patterns
                            suspicious_patterns = [
                                (r"Shell\s*\(", "Shell command execution"),
                                (r"CreateObject\s*\(", "COM object creation"),
                                (r"WScript\.Shell", "Windows Script Host"),
                                (r"PowerShell", "PowerShell execution"),
                                (r"cmd\.exe", "Command prompt execution"),
                                (r"Environ\s*\(", "Environment variable access"),
                                (r"DownloadFile", "File download"),
                                (r"URLDownloadToFile", "URL file download"),
                                (r"ADODB\.Stream", "Binary stream operations"),
                                (r"RegWrite|RegDelete", "Registry modification"),
                            ]

                            for pattern, desc in suspicious_patterns:
                                if re.search(pattern, vba_code, re.IGNORECASE):
                                    findings.append(
                                        SecurityFinding(
                                            category="office_macro_suspicious",
                                            description=f"Macro contains suspicious code: {desc}",
                                            threat_level=ThreatLevel.CRITICAL,
                                            details=f"Found in {vba_filename}",
                                            remediation="Do not open this document",
                                        )
                                    )

                    vba_parser.close()

                except Exception as e:
                    logger.warning(f"Macro analysis error: {e}")

        except ImportError:
            # oletools not installed - log warning but continue
            logger.warning("oletools not installed, skipping macro analysis")
            findings.append(
                SecurityFinding(
                    category="macro_check_skipped",
                    description="Macro analysis unavailable (oletools not installed)",
                    threat_level=ThreatLevel.LOW,
                    details="Install oletools for full security scanning",
                )
            )
        except Exception as e:
            logger.warning(f"Office macro analysis error: {e}")

        return findings

    async def _analyze_ole_macros(self, content: bytes) -> List[SecurityFinding]:
        """Analyze legacy Office (OLE) documents for macros."""
        findings = []

        try:
            from oletools.olevba import VBA_Parser

            vba_parser = VBA_Parser(filename="document.doc", data=content)

            if vba_parser.detect_vba_macros():
                findings.append(
                    SecurityFinding(
                        category="office_macros_detected",
                        description="Legacy Office document contains VBA macros",
                        threat_level=ThreatLevel.HIGH,
                        details="Legacy formats (.doc, .xls, .ppt) with macros are high risk",
                        remediation="Convert to modern format (.docx, .xlsx, .pptx) without macros",
                    )
                )

                # Additional analysis for OLE format
                for (
                    filename,
                    stream_path,
                    vba_filename,
                    vba_code,
                ) in vba_parser.extract_macros():
                    # Check for auto-execution
                    auto_exec_patterns = [
                        r"Auto_?Open",
                        r"Auto_?Close",
                        r"Auto_?Exec",
                        r"Document_?Open",
                        r"Workbook_?Open",
                    ]

                    for pattern in auto_exec_patterns:
                        if re.search(pattern, vba_code, re.IGNORECASE):
                            findings.append(
                                SecurityFinding(
                                    category="office_macro_autoexec",
                                    description="Macro has auto-execution trigger",
                                    threat_level=ThreatLevel.CRITICAL,
                                    details=f"Found auto-exec pattern in {vba_filename}",
                                    remediation="Do not open this document",
                                )
                            )
                            break

            vba_parser.close()

        except ImportError:
            logger.warning("oletools not installed, skipping OLE macro analysis")
        except Exception as e:
            logger.warning(f"OLE macro analysis error: {e}")

        return findings

    def _check_prompt_injection(
        self, content: bytes, file_type: str
    ) -> List[SecurityFinding]:
        """Check for prompt injection patterns in document text."""
        findings = []

        try:
            # Extract text based on file type
            text = ""

            if file_type == "pdf":
                # Basic text extraction from PDF
                text = content.decode("latin-1", errors="ignore")
            elif file_type == "zip_archive":
                # Extract text from OOXML documents
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        for name in zf.namelist():
                            if name.endswith(".xml"):
                                xml_content = zf.read(name).decode(
                                    "utf-8", errors="ignore"
                                )
                                # Strip XML tags for text content
                                text += re.sub(r"<[^>]+>", " ", xml_content)
                except Exception:
                    pass
            else:
                text = content.decode("utf-8", errors="ignore")

            # Check for prompt injection patterns
            for pattern in self.prompt_injection_patterns:
                matches = pattern.findall(text)
                if matches:
                    findings.append(
                        SecurityFinding(
                            category="prompt_injection",
                            description="Document contains potential prompt injection",
                            threat_level=ThreatLevel.HIGH,
                            details=f"Pattern matched: {matches[0][:50]}...",
                            remediation="Review document content for malicious instructions",
                        )
                    )
                    break  # One finding is enough

        except Exception as e:
            logger.warning(f"Prompt injection check error: {e}")

        return findings


# Convenience functions


async def validate_document(
    filename: str, content: Union[bytes, BinaryIO], **kwargs
) -> ValidationResult:
    """
    Validate a document for security threats.

    Args:
        filename: Original filename
        content: File content
        **kwargs: Additional options for DocumentValidator

    Returns:
        ValidationResult
    """
    validator = DocumentValidator(**kwargs)
    return await validator.validate(filename, content)


async def sanitize_document(
    filename: str,
    content: bytes,
    output_path: Optional[str] = None,
) -> Tuple[bytes, ValidationResult]:
    """
    Sanitize a document by removing potentially dangerous elements.

    Args:
        filename: Original filename
        content: File content
        output_path: Optional path to save sanitized document

    Returns:
        Tuple of (sanitized_content, validation_result)
    """
    validator = DocumentValidator()
    result = await validator.validate(filename, content)

    ext = Path(filename).suffix.lower()
    sanitized_content = content

    # Sanitize based on file type
    if result.file_type == "pdf":
        sanitized_content = await _sanitize_pdf(content)
    elif result.file_type == "zip_archive" and ext in [".docx", ".xlsx", ".pptx"]:
        sanitized_content = await _sanitize_ooxml(content)

    result.sanitized = True

    if output_path:
        Path(output_path).write_bytes(sanitized_content)
        result.sanitized_path = output_path

    return sanitized_content, result


async def _sanitize_pdf(content: bytes) -> bytes:
    """
    Sanitize PDF by removing JavaScript and other active content.

    Note: For production, consider using pikepdf or qpdf for proper sanitization.
    This is a basic implementation that removes common threats.
    """
    try:
        # Basic sanitization: remove JavaScript objects
        # For production, use a proper PDF library
        pdf_text = content.decode("latin-1", errors="ignore")

        # Remove JavaScript
        pdf_text = re.sub(r"/JavaScript\s*<<[^>]*>>", "", pdf_text)
        pdf_text = re.sub(r"/JS\s*\([^)]*\)", "", pdf_text)

        # Remove OpenAction
        pdf_text = re.sub(r"/OpenAction\s*<<[^>]*>>", "", pdf_text)
        pdf_text = re.sub(r"/OpenAction\s*\d+\s+\d+\s+R", "", pdf_text)

        # Remove Launch actions
        pdf_text = re.sub(r"/Launch\s*<<[^>]*>>", "", pdf_text)

        return pdf_text.encode("latin-1")

    except Exception as e:
        logger.warning(f"PDF sanitization error: {e}")
        return content


async def _sanitize_ooxml(content: bytes) -> bytes:
    """
    Sanitize Office Open XML by removing macros and external references.
    """
    try:
        sanitized_buffer = io.BytesIO()

        with zipfile.ZipFile(io.BytesIO(content), "r") as zf_in:
            with zipfile.ZipFile(sanitized_buffer, "w", zipfile.ZIP_DEFLATED) as zf_out:
                for item in zf_in.namelist():
                    # Skip macro-related files
                    skip_patterns = [
                        "vbaProject.bin",
                        "vbaData.xml",
                        ".bin",  # Skip all binary files (potential macros)
                        "activeX",
                        "embeddings",
                    ]

                    should_skip = any(
                        pattern in item.lower() for pattern in skip_patterns
                    )

                    if not should_skip:
                        data = zf_in.read(item)

                        # If XML, sanitize external references
                        if item.endswith(".xml") or item.endswith(".rels"):
                            data = _sanitize_xml_content(data)

                        zf_out.writestr(item, data)

        return sanitized_buffer.getvalue()

    except Exception as e:
        logger.warning(f"OOXML sanitization error: {e}")
        return content


def _sanitize_xml_content(content: bytes) -> bytes:
    """Sanitize XML content by removing external references."""
    try:
        text = content.decode("utf-8")

        # Remove external entity declarations
        text = re.sub(r"<!ENTITY\s+[^>]+>", "", text)

        # Remove external references in relationships
        text = re.sub(
            r'TargetMode\s*=\s*"External"[^>]*>', 'TargetMode="Internal">', text
        )

        return text.encode("utf-8")

    except Exception:
        return content
