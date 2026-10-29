"""
Document Security Validator

Validates uploaded documents for security threats including:
- Malicious macros in Office documents
- Suspicious PDF elements (JavaScript, auto-actions)
- File type spoofing (magic byte validation)
- Zip bombs and archive attacks

Uses oletools for Office analysis and custom PDF inspection.
"""

import io
import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional, Set, Tuple, Union
import hashlib

from .active_content import (
    SanitizationError,
    find_active_content,
    rebuild_ooxml,
    rebuild_pdf,
)

logger = logging.getLogger(__name__)


class ThreatLevel(Enum):
    """Threat severity levels."""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Explicit severity ordering. Never compare the string values:
        lexicographic comparison ranks every level below "safe"."""
        return _THREAT_RANK[self]


_THREAT_RANK = {
    ThreatLevel.SAFE: 0,
    ThreatLevel.LOW: 1,
    ThreatLevel.MEDIUM: 2,
    ThreatLevel.HIGH: 3,
    ThreatLevel.CRITICAL: 4,
}


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
    # XHTML starts with an XML declaration
    ".html": ["html", "xml", "text", "unknown"],
    ".htm": ["html", "xml", "text", "unknown"],
    ".js": ["text", "unknown"],
    ".css": ["text", "unknown"],
    ".tex": ["latex", "text", "unknown"],
    ".zip": ["zip_archive"],
}

# Code file extensions that need malware scanning
CODE_FILE_EXTENSIONS = {".html", ".htm", ".js", ".css", ".svg"}

OOXML_OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"}
OFFICE_EXTENSIONS = OOXML_OFFICE_EXTENSIONS | {".doc", ".xls", ".ppt"}

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


# A PDF name runs to the next PDF whitespace (NUL, tab, LF, FF, CR, space) or
# delimiter. Python's \s is a different set, so the bytes are matched directly.
_PDF_NAME = re.compile(rb"/[^\x00\x09\x0a\x0c\x0d\x20()<>\[\]{}/%]*")
_MAX_PDF_OBJECTS = 200_000


def _pdf_names(content: bytes) -> List[str]:
    """Every PDF name in the raw bytes, with #hh escapes decoded.

    A name may spell any character as #hh, so /J#61vaScript is /JavaScript.
    """
    return [
        re.sub(
            rb"#([0-9A-Fa-f]{2})",
            lambda escape: bytes([int(escape.group(1), 16)]),
            match.group(0),
        ).decode("latin-1")
        for match in _PDF_NAME.finditer(content)
    ]


def _pdf_structural_keywords(content: bytes) -> Dict[str, int]:
    """Keywords found by parsing the PDF, including inside object streams.

    The byte scan cannot see into compressed object streams. This counts the
    keywords as dictionary keys and name values in every object. A PDF that
    cannot be parsed returns an empty count, and the byte scan still applies.
    """
    counts: Dict[str, int] = {}
    try:
        import pikepdf

        pdf = pikepdf.open(io.BytesIO(content))
    except Exception:
        return counts

    def add(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    def visit(obj, depth: int) -> None:
        if depth > 32:
            return
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            for key in obj.keys():
                if key in SUSPICIOUS_PDF_KEYWORDS:
                    add(key)
                value = obj[key]
                if isinstance(value, pikepdf.Name) and str(value) in (
                    SUSPICIOUS_PDF_KEYWORDS
                ):
                    add(str(value))
                if not getattr(value, "is_indirect", True):
                    visit(value, depth + 1)
        elif isinstance(obj, pikepdf.Array):
            for value in obj:
                if isinstance(value, pikepdf.Name) and str(value) in (
                    SUSPICIOUS_PDF_KEYWORDS
                ):
                    add(str(value))
                if not getattr(value, "is_indirect", True):
                    visit(value, depth + 1)

    with pdf:
        try:
            for index, obj in enumerate(pdf.objects):
                if index >= _MAX_PDF_OBJECTS:
                    break
                visit(obj, 0)
        except Exception:
            pass
    return counts


def _vba_call(name: str) -> str:
    """A call to ``name`` with or without parentheses, as VBA allows.

    Any use of the name counts, whatever its first argument starts with
    (``Shell "calc.exe"``, ``Shell(cmd)``, ``Shell .Value``, ``Shell [A1]``),
    except an assignment to it or the bare name at the end of a line. A longer
    identifier such as ``ShellSort`` does not match.
    """
    return rf"\b{name}(?![A-Za-z0-9_$])(?![ \t]*(?:=|$))"


# Calls in macro code that make a macro CRITICAL rather than HIGH. They are
# matched after comments and string contents are removed, so a word inside a
# message is not a call.
SUSPICIOUS_VBA_CALLS = [
    (_vba_call("Shell"), "Shell command execution"),
    (_vba_call("CreateObject"), "COM object creation"),
    (_vba_call(r"Environ\$?"), "Environment variable access"),
]

# Names in macro code, usually inside strings, that make a macro CRITICAL.
# They are matched after comments are removed.
SUSPICIOUS_VBA_PATTERNS = [
    (r"WScript\.Shell", "Windows Script Host"),
    (r"PowerShell", "PowerShell execution"),
    (r"cmd\.exe", "Command prompt execution"),
    (r"DownloadFile", "File download"),
    (r"URLDownloadToFile", "URL file download"),
    (r"ADODB\.Stream", "Binary stream operations"),
    (r"RegWrite|RegDelete", "Registry modification"),
]


def _vba_without_comments(code: str) -> str:
    """VBA source with ' and Rem comments removed, keeping string contents."""
    lines = []
    for line in code.splitlines():
        in_string = False
        for index, char in enumerate(line):
            if char == '"':
                in_string = not in_string
            elif char == "'" and not in_string:
                line = line[:index]
                break
        if re.match(r"\s*Rem\b", line, re.IGNORECASE):
            line = ""
        lines.append(line)
    return "\n".join(lines)


def _suspicious_vba_calls(code: str) -> List[str]:
    """Descriptions of the suspicious calls and names in VBA source."""
    code = _vba_without_comments(code)
    # Strings end on their line; an unbalanced quote must not hide the lines
    # after it.
    without_strings = re.sub(r'"(?:[^"\n]|"")*"', '""', code)
    found = [
        desc
        for pattern, desc in SUSPICIOUS_VBA_CALLS
        if re.search(pattern, without_strings, re.IGNORECASE | re.MULTILINE)
    ]
    found += [
        desc
        for pattern, desc in SUSPICIOUS_VBA_PATTERNS
        if re.search(pattern, code, re.IGNORECASE)
    ]
    return found


_MAX_PACKAGE_XML_BYTES = 4 * 1024 * 1024


def _vba_project_parts(content: bytes) -> Optional[Set[str]]:
    """Parts an Office Open XML package declares as a VBA project.

    A package finds its parts through [Content_Types].xml and the .rels
    relationships, not through file names, so this reads both and also keeps a
    part named vbaProject.bin in any folder. An empty set means the package
    declares none. None means the package structure could not be read, which
    callers must treat as possibly carrying a project.
    """
    from defusedxml import ElementTree

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except Exception:
        return set()  # not a package; the archive checks report that

    with zf:
        members = zf.namelist()
        found: Set[str] = {
            name
            for name in members
            if name.replace("\\", "/").rsplit("/", 1)[-1].lower() == "vbaproject.bin"
        }
        by_lower = {name.lower(): name for name in members}

        def read_xml(name):
            if zf.getinfo(name).file_size > _MAX_PACKAGE_XML_BYTES:
                raise ValueError("package XML too large")
            return ElementTree.fromstring(zf.read(name))

        try:
            content_types = by_lower.get("[content_types].xml")
            if content_types is not None:
                for element in read_xml(content_types):
                    kind = element.tag.rsplit("}", 1)[-1]
                    if "vbaproject" not in element.get("ContentType", "").lower():
                        continue
                    if kind == "Override":
                        found.add(element.get("PartName", "").lstrip("/"))
                    elif kind == "Default":
                        extension = "." + element.get("Extension", "").lower()
                        found.update(
                            name for name in members if name.lower().endswith(extension)
                        )

            for name in members:
                if not name.lower().endswith(".rels"):
                    continue
                # A .rels file describes the part beside its _rels folder.
                source_folder = posixpath.dirname(posixpath.dirname(name))
                for relationship in read_xml(name):
                    kind = relationship.get("Type", "").lower()
                    if not kind.endswith("/vbaproject"):
                        continue
                    if relationship.get("TargetMode", "").lower() == "external":
                        continue
                    target = relationship.get("Target", "")
                    if target.startswith("/"):
                        found.add(posixpath.normpath(target.lstrip("/")))
                    else:
                        found.add(
                            posixpath.normpath(posixpath.join(source_folder, target))
                        )
        except Exception:
            return None

    return found


def _declares_macros(content: bytes) -> bool:
    """True if [Content_Types].xml declares a macro-enabled or VBA project type."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            declared = zf.read("[Content_Types].xml").lower()
        return b"macroenabled" in declared or b"vbaproject" in declared
    except Exception:
        return False


class DocumentValidator:
    """
    Validates documents for security threats.

    The checks are structural: file size, magic bytes against the extension,
    archive shape, macros, active content and code. The validator does not
    screen document text for phrases. Wording cannot change what a document
    contains, and document text reaches a model only inside a delimiter it
    cannot close, so a phrase list would only rebuild ordinary documents.

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

    async def validate(
        self,
        filename: str,
        content: Union[bytes, BinaryIO],
    ) -> ValidationResult:
        """
        Validate a document for security threats.

        Args:
            filename: Original filename
            content: File content as bytes or file-like object

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
            if ext in OOXML_OFFICE_EXTENSIONS:
                macro_findings = await self._analyze_office_macros(content, ext)
                findings.extend(macro_findings)
                findings.extend(self._analyze_active_content(content))
        elif detected_type == "ole_compound":
            # Legacy Office format - always check for macros
            macro_findings = await self._analyze_ole_macros(content)
            findings.extend(macro_findings)

        # 4. Scan standalone code files for malicious patterns
        if ext in CODE_FILE_EXTENSIONS:
            code_findings = self._scan_standalone_code(content, filename)
            findings.extend(code_findings)

        # Determine overall threat level
        max_threat = ThreatLevel.SAFE
        for finding in findings:
            if finding.threat_level.rank > max_threat.rank:
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
            # Whole names, so /JS does not match /JSomething. The parsed count
            # can only add to the byte count, for keywords in object streams.
            names = _pdf_names(content)
            parsed = _pdf_structural_keywords(content)

            for keyword, (threat_level, description) in SUSPICIOUS_PDF_KEYWORDS.items():
                count = max(names.count(keyword), parsed.get(keyword, 0))
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
            from oletools.olevba import VBA_Parser
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
            return findings

        vba_parser = None
        vba_parts = _vba_project_parts(content)
        # None means the package structure was unreadable: possibly a project.
        carries_vba_project = vba_parts is None or bool(vba_parts)
        try:
            vba_parser = VBA_Parser(filename=f"document{ext}", data=content)

            if not vba_parser.detect_vba_macros():
                if carries_vba_project:
                    # The package declares a VBA project, or its structure
                    # cannot be read, and oletools found no macros. Office may
                    # still read it, so do not call the file clean.
                    findings.append(
                        SecurityFinding(
                            category="macro_check_failed",
                            description="Document has a VBA project that could not be read",
                            threat_level=ThreatLevel.HIGH,
                            details="The package declares a VBA project or cannot be read, and no macros were found",
                            remediation="Remove the VBA project or verify document source",
                        )
                    )
                return findings

            findings.append(
                SecurityFinding(
                    category="office_macros_detected",
                    description="Document contains VBA macros",
                    threat_level=ThreatLevel.HIGH,
                    details="Macros can execute arbitrary code",
                    remediation="Remove macros or verify document source",
                )
            )

            # Analyze macro content. A failure here is reported, because the
            # suspicious-code check did not run. It stays MEDIUM because the
            # HIGH macro finding above already makes the upload path rebuild
            # the file without the VBA project.
            try:
                for (
                    filename,
                    stream_path,
                    vba_filename,
                    vba_code,
                ) in vba_parser.extract_macros():
                    for desc in _suspicious_vba_calls(vba_code or ""):
                        findings.append(
                            SecurityFinding(
                                category="office_macro_suspicious",
                                description=f"Macro contains suspicious code: {desc}",
                                threat_level=ThreatLevel.CRITICAL,
                                details=f"Found in {vba_filename}",
                                remediation="Do not open this document",
                            )
                        )
            except Exception as e:
                logger.warning(f"Macro content analysis error ({type(e).__name__})")
                findings.append(
                    SecurityFinding(
                        category="macro_analysis_failed",
                        description="Macro code could not be analyzed",
                        threat_level=ThreatLevel.MEDIUM,
                        details="The macros were detected but their code could not be read",
                    )
                )

        except Exception as e:
            logger.warning(f"Office macro analysis error ({type(e).__name__})")
            # Fail closed for a file that carries a VBA project, so a file built
            # to make the parser raise is still rebuilt without the project.
            # Without one, an ordinary document with embedded objects must not
            # be rebuilt, because that drops the objects (#856).
            carries_vba = carries_vba_project or _declares_macros(content)
            findings.append(
                SecurityFinding(
                    category="macro_check_failed",
                    description="Macro analysis failed",
                    threat_level=(
                        ThreatLevel.HIGH if carries_vba else ThreatLevel.MEDIUM
                    ),
                    details="The document could not be checked for macros",
                    remediation="Review the document manually",
                )
            )
        finally:
            if vba_parser is not None:
                try:
                    vba_parser.close()
                except Exception:
                    pass

        return findings

    def _analyze_active_content(self, content: bytes) -> List[SecurityFinding]:
        """Report Excel 4 macro sheets and content loaded from outside the file."""
        found = find_active_content(content)
        if found is None:
            return [
                SecurityFinding(
                    category="office_package_unreadable",
                    description="Document package structure could not be read",
                    threat_level=ThreatLevel.HIGH,
                    details="Its content types or relationships could not be parsed",
                    remediation="Save the document again from Office and upload that copy",
                )
            ]
        findings = []
        if found.macro_sheets:
            findings.append(
                SecurityFinding(
                    category="office_xlm_macros",
                    description="Workbook contains Excel 4 (XLM) macro sheets",
                    threat_level=ThreatLevel.HIGH,
                    details=f"{len(found.macro_sheets)} macro sheet(s)",
                    remediation="Remove the macro sheets or verify document source",
                )
            )
        if found.external_loaders:
            findings.append(
                SecurityFinding(
                    category="office_external_content",
                    description="Document loads content from outside the file when opened",
                    threat_level=ThreatLevel.HIGH,
                    details="External relationship types: "
                    + ", ".join(sorted(found.external_loaders)),
                    remediation="Remove the attached template or linked objects",
                )
            )
        return findings

    async def _analyze_ole_macros(self, content: bytes) -> List[SecurityFinding]:
        """Analyze legacy Office (OLE) documents for macros."""
        findings = []
        vba_parser = None

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

        except ImportError:
            logger.warning("oletools not installed, skipping OLE macro analysis")
        except Exception as e:
            logger.warning(f"OLE macro analysis error ({type(e).__name__})")
            # Fail closed: a legacy file that cannot be checked may hold macros.
            # HIGH legacy files are not yet rebuilt by sanitize_document, see
            # #863, so for now this keeps the finding and the recorded level.
            findings.append(
                SecurityFinding(
                    category="macro_check_failed",
                    description="Macro analysis failed",
                    threat_level=ThreatLevel.HIGH,
                    details="The document could not be checked for macros",
                    remediation="Review the document manually",
                )
            )
        finally:
            if vba_parser is not None:
                try:
                    vba_parser.close()
                except Exception:
                    pass

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


# Office formats the upload routes accept and the rebuild supports.
REBUILDABLE_OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}

# HIGH findings that a code file or code archive may carry and still be
# processed as uploaded: patterns in its code, which the scanner runs in an
# isolated browser. Any other HIGH finding needs a rebuild.
CODE_PATTERN_CATEGORIES = {"malicious_code", "malicious_html", "malicious_css"}


def _code_file_without_rebuild(ext: str, result: ValidationResult) -> bool:
    if ext in CODE_FILE_EXTENSIONS:
        expected = set(ALLOWED_EXTENSIONS.get(ext, ["html", "xml", "text", "unknown"]))
    elif ext == ".zip":
        expected = {"zip_archive"}
    else:
        return False
    high = {
        f.category
        for f in result.findings
        if f.threat_level.rank >= ThreatLevel.HIGH.rank
    }
    return result.file_type in expected and high <= CODE_PATTERN_CATEGORIES


async def sanitize_document(
    filename: str,
    content: bytes,
    output_path: Optional[str] = None,
) -> Tuple[bytes, ValidationResult]:
    """
    Rebuild a document without its active content.

    PDF and Office Open XML files are rebuilt and validated again, and the
    rebuilt copy is returned with ``sanitized`` set. A legacy Office file, an
    Office file in another format, or a rebuild that cannot be read or is
    still rated HIGH raises SanitizationError; the original bytes are never
    returned as if they were sanitised. Code and text files have no rebuild:
    they are returned unchanged with ``sanitized`` False.

    Args:
        filename: Original filename
        content: File content
        output_path: Optional path to save the rebuilt document

    Returns:
        Tuple of (content, validation_result)

    Raises:
        SanitizationError: if the file needs a rebuild that cannot be made
    """
    validator = DocumentValidator()
    result = await validator.validate(filename, content)
    ext = Path(filename).suffix.lower()

    if result.file_type == "pdf":
        rebuild = rebuild_pdf
    elif result.file_type == "zip_archive" and ext in REBUILDABLE_OFFICE_EXTENSIONS:
        rebuild = rebuild_ooxml
    elif _code_file_without_rebuild(ext, result):
        return content, result
    else:
        raise SanitizationError("not_rebuildable")

    try:
        rebuilt = rebuild(content)
    except SanitizationError:
        raise
    except Exception as exc:
        raise SanitizationError("rebuild_failed") from exc

    rebuilt_result = await validator.validate(filename, rebuilt)
    if rebuilt_result.threat_level.rank >= ThreatLevel.HIGH.rank:
        raise SanitizationError("rebuild_still_high")
    rebuilt_result.sanitized = True

    if output_path:
        Path(output_path).write_bytes(rebuilt)
        rebuilt_result.sanitized_path = output_path

    return rebuilt, rebuilt_result
