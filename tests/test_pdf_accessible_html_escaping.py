"""Security contracts for the PDF accessible-HTML fallback."""

import base64
from html.parser import HTMLParser
from io import BytesIO

import fitz
import pytest
from PIL import Image, UnidentifiedImageError

from src.education.remediation import pdf_remediator as pdf_remediator_module
from src.education.remediation.pdf_remediator import (
    PdfRemediator,
    _is_verified_image,
    _sanitize_pymupdf_html_fragment,
)

_ALLOWED_FRAGMENT_TAGS = {
    "a",
    "b",
    "br",
    "div",
    "em",
    "i",
    "img",
    "p",
    "span",
    "strong",
    "sub",
    "sup",
}
_ALLOWED_FRAGMENT_ATTRIBUTES = {
    "a": {"href", "id"},
    "img": {"alt", "id", "src", "title"},
}


class _HtmlRecorder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.start_tags: list[str] = []
        self.attributes: list[tuple[str, dict[str, str | None]]] = []
        self.end_tags: list[str] = []
        self.image_attributes: list[dict[str, str | None]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.start_tags.append(tag)
        self.attributes.append((tag, dict(attrs)))
        if tag == "img":
            self.image_attributes.append(dict(attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        self.end_tags.append(tag)


def _assert_fragment_contract(rendered: str) -> _HtmlRecorder:
    parser = _HtmlRecorder()
    parser.feed(rendered)
    parser.close()

    assert set(parser.start_tags) <= _ALLOWED_FRAGMENT_TAGS
    assert set(parser.end_tags) <= _ALLOWED_FRAGMENT_TAGS - {"br", "img"}
    for tag, attributes in parser.attributes:
        allowed = _ALLOWED_FRAGMENT_ATTRIBUTES.get(tag, {"id"})
        assert set(attributes) <= allowed
        if "id" in attributes:
            value = attributes["id"]
            assert value is not None
            assert value.startswith("page") and value[4:].isdigit()
        if "href" in attributes:
            href = attributes["href"] or ""
            assert href.startswith(("http://", "https://", "mailto:", "#"))
        if "src" in attributes:
            src = attributes["src"] or ""
            assert src.startswith(("data:image/png;base64,", "data:image/jpeg;base64,"))

    return parser


class _MetadataOnlyDocument:
    def __init__(self, title: str) -> None:
        self.metadata = {"title": title}

    def __len__(self) -> int:
        return 0


def _write_pdf(
    path, *, title: str = "", text: str = "", include_image: bool = False
) -> None:
    document = fitz.open()
    page = document.new_page()
    if title:
        document.set_metadata({"title": title})
    if text:
        page.insert_text((72, 72), text)
    if include_image:
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20))
        pixmap.clear_with(90)
        page.insert_image(fitz.Rect(72, 100, 92, 120), pixmap=pixmap)
    document.save(str(path))
    document.close()


def _generate_html(pdf_path, *, alt_text: str | None = None) -> tuple[str, str]:
    remediator = PdfRemediator(str(pdf_path), [])
    if alt_text is not None:
        remediator._alt_texts = {(1, 0): alt_text}
    with fitz.open(str(pdf_path)) as document:
        raw_title = remediator._get_document_title(document)
        assert remediator._generate_accessible_html(document)
    assert remediator._html_output is not None
    return raw_title, remediator._html_output


def _image_bytes(image_format: str, size: tuple[int, int] = (2, 2)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(12, 34, 56)).save(output, format=image_format)
    return output.getvalue()


def _image_data_url(mime_format: str, image_bytes: bytes) -> str:
    payload = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/{mime_format};base64,{payload}"


def test_fragment_sanitizer_rebuilds_allowlisted_html_canonically():
    png_data_url = _image_data_url("png", _image_bytes("PNG"))
    fragment = f"""
    <div class="page" id="page12" style="font-family:'x</style><script>x</script>';background:url(javascript:alert(1))" onclick="alert(1)">
      <p id="not-a-page"><span id="page3" data-x="bad" style="color:red"><font face="evil">Café &amp; tea</font></span></p>
      <a href="https://example.edu/a?x=1&amp;y=2" title="drop" onmouseover="bad">safe link</a>
      <img src="{png_data_url}" alt="A &quot;quote&quot; &amp; detail" title="Safe &lt;title&gt;" onerror="alert(1)" style="background:url(javascript:bad)">
      <br></br><!-- <img src='data:image/svg+xml;base64,PHN2Zz4='> -->
    </div>
    """

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    assert rendered == (
        '\n    <div id="page12">\n      <p><span id="page3">Café &amp; tea'
        "</span></p>\n      "
        '<a href="https://example.edu/a?x=1&amp;y=2">safe link</a>\n      '
        f'<img src="{png_data_url}" alt="A &quot;quote&quot; &amp; detail" '
        'title="Safe &lt;title&gt;">\n      <br>\n    </div>\n    '
    )
    parser = _assert_fragment_contract(rendered)
    assert "font" not in parser.start_tags
    assert "script" not in parser.start_tags


def test_fragment_sanitizer_rejects_active_urls_and_dangerous_content():
    jpeg_data_url = _image_data_url("jpeg", _image_bytes("JPEG"))
    fragment = f"""
    <a href="javascript:alert(1)">js</a>
    <a href="java&#x73;cript:alert(1)">encoded js</a>
    <a href="data:text/html;base64,PHNjcmlwdD4=">html</a>
    <a href="//evil.example/path">scheme relative</a>
    <a href="HTTP://safe.example/path">http</a>
    <a href="https://safe.example/path">https</a>
    <a href="mailto:accessibility@example.edu">mail</a><a href="#page9">jump</a>
    <img src="data:image/svg+xml;base64,PHN2Zz4=" alt="svg">
    <img src="data:text/html;base64,PGgxPmJhZDwvaDE+" alt="html">
    <img src="data:image/png;base64,not-valid!" alt="invalid base64">
    <img src="{jpeg_data_url}" alt="photo" onerror="bad">
    <script><p>script secret</p></script>
    <style><p>style secret</p></style>
    <iframe><p>frame secret</p></iframe>
    <object><p>object secret</p></object>
    <embed><p>embed visible</p></embed>
    <meta><p>meta visible</p></meta>
    <link><p>link visible</p></link>
    """

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert [attrs for tag, attrs in parser.attributes if tag == "a"] == [
        {},
        {},
        {},
        {},
        {"href": "http://safe.example/path"},
        {"href": "https://safe.example/path"},
        {"href": "mailto:accessibility@example.edu"},
        {"href": "#page9"},
    ]
    assert parser.image_attributes == [
        {"alt": "svg"},
        {"alt": "html"},
        {"alt": "invalid base64"},
        {"src": jpeg_data_url, "alt": "photo"},
    ]
    text = "".join(parser.text)
    assert "encoded js" in text and "scheme relative" in text
    assert "http" in text and "https" in text and "mail" in text and "jump" in text
    assert "embed visible" in text and "meta visible" in text and "link visible" in text
    assert "secret" not in text


def test_fragment_sanitizer_drops_blocked_void_tags_without_hiding_following_content():
    fragment = (
        "<meta><p>visible</p>"
        "<link><p>visible2</p>"
        "<embed><p>visible3</p>"
        "<script><p>script secret</p></script>"
        "<style><p>style secret</p></style>"
        "<iframe><p>iframe secret</p></iframe>"
        "<object><p>object secret</p></object>"
    )

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    assert rendered == "<p>visible</p><p>visible2</p><p>visible3</p>"
    parser = _assert_fragment_contract(rendered)
    assert "".join(parser.text) == "visiblevisible2visible3"


def test_fragment_sanitizer_keeps_malformed_blocked_containers_passive_and_escaped():
    fragment = (
        "<div><script><style>hidden & <b>bad</script></style>"
        '<p onclick="alert(1)">visible & text</p>'
        '<object><a href="javascript:bad">object secret</object>'
        '<unknown><em data-x="bad">tail & detail</em></unknown></div>'
    )

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    assert rendered == (
        "<div><p>visible &amp; text</p><em>tail &amp; detail</em></div>"
    )
    parser = _assert_fragment_contract(rendered)
    assert "secret" not in "".join(parser.text)


def test_fragment_sanitizer_accepts_valid_png_and_jpeg_only_for_matching_mime():
    png_data_url = _image_data_url("png", _image_bytes("PNG"))
    jpeg_data_url = _image_data_url("jpeg", _image_bytes("JPEG"))
    fragment = f"""
    <img src="data:image/png;base64,aGVsbG8=" alt="spoofed png">
    <img src="data:image/jpeg;base64,aGVsbG8=" alt="spoofed jpeg">
    <img src="{png_data_url}" alt="valid png">
    <img src="{jpeg_data_url}" alt="valid jpeg">
    <img src="{_image_data_url('png', _image_bytes('JPEG'))}" alt="jpeg as png">
    <img src="{_image_data_url('jpeg', _image_bytes('PNG'))}" alt="png as jpeg">
    """

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [
        {"alt": "spoofed png"},
        {"alt": "spoofed jpeg"},
        {"src": png_data_url, "alt": "valid png"},
        {"src": jpeg_data_url, "alt": "valid jpeg"},
        {"alt": "jpeg as png"},
        {"alt": "png as jpeg"},
    ]


@pytest.mark.parametrize(
    ("mime_format", "invalid_bytes"),
    [
        ("png", b"\x89PNG\r\n\x1a\n"),
        ("png", _image_bytes("PNG")[:-8]),
        ("jpeg", b"\xff\xd8\xff"),
        ("jpeg", _image_bytes("JPEG")[:-2]),
    ],
)
def test_fragment_sanitizer_rejects_signature_only_and_truncated_images(
    mime_format: str, invalid_bytes: bytes
):
    fragment = f'<img src="{_image_data_url(mime_format, invalid_bytes)}" alt="bad">'

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "bad"}]


@pytest.mark.parametrize("mime_format", ["png", "jpeg"])
def test_fragment_sanitizer_rejects_trailing_html_and_polyglot_bytes(
    mime_format: str,
):
    image_format = "PNG" if mime_format == "png" else "JPEG"
    polyglot = _image_bytes(image_format) + b"<script>alert(1)</script>"
    fragment = f'<img src="{_image_data_url(mime_format, polyglot)}" alt="polyglot">'

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "polyglot"}]


def test_jpeg_rejects_trailing_attacker_bytes_even_with_synthetic_final_eoi():
    baseline_jpeg = _image_bytes("JPEG")
    attacker_jpeg = baseline_jpeg + b"<script>alert(1)</script>" + b"\xff\xd9"

    assert _is_verified_image(baseline_jpeg, "jpeg")
    assert not _is_verified_image(attacker_jpeg, "jpeg")


def test_jpeg_marker_parser_handles_entropy_markers_and_multiple_scans():
    framed_jpeg = (
        b"\xff\xd8"
        b"\xff\xe0\x00\x04AB"
        b"\xff\xda\x00\x02"
        b"scan\xff\x00stuffed\xff\xd0restart\xff\xff\xd7"
        b"\xff\xc4\x00\x02"
        b"\xff\xda\x00\x02"
        b"second\xff\xff\x00stuffed-again"
        b"\xff\xff\xd9"
    )

    assert pdf_remediator_module._is_complete_jpeg_file(framed_jpeg)
    assert not pdf_remediator_module._is_complete_jpeg_file(framed_jpeg + b"trailing")


def test_verified_image_reopens_and_rejects_pixel_load_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    class StubImage:
        format = "PNG"
        size = (2, 2)

        def __init__(self, *, load_error: Exception | None = None) -> None:
            self.load_error = load_error
            self.verify_called = False
            self.load_called = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def verify(self) -> None:
            self.verify_called = True

        def load(self) -> None:
            self.load_called = True
            if self.load_error is not None:
                raise self.load_error

    verify_image = StubImage()
    load_image = StubImage(load_error=OSError("pixel decode failed"))
    images = iter((verify_image, load_image))
    opened_sources: list[BytesIO] = []

    def open_stub(source: BytesIO):
        opened_sources.append(source)
        return next(images)

    monkeypatch.setattr(pdf_remediator_module.Image, "open", open_stub)

    result = _is_verified_image(_image_bytes("PNG"), "png")

    assert verify_image.verify_called
    assert load_image.load_called
    assert len(opened_sources) == 2
    assert opened_sources[0] is not opened_sources[1]
    assert not result


@pytest.mark.parametrize(
    ("mime_format", "corrupt_bytes"),
    [
        (
            "png",
            _image_bytes("PNG")[:8]
            + b"\x7f\xff\xff\xffIDAT"
            + _image_bytes("PNG")[16:],
        ),
        ("jpeg", b"\xff\xd8\xff\xe0\x00\x01\xff\xd9"),
    ],
)
def test_fragment_sanitizer_rejects_corrupt_chunks_and_segments(
    mime_format: str, corrupt_bytes: bytes
):
    fragment = (
        f'<img src="{_image_data_url(mime_format, corrupt_bytes)}" alt="corrupt">'
    )

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "corrupt"}]


def test_fragment_sanitizer_rejects_png_with_corrupt_chunk_checksum():
    corrupt_png = bytearray(_image_bytes("PNG"))
    idat_type_offset = corrupt_png.index(b"IDAT")
    corrupt_png[idat_type_offset + 4] ^= 0x01
    fragment = f'<img src="{_image_data_url("png", bytes(corrupt_png))}" alt="crc">'

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "crc"}]


@pytest.mark.parametrize(
    "pillow_error",
    [
        Image.DecompressionBombError("bomb"),
        Image.DecompressionBombWarning("bomb warning"),
        UnidentifiedImageError("unknown"),
        EOFError("parse error"),
        OSError("parse error"),
        SyntaxError("parse error"),
        ValueError("parse error"),
    ],
)
def test_fragment_sanitizer_fails_closed_on_pillow_errors(
    monkeypatch: pytest.MonkeyPatch, pillow_error: Exception
):
    png_data_url = _image_data_url("png", _image_bytes("PNG"))

    def raise_pillow_error(*args, **kwargs):
        raise pillow_error

    monkeypatch.setattr(pdf_remediator_module.Image, "open", raise_pillow_error)

    rendered = _sanitize_pymupdf_html_fragment(
        f'<img src="{png_data_url}" alt="pillow error">'
    )

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "pillow error"}]


@pytest.mark.parametrize(
    ("constant_name", "size", "limit"),
    [
        ("_MAX_IMAGE_WIDTH", (2, 1), 1),
        ("_MAX_IMAGE_HEIGHT", (1, 2), 1),
        ("_MAX_IMAGE_PIXELS", (2, 2), 3),
    ],
)
def test_fragment_sanitizer_rejects_images_over_dimension_limits(
    monkeypatch: pytest.MonkeyPatch,
    constant_name: str,
    size: tuple[int, int],
    limit: int,
):
    monkeypatch.setattr(pdf_remediator_module, constant_name, limit)
    fragment = (
        f'<img src="{_image_data_url("png", _image_bytes("PNG", size))}" alt="huge">'
    )

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "huge"}]


@pytest.mark.parametrize("overflow", [b"x", b"xxx"])
def test_fragment_sanitizer_rejects_oversized_image_payloads(
    monkeypatch: pytest.MonkeyPatch, overflow: bytes
):
    png_bytes = _image_bytes("PNG")
    monkeypatch.setattr(pdf_remediator_module, "_MAX_IMAGE_DATA_BYTES", len(png_bytes))
    oversized_payload = base64.b64encode(png_bytes + overflow).decode("ascii")
    fragment = f'<img src="data:image/png;base64,{oversized_payload}" alt="oversized">'

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "oversized"}]


def test_fragment_sanitizer_rejects_whitespace_amplified_data_url_before_decode(
    monkeypatch: pytest.MonkeyPatch,
):
    png_bytes = _image_bytes("PNG")
    monkeypatch.setattr(pdf_remediator_module, "_MAX_IMAGE_DATA_BYTES", len(png_bytes))

    def fail_if_decoded(*args, **kwargs):
        pytest.fail("oversized raw payload reached base64 decoding")

    monkeypatch.setattr(pdf_remediator_module.base64, "b64decode", fail_if_decoded)
    amplified_payload = base64.b64encode(png_bytes).decode("ascii") + (" \n\t" * 10_000)
    fragment = f'<img src="data:image/png;base64,{amplified_payload}" alt="amplified">'

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    parser = _assert_fragment_contract(rendered)
    assert parser.image_attributes == [{"alt": "amplified"}]


def test_fragment_sanitizer_balances_malformed_nesting_and_never_closes_void_tags():
    fragment = "<div><p>one<span>two</div>three</span></p><br></br><img></img>"

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    assert rendered == "<div><p>one<span>two</span></p></div>three<br><img>"
    parser = _assert_fragment_contract(rendered)
    assert parser.end_tags == ["span", "p", "div"]


def test_fragment_sanitizer_drops_comments_and_normalizes_invalid_unicode():
    fragment = "<p>A\x00B\ud800<!-- <script>hidden</script> -->&lt;&amp;</p>"

    rendered = _sanitize_pymupdf_html_fragment(fragment)

    assert rendered == "<p>A�B�&lt;&amp;</p>"
    assert "\x00" not in rendered and "\ud800" not in rendered
    rendered.encode("utf-8")
    parser = _assert_fragment_contract(rendered)
    assert "".join(parser.text) == "A�B�<&"


def test_metadata_title_is_escaped_at_text_interpolation_boundary(tmp_path):
    payload = '</title><script>alert(1)</script>&"'
    pdf_path = tmp_path / "metadata-title.pdf"
    _write_pdf(pdf_path, title=payload)

    raw_title, rendered = _generate_html(pdf_path)

    assert raw_title == payload
    assert payload not in rendered
    assert '&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;&amp;"' in rendered
    parser = _HtmlRecorder()
    parser.feed(rendered)
    assert "script" not in parser.start_tags
    assert payload in "".join(parser.text)


def test_stored_alt_text_is_escaped_as_one_attribute(tmp_path):
    payload = "Café 😀 ' \" onerror=\"alert(1)\" data-x='bad'><script>x</script>&"
    pdf_path = tmp_path / "image.pdf"
    _write_pdf(pdf_path, include_image=True)

    remediator = PdfRemediator(str(pdf_path), [])
    remediator._alt_texts = {(1, 0): payload}
    assert remediator._get_alt_text_for_image(1, 0) == payload
    with fitz.open(str(pdf_path)) as document:
        assert remediator._generate_accessible_html(document)

    assert remediator._html_output is not None
    rendered = remediator._html_output
    assert payload not in rendered
    assert "&quot;" in rendered
    assert "&#x27;" in rendered
    assert "&lt;script&gt;x&lt;/script&gt;&amp;" in rendered
    assert "Café 😀" in rendered
    parser = _HtmlRecorder()
    parser.feed(rendered)
    assert "script" not in parser.start_tags
    fallback_images = [
        attributes for attributes in parser.image_attributes if "src" not in attributes
    ]
    assert fallback_images == [{"alt": payload}]
    assert "[Image " not in rendered


def test_filename_fallback_title_is_escaped(tmp_path):
    pdf_path = tmp_path / 'report<title onclick="alert(1)">&.pdf'
    _write_pdf(pdf_path)

    raw_title, rendered = _generate_html(pdf_path)

    assert raw_title == pdf_path.stem
    assert pdf_path.stem not in rendered
    assert 'report&lt;title onclick="alert(1)"&gt;&amp;' in rendered
    parser = _HtmlRecorder()
    parser.feed(rendered)
    assert parser.start_tags.count("title") == 1
    assert pdf_path.stem in "".join(parser.text)


def test_malformed_unicode_is_replaced_without_losing_valid_unicode(tmp_path):
    pdf_path = tmp_path / "unicode.pdf"
    _write_pdf(pdf_path)
    title = "Café 漢字 😀 malformed:\ud800"
    remediator = PdfRemediator(str(pdf_path), [])

    assert remediator._generate_accessible_html(_MetadataOnlyDocument(title))

    assert remediator._html_output is not None
    assert "Café 漢字 😀 malformed:" in remediator._html_output
    assert "\ud800" not in remediator._html_output
    remediator._html_output.encode("utf-8")


def test_pymupdf_page_html_is_sanitized_without_double_escaping(tmp_path):
    payload = '<script>alert(1)</script> & "quoted"'
    pdf_path = tmp_path / "page-text.pdf"
    _write_pdf(pdf_path, text=payload, include_image=True)
    remediator = PdfRemediator(str(pdf_path), [])

    with fitz.open(str(pdf_path)) as document:
        page_html = document[0].get_text("html")
        sanitized_page_html = _sanitize_pymupdf_html_fragment(page_html)
        assert 'style="' in page_html
        assert "data:image/png;base64," in page_html
        assert "<script>" not in page_html
        assert (
            "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot;"
            in page_html
        )
        assert 'style="' not in sanitized_page_html
        assert "class=" not in sanitized_page_html
        assert "onerror=" not in sanitized_page_html
        assert "<script" not in sanitized_page_html
        assert "data:image/png;base64," in sanitized_page_html
        assert (
            '&lt;script&gt;alert(1)&lt;/script&gt; &amp; "quoted"'
            in sanitized_page_html
        )
        assert "&amp;lt;script&amp;gt;" not in sanitized_page_html
        _assert_fragment_contract(sanitized_page_html)
        assert remediator._generate_accessible_html(document)

    assert remediator._html_output is not None
    assert page_html not in remediator._html_output
    assert sanitized_page_html in remediator._html_output
    assert "&amp;lt;script&amp;gt;" not in remediator._html_output
    parser = _HtmlRecorder()
    parser.feed(sanitized_page_html)
    assert "script" not in parser.start_tags
    assert payload in "".join(parser.text)
