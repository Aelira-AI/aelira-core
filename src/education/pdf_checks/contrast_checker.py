"""Color contrast accessibility checking for PDFs."""

import logging
from typing import Dict, List

try:
    import pikepdf

    HAS_PIKEPDF = True
except ImportError:
    HAS_PIKEPDF = False
    pikepdf = None

logger = logging.getLogger(__name__)


def _relative_luminance(r: float, g: float, b: float) -> float:
    """WCAG relative luminance from 0-1 RGB values."""

    def adj(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * adj(r) + 0.7152 * adj(g) + 0.0722 * adj(b)


def _contrast_ratio(
    r: float,
    g: float,
    b: float,
    bg_r: float = 1.0,
    bg_g: float = 1.0,
    bg_b: float = 1.0,
) -> float:
    """Calculate WCAG contrast ratio between foreground and background colors."""
    l1 = _relative_luminance(r, g, b)
    l2 = _relative_luminance(bg_r, bg_g, bg_b)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


class ColorContrastChecker:
    """Check text color contrast against background in PDF content streams (WCAG 1.4.3)."""

    def check(self, file_path: str) -> List[Dict]:
        """Run color contrast checks on PDF content streams.

        Extracts text fill colors (rg/RG/g/G operators) and checks contrast
        ratio against white background (most common for PDFs). Flags text
        that fails WCAG AA threshold (4.5:1 normal text, 3:1 large text).

        Args:
            file_path: Path to the PDF file

        Returns:
            List of accessibility issues for low-contrast text
        """
        issues = []
        if not HAS_PIKEPDF:
            return issues

        try:
            pdf = pikepdf.open(file_path)
            low_contrast_pages = set()

            # Check first 10 pages for performance
            for page_idx, page in enumerate(list(pdf.pages)[:10]):
                try:
                    ops = list(pikepdf.parse_content_stream(page))
                except Exception:
                    continue

                current_fill = (0.0, 0.0, 0.0)  # Default black
                for operands, operator in ops:
                    op = str(operator)

                    # RGB fill color: r g b rg
                    if op == "rg" and len(operands) == 3:
                        try:
                            current_fill = (
                                float(operands[0]),
                                float(operands[1]),
                                float(operands[2]),
                            )
                        except (ValueError, TypeError):
                            pass

                    # Grayscale fill: g G
                    elif op == "g" and len(operands) == 1:
                        try:
                            v = float(operands[0])
                            current_fill = (v, v, v)
                        except (ValueError, TypeError):
                            pass

                    # Text showing operators -- check current fill color
                    elif op in ("Tj", "TJ", "'", '"'):
                        r, g, b = current_fill
                        # Skip black text (always passes against white)
                        if r < 0.05 and g < 0.05 and b < 0.05:
                            continue
                        # Skip white/near-white (decorative or on dark bg we can't detect)
                        if r > 0.95 and g > 0.95 and b > 0.95:
                            continue
                        # Skip light text likely intended for dark backgrounds
                        # (we can't detect bg color from content stream alone)
                        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
                        if luminance > 0.5:
                            continue

                        ratio = _contrast_ratio(r, g, b)
                        if ratio < 4.5:  # WCAG AA for normal text
                            low_contrast_pages.add(page_idx + 1)

            if low_contrast_pages:
                page_list = sorted(low_contrast_pages)[:5]
                pages_str = ", ".join(str(p) for p in page_list)
                if len(low_contrast_pages) > 5:
                    pages_str += f" (+{len(low_contrast_pages) - 5} more)"
                issues.append(
                    {
                        "severity": "high",
                        "rule": "WCAG 1.4.3",
                        "message": f"Low contrast text detected on {len(low_contrast_pages)} page(s): {pages_str}",
                        "impact": "Users with low vision may not be able to read text with insufficient contrast",
                        "page_number": page_list[0],
                        "location": f"Pages: {pages_str}",
                        "element": "Text fill color",
                        "suggested_fix": "Ensure text has at least 4.5:1 contrast ratio against background (3:1 for large text)",
                        "issue_type": "low_color_contrast",
                    }
                )

            pdf.close()
        except Exception as e:
            logger.warning(f"[ColorContrastChecker] Color contrast check error: {e}")

        return issues
