"""Source-bound PDF annotation descriptions, without AI or URL retrieval.

Grouped scanner findings cover the whole document. Mutations touch /Contents
only; actions, destinations, geometry, existing names and tag relations remain
intact. Partial repair retains a concrete unresolved grouped outcome.
"""

import math
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional
from urllib.parse import urlsplit

import pikepdf
import pymupdf as fitz

from .base import IssueCategory, RemediationIssue

VAGUE_PHRASES = {
    "click here",
    "here",
    "read more",
    "more",
    "link",
    "learn more",
    "details",
    "info",
    "this link",
    "this page",
    "go",
    "continue",
}
MAX_PAGES = 1000
MAX_ANNOTATIONS = 10000
MAX_LABEL = 2048


@dataclass
class FixResult:
    success: bool
    links_examined: int = 0
    links_fixed: int = 0
    fix_method: str = "rule"
    notes: Optional[str] = None
    error: Optional[str] = None


def _safe_text(value):
    if not isinstance(value, pikepdf.String):
        return ""
    text = str(value).strip()
    if len(text) > MAX_LABEL or any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in text
    ):
        return ""
    return text


class LinkFixer:
    def __init__(self, pdf: Any, fitz_doc: Any, ai_client: Optional[Any] = None):
        self._pdf = pdf
        self._fitz_doc = fitz_doc
        # Signature retained for callers. Generated labels are not source evidence.

    def fix(self, issues: List[RemediationIssue]) -> List[FixResult]:
        results = []
        for issue in issues:
            kind = issue.metadata.get("issue_type", "links_missing_alt")
            if issue.category != IssueCategory.LINK or kind not in {
                "links_missing_alt",
                "links_missing_contents",
                "vague_link_text",
            }:
                results.append(FixResult(False, error="link_unsupported_finding"))
                continue
            try:
                # These scanner types are document-wide aggregates; page_number=1
                # is a display location, not an annotation selection boundary.
                page_number = (
                    issue.metadata.get("page_number")
                    if issue.metadata.get("link_scope") == "page"
                    else None
                )
                results.append(
                    self._repair(page_number, vague=kind == "vague_link_text")
                )
            except (ValueError, TypeError, KeyError, pikepdf.PdfError):
                results.append(FixResult(False, error="link_malformed_source"))
        return results

    def _inventory(self, page_number):
        if len(self._pdf.pages) > MAX_PAGES:
            raise ValueError("link_page_limit")
        if page_number is not None and (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or not 1 <= page_number <= len(self._pdf.pages)
        ):
            raise ValueError("link_invalid_page")
        inventory = []
        seen = set()
        count = 0
        for index, page in enumerate(self._pdf.pages):
            annots = page.obj.get("/Annots", pikepdf.Array())
            if not isinstance(annots, pikepdf.Array):
                raise ValueError("link_malformed_annotations")
            count += len(annots)
            if count > MAX_ANNOTATIONS:
                raise ValueError("link_annotation_limit")
            for ordinal, annot in enumerate(annots):
                if not isinstance(annot, pikepdf.Dictionary):
                    raise ValueError("link_malformed_annotation")
                if annot.get("/Subtype") != pikepdf.Name.Link:
                    continue
                if not annot.is_indirect or annot.objgen in seen:
                    raise ValueError("link_ambiguous_annotation_ownership")
                seen.add(annot.objgen)
                if "/P" in annot and annot.P != page.obj:
                    raise ValueError("link_invalid_page_owner")
                if page_number is None or index == page_number - 1:
                    inventory.append((index, ordinal, annot))
        return inventory

    def _repair(self, page_number, *, vague=False):
        try:
            inventory = self._inventory(page_number)
        except ValueError as exc:
            return FixResult(False, error=str(exc))
        pending, refused = [], []
        for index, ordinal, annot in inventory:
            contents = annot.get("/Contents")
            # Preserve nonempty existing names, including names outside our new
            # label policy. Never replace one as a side effect of a missing-name fix.
            if contents is not None and str(contents).strip():
                if not vague or not self._is_vague(str(contents)):
                    continue
            elif vague:
                continue
            destination, reason = self._destination(annot)
            if reason:
                refused.append(f"page {index + 1} link {ordinal + 1}: {reason}")
                continue
            alt = _safe_text(annot.get("/Alt"))
            if (
                annot.get("/Alt") is not None
                and str(annot.get("/Alt")).strip()
                and not alt
            ):
                refused.append(
                    f"page {index + 1} link {ordinal + 1}: link_existing_name_requires_review"
                )
                continue
            label = alt
            if not label and index < len(self._fitz_doc):
                label = self._extract_text_under_rect(
                    self._fitz_doc[index], annot.get("/Rect")
                )
            if not label or self._is_vague(label):
                label = destination
            if not label:
                refused.append(
                    f"page {index + 1} link {ordinal + 1}: link_source_label_unavailable"
                )
                continue
            pending.append((annot, label))
        for annot, label in pending:
            annot.Contents = pikepdf.String(label)
        count = len(pending)
        return FixResult(
            success=bool(count) and not refused,
            links_examined=len(inventory),
            links_fixed=count,
            notes=f"Added source-bound descriptions to {count} link annotation(s).",
            error=(
                "; ".join(refused[:20])
                if refused
                else (None if count else "link_no_missing_supported_descriptions")
            ),
        )

    def _destination(self, annot):
        if "/AA" in annot:
            return "", "link_additional_action_requires_review"
        action = annot.get("/A")
        if action is not None:
            if (
                not isinstance(action, pikepdf.Dictionary)
                or "/Next" in action
                or "/Dest" in annot
            ):
                return "", "link_ambiguous_action"
            kind = action.get("/S")
            if kind == pikepdf.Name.URI:
                uri = _safe_text(action.get("/URI"))
                if (
                    not uri
                    or any(char.isspace() for char in uri)
                    or action.get("/IsMap", False)
                ):
                    return "", "link_unsupported_uri"
                try:
                    parts = urlsplit(uri)
                    if parts.scheme.lower() in {"https", "http"}:
                        # Relative URIs depend on catalog /URI /Base. Credentials
                        # and deceptive control text are not suitable names.
                        if not parts.hostname or parts.username or parts.password:
                            return "", "link_unsupported_uri"
                        _ = parts.port
                    elif parts.scheme.lower() == "mailto":
                        if not parts.path or "@" not in parts.path or parts.netloc:
                            return "", "link_unsupported_uri"
                    else:
                        return "", "link_unsupported_uri_scheme"
                except ValueError:
                    return "", "link_unsupported_uri"
                return uri, ""
            if kind != pikepdf.Name.GoTo:
                return "", "link_unsupported_action"
            dest = action.get("/D")
        else:
            dest = annot.get("/Dest")
        # An explicit intra-document destination can be described exactly.
        # Named trees, remote targets and malformed arrays require review.
        if not isinstance(dest, pikepdf.Array) or len(dest) < 2:
            return "", "link_unresolved_destination"
        target = dest[0]
        if not isinstance(target, pikepdf.Dictionary) or not target.is_indirect:
            return "", "link_unresolved_destination"
        page_numbers = [
            index + 1
            for index, page in enumerate(self._pdf.pages)
            if page.obj.objgen == target.objgen
        ]
        lengths = {
            "/Fit": 2,
            "/FitB": 2,
            "/FitH": 3,
            "/FitV": 3,
            "/FitBH": 3,
            "/FitBV": 3,
            "/XYZ": 5,
            "/FitR": 6,
        }
        if len(page_numbers) != 1 or lengths.get(str(dest[1])) != len(dest):
            return "", "link_unresolved_destination"
        for number in list(dest)[2:]:
            if number is None and str(dest[1]) != "/FitR":
                continue
            try:
                if isinstance(
                    number, (bool, pikepdf.String, pikepdf.Name)
                ) or not math.isfinite(float(number)):
                    return "", "link_unresolved_destination"
            except (TypeError, ValueError):
                return "", "link_unresolved_destination"
        return f"Go to page {page_numbers[0]}", ""

    def _extract_text_under_rect(self, fitz_page: Any, rect: Any) -> str:
        try:
            if rect is None or len(rect) != 4:
                return ""
            values = [float(value) for value in rect]
            if not all(math.isfinite(value) for value in values):
                return ""
            if values[0] >= values[2] or values[1] >= values[3]:
                return ""
            # This matrix accounts for the crop box and page coordinate origin.
            box = fitz.Rect(values) * fitz_page.transformation_matrix
            layout = fitz_page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            if any(
                tuple(line.get("dir", (1, 0))) != (1, 0)
                for block in layout.get("blocks", [])
                for line in block.get("lines", [])
                if fitz.Rect(line["bbox"]).intersects(box)
            ):
                return ""
            words = fitz_page.get_text("words")
            if len(words) > 20000:
                return ""
            selected = []
            for word in words:
                wb = fitz.Rect(word[:4])
                overlap = wb & box
                if overlap.is_empty:
                    continue
                if (
                    wb.x0 < box.x0 - 1
                    or wb.x1 > box.x1 + 1
                    or overlap.height < wb.height * 0.5
                ):
                    return ""
                selected.append(word)
            if not selected or len({(word[5], word[6]) for word in selected}) != 1:
                return ""
            label = " ".join(
                word[4] for word in sorted(selected, key=lambda word: word[0])
            )
            if any(
                unicodedata.bidirectional(char) in {"R", "AL", "AN"} for char in label
            ):
                return ""
            return _safe_text(pikepdf.String(label))
        except (ValueError, TypeError, RuntimeError):
            return ""

    @staticmethod
    def _is_vague(text: str) -> bool:
        return text.lower().strip() in VAGUE_PHRASES
