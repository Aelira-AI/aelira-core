"""Fail-closed visual round-trip verification for image-derived equations."""

from __future__ import annotations

import base64
import hashlib
import math
import multiprocessing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional
from xml.etree import ElementTree

import numpy as np
from PIL import Image, UnidentifiedImageError
from latex2mathml.converter import convert as latex_to_mathml

from .equation_image_source import ValidatedEquationImage

ASSET_DIR = Path(__file__).parent / "assets" / "math-font"
FONT_PATH = ASSET_DIR / "STIXTwoMath-Regular.ttf"
LICENSE_PATH = ASSET_DIR / "OFL.txt"
FONT_SHA256 = "562551b15b836e6e01d1b7350909baf3c8c8d83260c1190fbf4544333e6936de"
MATHML_NAMESPACE = "http://www.w3.org/1998/Math/MathML"
_ALLOWED_MATHML_TAGS = frozenset(
    {
        "math", "mi", "mn", "mo", "mrow", "mfrac", "msqrt", "mroot",
        "msup", "msub", "msubsup", "munder", "mover", "munderover",
        "mtable", "mtr", "mtd", "mspace", "mpadded", "mphantom",
        "mfenced", "menclose", "mmultiscripts", "mprescripts", "none",
    }
)
_COMMON_PASSIVE_ATTRIBUTES = frozenset(
    {
        "accent", "accentunder", "close", "columnalign", "columnspan",
        "columnspacing", "depth", "display", "displaystyle", "fence", "form",
        "height", "largeop", "lspace", "mathbackground", "mathcolor",
        "mathsize", "mathvariant", "maxsize", "minsize", "movablelimits",
        "notation", "open", "rowalign", "rowspan", "rowspacing", "rspace",
        "scriptlevel", "separators", "separator", "stretchy", "symmetric",
        "width",
    }
)
_PASSIVE_ATTRIBUTES_BY_TAG = {
    tag: _COMMON_PASSIVE_ATTRIBUTES for tag in _ALLOWED_MATHML_TAGS
}


class EquationVerificationRejected(ValueError):
    """Round-trip verification could not produce bounded trustworthy evidence."""


@dataclass(frozen=True)
class ComparisonMetrics:
    ink_iou: float
    pixel_similarity: float


@dataclass(frozen=True)
class VerifierConfig:
    renderer_version: str = "chromium-native-mathml-stix-v1"
    comparator_version: str = "binary-ink-canvas-v1"
    font_sha256: str = FONT_SHA256
    threshold_version: str = "printed-equation-v1"
    required_ink_iou: float = 0.90
    required_pixel_similarity: float = 0.98
    max_mathml_chars: int = 32_768
    max_mathml_nodes: int = 512
    max_mathml_depth: int = 32
    max_render_bytes: int = 5 * 1024 * 1024


@dataclass(frozen=True)
class EquationVerificationEvidence:
    passed: bool
    source_sha256: str
    rendered_sha256: str
    mathml_sha256: str
    renderer_version: str
    comparator_version: str
    font_sha256: str
    threshold_version: str
    ink_iou: float
    pixel_similarity: float
    required_ink_iou: float
    required_pixel_similarity: float


def _render_mathml_worker(
    mathml: str,
    font_data: str,
    connection,
    max_width: int,
    max_height: int,
) -> None:
    network_requests = 0
    try:
        from playwright.sync_api import sync_playwright

        html = f"""<!doctype html><meta charset=utf-8><style>
@font-face{{font-family:STIXPinned;src:url(data:font/ttf;base64,{font_data}) format('truetype')}}
html,body{{margin:0;background:#fff;color:#000}}
#formula{{display:inline-block;padding:24px;font:48px STIXPinned;line-height:1}}
</style><div id=formula>{mathml}</div>"""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    viewport={"width": 1024, "height": 512},
                    device_scale_factor=1,
                    color_scheme="light",
                )

                def block(route):
                    nonlocal network_requests
                    network_requests += 1
                    route.abort()

                page.route("**/*", block)
                page.set_content(html, wait_until="load", timeout=5_000)
                page.evaluate("document.fonts.ready")
                locator = page.locator("#formula")
                box = locator.bounding_box(timeout=5_000)
                if (
                    box is None
                    or box["width"] <= 0
                    or box["height"] <= 0
                    or box["width"] > max_width
                    or box["height"] > max_height
                ):
                    raise RuntimeError("renderer_dimension_limit")
                output = locator.screenshot(animations="disabled", timeout=5_000)
            finally:
                browser.close()
        connection.send(("ok", output, network_requests))
    except Exception:
        connection.send(("error", b"", network_requests))
    finally:
        connection.close()


class OfflineMathMLRenderer:
    """Render passive MathML under a killable deadline with pinned assets."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_width: int = 4096,
        max_height: int = 2048,
        worker_target: Callable[..., None] = _render_mathml_worker,
    ) -> None:
        self.network_requests = 0
        self.timeout_seconds = timeout_seconds
        self.max_width = max_width
        self.max_height = max_height
        self.worker_target = worker_target

    def render(self, mathml: str) -> bytes:
        font = FONT_PATH.read_bytes()
        if hashlib.sha256(font).hexdigest() != FONT_SHA256:
            raise RuntimeError("font_integrity_failed")
        font_data = base64.b64encode(font).decode("ascii")
        self.network_requests = 0
        context = multiprocessing.get_context("spawn")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(
            target=self.worker_target,
            args=(mathml, font_data, sending, self.max_width, self.max_height),
        )
        try:
            process.start()
            sending.close()
            process.join(self.timeout_seconds)
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join()
                raise RuntimeError("renderer_timeout")
            if not receiving.poll() or process.exitcode != 0:
                raise RuntimeError("renderer_failed")
            status, output, self.network_requests = receiving.recv()
            if status != "ok":
                raise RuntimeError("renderer_failed")
        finally:
            receiving.close()
            if process.is_alive():
                process.terminate()
                process.join()
        if self.network_requests:
            raise RuntimeError("renderer_network_attempt")
        return output


class EquationVerifier:
    """Apply a narrow deterministic visual filter; not an equivalence proof."""

    def __init__(
        self,
        *,
        converter: Callable[[str], str] = latex_to_mathml,
        renderer: Optional[Callable[[str], bytes]] = None,
        comparator: Optional[Callable[[bytes, bytes], ComparisonMetrics]] = None,
        config: Optional[VerifierConfig] = None,
    ) -> None:
        self.converter = converter
        self._renderer_object = None
        if renderer is None:
            self._renderer_object = OfflineMathMLRenderer()
            renderer = self._renderer_object.render
        self.renderer = renderer
        self.comparator = comparator or self._compare
        self.config = config or VerifierConfig()

    def verify(
        self, source: ValidatedEquationImage, latex: str
    ) -> EquationVerificationEvidence:
        try:
            mathml = self.converter(latex)
        except Exception:
            raise EquationVerificationRejected("conversion_failed") from None
        self._validate_mathml(mathml)
        try:
            rendered = self.renderer(mathml)
        except Exception:
            raise EquationVerificationRejected("renderer_failed") from None
        if not isinstance(rendered, bytes) or not rendered:
            raise EquationVerificationRejected("renderer_failed")
        if len(rendered) > self.config.max_render_bytes:
            raise EquationVerificationRejected("render_byte_limit")
        self._decode_ink(rendered)
        try:
            metrics = self.comparator(source.jpeg_bytes, rendered)
        except Exception:
            raise EquationVerificationRejected("comparison_failed") from None
        if not isinstance(metrics, ComparisonMetrics):
            raise EquationVerificationRejected("comparison_failed")
        if any(
            not math.isfinite(value) or value < 0.0 or value > 1.0
            for value in (metrics.ink_iou, metrics.pixel_similarity)
        ):
            raise EquationVerificationRejected("comparison_failed")
        passed = (
            metrics.ink_iou >= self.config.required_ink_iou
            and metrics.pixel_similarity >= self.config.required_pixel_similarity
        )
        return EquationVerificationEvidence(
            passed=passed,
            source_sha256=source.normalized_sha256,
            rendered_sha256=hashlib.sha256(rendered).hexdigest(),
            mathml_sha256=hashlib.sha256(mathml.encode("utf-8")).hexdigest(),
            renderer_version=self.config.renderer_version,
            comparator_version=self.config.comparator_version,
            font_sha256=self.config.font_sha256,
            threshold_version=self.config.threshold_version,
            ink_iou=metrics.ink_iou,
            pixel_similarity=metrics.pixel_similarity,
            required_ink_iou=self.config.required_ink_iou,
            required_pixel_similarity=self.config.required_pixel_similarity,
        )

    def _validate_mathml(self, mathml: Any) -> None:
        if not isinstance(mathml, str) or not mathml or len(mathml) > self.config.max_mathml_chars:
            raise EquationVerificationRejected("invalid_mathml")
        try:
            root = ElementTree.fromstring(mathml)
        except ElementTree.ParseError:
            raise EquationVerificationRejected("invalid_mathml") from None
        if self._local_name(root.tag) != "math":
            raise EquationVerificationRejected("invalid_mathml")
        nodes = 0
        text_chars = 0
        stack = [(root, 1)]
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > self.config.max_mathml_nodes or depth > self.config.max_mathml_depth:
                raise EquationVerificationRejected("invalid_mathml")
            namespace, name = self._expanded_name(node.tag)
            if namespace not in {None, MATHML_NAMESPACE}:
                raise EquationVerificationRejected("invalid_mathml")
            if name not in _ALLOWED_MATHML_TAGS or name == "mtext":
                raise EquationVerificationRejected("invalid_mathml")
            allowed_attributes = _PASSIVE_ATTRIBUTES_BY_TAG[name]
            for key, value in node.attrib.items():
                lowered = key.lower()
                if (
                    "}" in key
                    or ":" in key
                    or lowered not in allowed_attributes
                    or len(value) > 128
                    or not value.isprintable()
                    or "url(" in value.lower()
                    or "http:" in value.lower()
                    or "https:" in value.lower()
                    or "data:" in value.lower()
                ):
                    raise EquationVerificationRejected("invalid_mathml")
            text_chars += len(node.text or "") + len(node.tail or "")
            if text_chars > self.config.max_mathml_chars:
                raise EquationVerificationRejected("invalid_mathml")
            stack.extend((child, depth + 1) for child in list(node))
        if nodes <= 1:
            raise EquationVerificationRejected("invalid_mathml")

    @staticmethod
    def _local_name(tag: str) -> str:
        return EquationVerifier._expanded_name(tag)[1]

    @staticmethod
    def _expanded_name(tag: str) -> tuple[Optional[str], str]:
        if not tag.startswith("{"):
            if "{" in tag or "}" in tag:
                raise EquationVerificationRejected("invalid_mathml")
            return None, tag.lower()
        boundary = tag.find("}")
        local = tag[boundary + 1 :] if boundary > 1 else ""
        if boundary <= 1 or not local or "{" in local or "}" in local:
            raise EquationVerificationRejected("invalid_mathml")
        return tag[1:boundary], local.lower()

    def _compare(self, source: bytes, rendered: bytes) -> ComparisonMetrics:
        left = self._normalized_canvas(source)
        right = self._normalized_canvas(rendered)
        left_ink = left < 245
        right_ink = right < 245
        union = np.logical_or(left_ink, right_ink).sum()
        if union == 0:
            raise EquationVerificationRejected("blank_render")
        intersection = np.logical_and(left_ink, right_ink).sum()
        iou = float(intersection / union)
        similarity = float(
            1.0
            - np.abs(left.astype(np.int16) - right.astype(np.int16)).mean() / 255.0
        )
        return ComparisonMetrics(ink_iou=iou, pixel_similarity=similarity)

    def _normalized_canvas(self, payload: bytes) -> np.ndarray:
        ink = self._decode_ink(payload)
        points = np.argwhere(ink < 245)
        if not len(points):
            raise EquationVerificationRejected("blank_render")
        y0, x0 = points.min(axis=0)
        y1, x1 = points.max(axis=0) + 1
        cropped = Image.fromarray(ink[y0:y1, x0:x1], mode="L")
        cropped.thumbnail((480, 224), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (512, 256), 255)
        canvas.paste(
            cropped, ((512 - cropped.width) // 2, (256 - cropped.height) // 2)
        )
        return np.asarray(canvas, dtype=np.uint8)

    @staticmethod
    def _decode_ink(payload: bytes) -> np.ndarray:
        try:
            with Image.open(BytesIO(payload)) as image:
                if (
                    image.width <= 0
                    or image.height <= 0
                    or image.width * image.height > 25_000_000
                ):
                    raise EquationVerificationRejected("render_dimension_limit")
                image.load()
                return np.asarray(image.convert("L"), dtype=np.uint8)
        except EquationVerificationRejected:
            raise
        except (UnidentifiedImageError, EOFError, OSError, ValueError):
            raise EquationVerificationRejected("invalid_render") from None
