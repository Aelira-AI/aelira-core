"""Font Unicode (ToUnicode CMap) fixer for PDF accessibility.

Fonts without a /ToUnicode CMap cannot be read by screen readers or
copy-pasted correctly. This fixer builds /ToUnicode CMap streams from the
font's /Encoding /Differences array where available, and flags fonts that
require manual intervention where they are not.

WCAG 1.3.1 (Info and Relationships): Text must be extractable and
machine-readable so that assistive technologies can present it correctly.

PDF/UA Matterhorn Protocol checkpoint 14-003: All non-standard glyphs must
be mapped via /ToUnicode or ActualText.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, String

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None
    Array = None
    Dictionary = None
    Name = None
    String = None

from .base import IssueCategory, RemediationIssue
from .confidence import ConfidenceCalculator, FixMethod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Adobe Glyph List subset — maps glyph names to Unicode codepoints.
# Covers Latin uppercase/lowercase, digits, common punctuation, ligatures,
# dashes, and typographic quotes.
# ---------------------------------------------------------------------------
ADOBE_GLYPH_LIST: Dict[str, int] = {
    # Uppercase Latin
    "A": 0x0041,
    "B": 0x0042,
    "C": 0x0043,
    "D": 0x0044,
    "E": 0x0045,
    "F": 0x0046,
    "G": 0x0047,
    "H": 0x0048,
    "I": 0x0049,
    "J": 0x004A,
    "K": 0x004B,
    "L": 0x004C,
    "M": 0x004D,
    "N": 0x004E,
    "O": 0x004F,
    "P": 0x0050,
    "Q": 0x0051,
    "R": 0x0052,
    "S": 0x0053,
    "T": 0x0054,
    "U": 0x0055,
    "V": 0x0056,
    "W": 0x0057,
    "X": 0x0058,
    "Y": 0x0059,
    "Z": 0x005A,
    # Lowercase Latin
    "a": 0x0061,
    "b": 0x0062,
    "c": 0x0063,
    "d": 0x0064,
    "e": 0x0065,
    "f": 0x0066,
    "g": 0x0067,
    "h": 0x0068,
    "i": 0x0069,
    "j": 0x006A,
    "k": 0x006B,
    "l": 0x006C,
    "m": 0x006D,
    "n": 0x006E,
    "o": 0x006F,
    "p": 0x0070,
    "q": 0x0071,
    "r": 0x0072,
    "s": 0x0073,
    "t": 0x0074,
    "u": 0x0075,
    "v": 0x0076,
    "w": 0x0077,
    "x": 0x0078,
    "y": 0x0079,
    "z": 0x007A,
    # Digits
    "zero": 0x0030,
    "one": 0x0031,
    "two": 0x0032,
    "three": 0x0033,
    "four": 0x0034,
    "five": 0x0035,
    "six": 0x0036,
    "seven": 0x0037,
    "eight": 0x0038,
    "nine": 0x0039,
    # Common punctuation
    "space": 0x0020,
    "exclam": 0x0021,
    "quotedbl": 0x0022,
    "numbersign": 0x0023,
    "dollar": 0x0024,
    "percent": 0x0025,
    "ampersand": 0x0026,
    "quotesingle": 0x0027,
    "parenleft": 0x0028,
    "parenright": 0x0029,
    "asterisk": 0x002A,
    "plus": 0x002B,
    "comma": 0x002C,
    "hyphen": 0x002D,
    "period": 0x002E,
    "slash": 0x002F,
    "colon": 0x003A,
    "semicolon": 0x003B,
    "less": 0x003C,
    "equal": 0x003D,
    "greater": 0x003E,
    "question": 0x003F,
    "at": 0x0040,
    "bracketleft": 0x005B,
    "backslash": 0x005C,
    "bracketright": 0x005D,
    "asciicircum": 0x005E,
    "underscore": 0x005F,
    "grave": 0x0060,
    "braceleft": 0x007B,
    "bar": 0x007C,
    "braceright": 0x007D,
    "asciitilde": 0x007E,
    # Ligatures
    "fi": 0xFB01,
    "fl": 0xFB02,
    "ff": 0xFB00,
    "ffi": 0xFB03,
    "ffl": 0xFB04,
    # Dashes and bullets
    "endash": 0x2013,
    "emdash": 0x2014,
    "bullet": 0x2022,
    "periodcentered": 0x00B7,
    # Typographic quotes
    "quoteleft": 0x2018,
    "quoteright": 0x2019,
    "quotedblleft": 0x201C,
    "quotedblright": 0x201D,
    "quotesinglbase": 0x201A,
    "quotedblbase": 0x201E,
    # Other common glyphs
    "ellipsis": 0x2026,
    "dagger": 0x2020,
    "daggerdbl": 0x2021,
    "perthousand": 0x2030,
    "trademark": 0x2122,
    "copyright": 0x00A9,
    "registered": 0x00AE,
    "degree": 0x00B0,
    "plusminus": 0x00B1,
    "mu": 0x00B5,
    "paragraph": 0x00B6,
    "sterling": 0x00A3,
    "yen": 0x00A5,
    "Euro": 0x20AC,
    "euro": 0x20AC,
    "section": 0x00A7,
    "acute": 0x00B4,
    "cedilla": 0x00B8,
    "ordfeminine": 0x00AA,
    "ordmasculine": 0x00BA,
    "guillemotleft": 0x00AB,
    "guillemotright": 0x00BB,
    "guilsinglleft": 0x2039,
    "guilsinglright": 0x203A,
    # Accented Latin (a selection)
    "Agrave": 0x00C0,
    "Aacute": 0x00C1,
    "Acircumflex": 0x00C2,
    "Atilde": 0x00C3,
    "Adieresis": 0x00C4,
    "Aring": 0x00C5,
    "AE": 0x00C6,
    "Ccedilla": 0x00C7,
    "Egrave": 0x00C8,
    "Eacute": 0x00C9,
    "Ecircumflex": 0x00CA,
    "Edieresis": 0x00CB,
    "Igrave": 0x00CC,
    "Iacute": 0x00CD,
    "Icircumflex": 0x00CE,
    "Idieresis": 0x00CF,
    "Eth": 0x00D0,
    "Ntilde": 0x00D1,
    "Ograve": 0x00D2,
    "Oacute": 0x00D3,
    "Ocircumflex": 0x00D4,
    "Otilde": 0x00D5,
    "Odieresis": 0x00D6,
    "OE": 0x0152,
    "Oslash": 0x00D8,
    "Ugrave": 0x00D9,
    "Uacute": 0x00DA,
    "Ucircumflex": 0x00DB,
    "Udieresis": 0x00DC,
    "Yacute": 0x00DD,
    "Thorn": 0x00DE,
    "germandbls": 0x00DF,
    "agrave": 0x00E0,
    "aacute": 0x00E1,
    "acircumflex": 0x00E2,
    "atilde": 0x00E3,
    "adieresis": 0x00E4,
    "aring": 0x00E5,
    "ae": 0x00E6,
    "ccedilla": 0x00E7,
    "egrave": 0x00E8,
    "eacute": 0x00E9,
    "ecircumflex": 0x00EA,
    "edieresis": 0x00EB,
    "igrave": 0x00EC,
    "iacute": 0x00ED,
    "icircumflex": 0x00EE,
    "idieresis": 0x00EF,
    "eth": 0x00F0,
    "ntilde": 0x00F1,
    "ograve": 0x00F2,
    "oacute": 0x00F3,
    "ocircumflex": 0x00F4,
    "otilde": 0x00F5,
    "odieresis": 0x00F6,
    "oe": 0x0153,
    "oslash": 0x00F8,
    "ugrave": 0x00F9,
    "uacute": 0x00FA,
    "ucircumflex": 0x00FB,
    "udieresis": 0x00FC,
    "yacute": 0x00FD,
    "thorn": 0x00FE,
    "ydieresis": 0x00FF,
    # Scaron / Zcaron for Central European
    "Scaron": 0x0160,
    "scaron": 0x0161,
    "Zcaron": 0x017D,
    "zcaron": 0x017E,
}


@dataclass
class FontFixResult:
    """Result of a single font /ToUnicode fix attempt.

    Attributes:
        success: Whether a CMap was successfully added.
        font_name: The /BaseFont name that was processed.
        mappings_added: Number of glyph->Unicode mappings written.
        confidence: Fix confidence (0.0 = manual required, >0 = auto-fixed).
        needs_review: Whether a human should verify the result.
        error: Error message if the fix failed unexpectedly.
    """

    success: bool
    font_name: str = ""
    mappings_added: int = 0
    confidence: float = 0.0
    needs_review: bool = True
    error: Optional[str] = None


class FontUnicodeFixer:
    """Add /ToUnicode CMap streams to fonts that lack them.

    Iterates every font referenced across all pages. For fonts that already
    have /ToUnicode the font is skipped. For fonts whose /Encoding has a
    /Differences array, a CMap is constructed from that array using the
    Adobe Glyph List. Fonts without /Differences are flagged as requiring
    manual remediation.

    Args:
        pdf: An open ``pikepdf.Pdf`` object.
        fitz_doc: An open ``fitz.Document`` object (PyMuPDF). Used for
            supplementary information; may be ``None``.
    """

    def __init__(self, pdf: "pikepdf.Pdf", fitz_doc=None) -> None:
        if not HAS_PIKEPDF:
            raise ImportError(
                "pikepdf is required for font Unicode fixing. "
                "Install with: pip install pikepdf"
            )
        self.pdf = pdf
        self.fitz_doc = fitz_doc
        self._confidence_calc = ConfidenceCalculator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self, issues: List[RemediationIssue]) -> List[FontFixResult]:
        """Fix /ToUnicode issues for the relevant issue types.

        Args:
            issues: List of ``RemediationIssue`` objects. Issues whose
                ``metadata["issue_type"]`` is ``"missing_tounicode"`` (or
                whose category is ``IssueCategory.STRUCTURE``) will trigger
                the full font scan. Other issues are ignored.

        Returns:
            List of ``FontFixResult`` objects, one per processed font.
            If no relevant issues are found, an empty list is returned.
        """
        # Decide whether we should act at all
        relevant = [
            iss
            for iss in issues
            if iss.metadata.get("issue_type") == "missing_tounicode"
            or iss.category == IssueCategory.STRUCTURE
        ]
        if not relevant:
            return []

        return self._fix_all_fonts()

    # ------------------------------------------------------------------
    # Core font scan
    # ------------------------------------------------------------------

    def _fix_all_fonts(self) -> List[FontFixResult]:
        """Scan all page font resources and add /ToUnicode where missing."""
        results: List[FontFixResult] = []
        seen_objgens: set = set()  # Avoid processing the same indirect object twice

        for page in self.pdf.pages:
            page_obj = page.obj
            resources = page_obj.get(Name.Resources)
            if resources is None:
                continue

            font_dict = resources.get(Name.Font)
            if font_dict is None:
                continue

            for _key in font_dict.keys():
                font_ref = font_dict[_key]

                # Resolve indirect references
                try:
                    font_obj = font_ref
                    # Check if it is an indirect object we've already processed
                    try:
                        objgen = font_ref.objgen
                        if objgen in seen_objgens:
                            continue
                        seen_objgens.add(objgen)
                    except AttributeError:
                        pass  # Not an indirect ref — process inline font

                    result = self._process_font(font_obj)
                    if result is not None:
                        results.append(result)
                except Exception as exc:
                    logger.warning(f"Error processing font {_key}: {exc}")
                    results.append(
                        FontFixResult(
                            success=False,
                            font_name=str(_key),
                            error=str(exc),
                        )
                    )

        # If no fonts required attention, produce a single success result
        if not results:
            results.append(
                FontFixResult(
                    success=True,
                    font_name="(none)",
                    confidence=1.0,
                    needs_review=False,
                )
            )

        return results

    def _process_font(self, font_obj) -> Optional[FontFixResult]:
        """Attempt to add /ToUnicode to a single font object.

        Returns:
            A ``FontFixResult``, or ``None`` if the font was skipped
            (e.g. already has /ToUnicode).
        """
        font_name = ""
        try:
            if Name.BaseFont in font_obj:
                font_name = str(font_obj[Name.BaseFont]).lstrip("/")
        except Exception:
            pass

        # Skip if /ToUnicode is already present
        if Name.ToUnicode in font_obj:
            return None

        # Try to build a CMap from /Encoding /Differences
        encoding = font_obj.get(Name.Encoding)
        differences = self._extract_differences(encoding)

        if differences:
            cmap_bytes, n_mappings = self._build_cmap_from_differences(
                differences, font_name
            )
            if cmap_bytes and n_mappings > 0:
                font_obj[Name.ToUnicode] = self.pdf.make_stream(cmap_bytes)
                confidence = self._confidence_calc.calculate(
                    FixMethod.RULE,
                    verified=True,
                    signal_strength=min(1.0, n_mappings / 32),
                    context_quality=0.9,
                )
                return FontFixResult(
                    success=True,
                    font_name=font_name,
                    mappings_added=n_mappings,
                    confidence=confidence,
                    needs_review=self._confidence_calc.needs_review(confidence),
                )
            else:
                # Differences present but no glyph names matched — flag manual
                return FontFixResult(
                    success=False,
                    font_name=font_name,
                    mappings_added=0,
                    confidence=0.0,
                    needs_review=True,
                    error="Glyph names in /Differences not found in Adobe Glyph List",
                )
        else:
            # No /Differences — cannot auto-generate CMap
            return FontFixResult(
                success=False,
                font_name=font_name,
                mappings_added=0,
                confidence=0.0,
                needs_review=True,
                error="No /Differences array; /ToUnicode requires manual addition",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_differences(self, encoding) -> Optional[List]:
        """Extract the /Differences array from an /Encoding object or name.

        Returns:
            The raw pikepdf Array (or Python list), or ``None`` if not present.
        """
        if encoding is None:
            return None

        # Encoding may be a Name (like /WinAnsiEncoding) — no Differences
        if isinstance(encoding, Name):
            return None

        try:
            diffs = encoding.get(Name.Differences)
        except AttributeError:
            return None

        if diffs is None:
            return None

        return diffs

    def _build_cmap_from_differences(self, differences, font_name: str) -> tuple:
        """Parse a /Differences array and generate a ToUnicode CMap.

        The /Differences format is:
            [start_code /GlyphName1 /GlyphName2 ... nextStartCode /NameA ...]

        Each integer establishes the base char code for the following
        glyph name(s). Code increments by 1 for each subsequent name until
        the next integer.

        Args:
            differences: pikepdf Array of mixed integers and Name objects.
            font_name: Font name string for the CMap header comment.

        Returns:
            Tuple of (cmap_bytes: bytes, n_mappings: int).
            ``cmap_bytes`` is empty bytes if no mappings were found.
        """
        code_to_unicode: Dict[int, int] = {}
        current_code = 0

        for item in differences:
            # Integer entries set the next character code
            if isinstance(item, int):
                current_code = int(item)
            elif isinstance(item, pikepdf.Object):
                # Try to get name string
                try:
                    raw = str(item)
                    glyph_name = raw.lstrip("/")
                except Exception:
                    current_code += 1
                    continue

                unicode_val = ADOBE_GLYPH_LIST.get(glyph_name)
                if unicode_val is not None:
                    code_to_unicode[current_code] = unicode_val
                else:
                    logger.debug(
                        f"Glyph '{glyph_name}' not in Adobe Glyph List — skipping"
                    )
                current_code += 1
            else:
                # Fallback for plain Python int/str items
                try:
                    val = int(item)
                    current_code = val
                except (TypeError, ValueError):
                    try:
                        glyph_name = str(item).lstrip("/")
                        unicode_val = ADOBE_GLYPH_LIST.get(glyph_name)
                        if unicode_val is not None:
                            code_to_unicode[current_code] = unicode_val
                    except Exception:
                        pass
                    current_code += 1

        if not code_to_unicode:
            return b"", 0

        cmap_bytes = self._generate_cmap(code_to_unicode, font_name)
        return cmap_bytes, len(code_to_unicode)

    def _generate_cmap(self, code_to_unicode: Dict[int, int], font_name: str) -> bytes:
        """Generate a PDF ToUnicode CMap stream.

        Produces a well-formed CMap conforming to PDF spec section 9.10.3,
        using the CIDInit/ProcSet boilerplate required by Acrobat and
        PDF/UA validators.

        Args:
            code_to_unicode: Mapping of character code -> Unicode codepoint.
            font_name: Name embedded in the CMap header for identification.

        Returns:
            CMap as UTF-8 encoded bytes.
        """
        lines = [
            "/CIDInit /ProcSet findresource begin",
            "12 dict begin",
            "begincmap",
            "/CIDSystemInfo",
            "<< /Registry (Adobe)",
            "   /Ordering (UCS)",
            "   /Supplement 0",
            ">> def",
            f"/CMapName /{font_name}-UCS2 def",
            "/CMapType 2 def",
            "1 begincodespacerange",
            "<00> <FF>",
            "endcodespacerange",
        ]

        # Emit bfchar entries in batches of up to 100 (PDF spec limit)
        batch_size = 100
        entries = sorted(code_to_unicode.items())
        for batch_start in range(0, len(entries), batch_size):
            batch = entries[batch_start : batch_start + batch_size]
            lines.append(f"{len(batch)} beginbfchar")
            for code, ucp in batch:
                lines.append(f"<{code:02X}> <{ucp:04X}>")
            lines.append("endbfchar")

        lines += [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]

        return "\n".join(lines).encode("utf-8")


__all__ = [
    "ADOBE_GLYPH_LIST",
    "FontFixResult",
    "FontUnicodeFixer",
]
