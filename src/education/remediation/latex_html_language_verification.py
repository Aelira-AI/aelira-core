"""Bounded saved-HTML observation for an explicitly authored LaTeX language fix.

This establishes only that the final candidate carries the authored document
language. It does not assess HTML accessibility, mathematical fidelity, or AT.
"""

from html.parser import HTMLParser
from pathlib import Path

from ..latex_diagnostics import ConversionDiagnostics
from ..latex_evidence import LatexRepresentationEvidence, digest
from ..latex_metadata import extract_metadata


class _RootLanguage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots = []
        self.first_tag = None
        self.closed_roots = 0
        self.outside_text = False

    def handle_starttag(self, tag, attrs):
        if self.first_tag is None:
            self.first_tag = tag
        if tag == "html":
            self.roots.append(attrs)

    def handle_endtag(self, tag):
        if tag == "html":
            self.closed_roots += 1

    def handle_data(self, data):
        if not self.roots or self.closed_roots:
            self.outside_text |= bool(data.strip())


def saved_html_has_authored_language(
    original_source: bytes,
    converted_source: bytes,
    candidate: bytes,
    diagnostics: ConversionDiagnostics | None,
    receipt: LatexRepresentationEvidence | None,
) -> bool:
    """Require an accepted, digest-bound candidate with one matching root lang."""
    try:
        authored = extract_metadata(original_source.decode("utf-8", errors="strict"))
        converted = extract_metadata(converted_source.decode("utf-8", errors="strict"))
        if not authored.language or authored.issues or converted.issues:
            return False
        if converted.language != authored.language:
            return False
        if diagnostics is None or receipt is None:
            return False
        if diagnostics.status != "accepted" or diagnostics.decision is None:
            return False
        if any(stage.blocked for stage in diagnostics.stages):
            return False
        if (
            diagnostics.source_sha256 != digest(converted_source)
            or diagnostics.decision.source_sha256 != diagnostics.source_sha256
            or diagnostics.candidate_sha256 != digest(candidate)
            or receipt.source_sha256 != digest(original_source)
            or receipt.candidate_sha256 != digest(candidate)
            or receipt.conversion.status != "completed"
            or receipt.conversion_diagnostics != diagnostics
        ):
            return False
        text = candidate.decode("utf-8", errors="strict")
        parser = _RootLanguage()
        parser.feed(text)
        parser.close()
        if (
            parser.first_tag != "html"
            or len(parser.roots) != 1
            or parser.closed_roots != 1
            or parser.outside_text
        ):
            return False
        root_attrs = parser.roots[0]
        languages = [value for name, value in root_attrs if name == "lang"]
        xml_languages = [value for name, value in root_attrs if name == "xml:lang"]
        return languages == [authored.language] and not xml_languages
    except (OSError, UnicodeError, ValueError):
        return False


def verify_saved_html_language_files(
    source_path: str,
    tex_path: str,
    html_path: str,
    diagnostics: ConversionDiagnostics | None,
    receipt: LatexRepresentationEvidence | None,
    loaded_source: bytes,
) -> bool:
    try:
        current_source = Path(source_path).read_bytes()
        if current_source != loaded_source:
            return False
        return saved_html_has_authored_language(
            current_source,
            Path(tex_path).read_bytes(),
            Path(html_path).read_bytes(),
            diagnostics,
            receipt,
        )
    except OSError:
        return False
