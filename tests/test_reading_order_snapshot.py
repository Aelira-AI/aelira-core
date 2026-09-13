"""Snapshots must describe the uploaded PDF's tags, including their limitations."""

import base64
import hashlib
import io
import zlib

import fitz
import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from src.education import reading_order_snapshot as snapshot

pytestmark = pytest.mark.unit


def make_pdf(
    *,
    pages=1,
    duplicate=False,
    replacement=None,
    defect=None,
    rotation=0,
    crop=False,
    kind="integer",
    alt=None,
    marked_actual=None,
    encrypted=False,
):
    pdf = pikepdf.new()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    children, numbers = [], []
    for page_index in range(pages):
        page = pdf.add_blank_page(page_size=(400, 400))
        page.obj.StructParents = page_index
        page.obj.Rotate = rotation
        if crop:
            page.obj.CropBox = Array([20, 20, 380, 390])
        page.obj.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(
                    Type=Name.Font,
                    Subtype=Name.Type1,
                    BaseFont=Name.Helvetica,
                )
            )
        )
        labels = [f"First page {page_index}", f"Second page {page_index}"]
        if duplicate:
            labels = ["Repeated", "Repeated"]
        page.obj.Contents = pdf.make_stream(
            "\n".join(
                f"/P <</MCID {i}>> BDC BT /F1 12 Tf 30 {330-i*60} Td ({labels[i]}) Tj ET EMC"
                for i in (0, 1)
            ).encode()
        )
        if marked_actual is not None:
            page.obj.Contents = pdf.make_stream(
                page.obj.Contents.read_bytes().replace(
                    b"/MCID 0", b"/MCID 0 /ActualText (" + marked_actual.encode() + b")"
                )
            )
        owners = []
        for i in range(2):
            element = pdf.make_indirect(
                Dictionary(Type=Name.StructElem, S=Name.P, P=root, Pg=page.obj, K=i)
            )
            if kind == "mcr":
                element.K = Dictionary(Type=Name.MCR, MCID=i)
            owners.append(element)
        if replacement is not None:
            owners[0].ActualText = String(replacement)
        if alt is not None:
            owners[0].Alt = String(alt)
        children.extend(reversed(owners))
        numbers.extend([page_index, Array(owners)])
    root.K = Array(children)
    root.ParentTree = Dictionary(Nums=Array(numbers))
    if defect == "untagged":
        del pdf.Root.StructTreeRoot
    elif defect == "owner":
        root.ParentTree.Nums[1][0] = None
    elif defect == "missing_content":
        root.K[0].K = 99
    elif defect == "object_reference":
        root.K[0].K = Dictionary(Type=Name.OBJR)
    elif defect == "empty":
        root.K = Array([])
    elif defect == "invalid_alt":
        root.K[0].Alt = 123
    elif defect == "missing_tag":
        root.K = Array([root.K[0]])
    elif defect == "huge_page":
        pdf.pages[0].obj.MediaBox = Array([0, 0, 100000, 100000])
    elif defect == "cycle":
        root.K[0].K = root.K[0]
    elif defect == "expansion":
        pdf.pages[0].obj.Contents = pdf.make_stream(
            b" " * (snapshot.MAX_DECODED_STREAM_BYTES + 1)
        )
    elif defect == "substring":
        page = pdf.pages[0]
        page.obj.Contents = pdf.make_stream(
            page.obj.Contents.read_bytes()
            + b" BT /F1 12 Tf 30 200 Td (Other Second page 0 suffix) Tj ET"
        )
    elif defect == "multiline":
        page = pdf.pages[0]
        page.obj.Contents = pdf.make_stream(
            page.obj.Contents.read_bytes().replace(
                b"(Second page 0) Tj", b"(Second) Tj 0 -20 Td (page 0) Tj"
            )
        )
    output = io.BytesIO()
    pdf.save(
        output,
        encryption=(
            pikepdf.Encryption(owner="owner", user="password") if encrypted else False
        ),
    )
    return output.getvalue()


@pytest.mark.parametrize(
    "kind", ["form", "type3", "pattern", "inline", "repeated_image"]
)
def test_preview_execution_expansion_is_rejected_before_render(monkeypatch, kind):
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(400, 400))
        resources = Dictionary()
        page.Resources = resources
        content = b"q Q"
        if kind == "form":
            child = pdf.make_stream(b"0 0 10 10 re f")
            for _ in range(12):
                child.Type = Name.XObject
                child.Subtype = Name.Form
                child.BBox = Array([0, 0, 400, 400])
                child.Resources = child.get("/Resources", Dictionary())
                parent = pdf.make_stream(b"/Next Do /Next Do")
                parent.Resources = Dictionary(XObject=Dictionary(Next=child))
                child = parent
            child.Type, child.Subtype = Name.XObject, Name.Form
            child.BBox = Array([0, 0, 400, 400])
            resources.XObject = Dictionary(F=child)
            content = b"/F Do"
        elif kind == "type3":
            resources.Font = Dictionary(
                F=Dictionary(Type=Name.Font, Subtype=Name.Type3)
            )
        elif kind == "pattern":
            resources.Pattern = Dictionary(P=Dictionary(PatternType=1))
        elif kind == "inline":
            content = b"BI /W 1 /H 1 /BPC 8 /CS /RGB ID " + bytes([0, 0, 0]) + b" EI"
        else:
            image = pdf.make_stream(bytes(512 * 512 * 3))
            image.Type, image.Subtype = Name.XObject, Name.Image
            image.Width, image.Height = 512, 512
            image.ColorSpace, image.BitsPerComponent = Name.DeviceRGB, 8
            resources.XObject = Dictionary(Im=image)
            content = b"/Im Do " * 10000
        page.Contents = pdf.make_stream(content)
        output = io.BytesIO()
        pdf.save(output)
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("renderer reached before execution preflight")

    monkeypatch.setattr(snapshot.fitz, "open", forbidden)
    result = snapshot.inspect_pdf_reading_order(output.getvalue(), 1)
    assert called == []
    assert result["status"] == "unavailable"
    assert result["reason"] in {"unsupported_pdf", "limit_exceeded"}


def test_actual_tag_sequence_and_preview_from_unchanged_bytes():
    content = make_pdf()
    original = bytes(content)
    result = snapshot.inspect_pdf_reading_order(content, 1)
    assert result["status"] == "available"
    assert result["reason"] is None
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["page_count"] == 1
    assert [b["text"] for b in result["blocks"]] == ["Second page 0", "First page 0"]
    assert [b["index"] for b in result["blocks"]] == [1, 2]
    assert all(b["source"] == "MCID" for b in result["blocks"])
    assert result["blocks"][0]["bbox"][1] > result["blocks"][1]["bbox"][1]
    pix = fitz.Pixmap(base64.b64decode(result["preview_png_base64"]))
    assert max(pix.width, pix.height) <= 1200
    assert (result["width"], result["height"]) == (400, 400)
    assert content == original


@pytest.mark.parametrize("kind", ["repeated_stream", "operators", "page_bytes"])
def test_content_invocation_limits_precede_renderer(monkeypatch, kind):
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        page = pdf.pages[0]
        if kind == "repeated_stream":
            stream = pdf.make_stream(b"q Q " * 300)
            page.Contents = Array([stream] * 1000)
        elif kind == "operators":
            page.Contents = pdf.make_stream(b"q Q " * 10001)
        else:
            page.Contents = pdf.make_stream(
                b" " * (snapshot.MAX_PAGE_CONTENT_BYTES + 1)
            )
        output = io.BytesIO()
        pdf.save(output)
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("renderer reached")

    monkeypatch.setattr(snapshot.fitz, "open", forbidden)
    result = snapshot.inspect_pdf_reading_order(output.getvalue(), 1)
    assert called == []
    assert result["reason"] == "limit_exceeded"


def test_page_scoping_hash_and_invalid_page():
    content = make_pdf(pages=2)
    result = snapshot.inspect_pdf_reading_order(content, 2)
    assert result["page_count"] == 2
    assert result["page_number"] == 2
    assert [b["text"] for b in result["blocks"]] == ["Second page 1", "First page 1"]
    for page in (0, 3, True):
        invalid = snapshot.inspect_pdf_reading_order(content, page)
        assert invalid["reason"] == "invalid_page"
        assert invalid["blocks"] == []
    assert (
        result["sha256"] != snapshot.inspect_pdf_reading_order(make_pdf(), 1)["sha256"]
    )


def test_duplicate_text_is_never_assigned_guessed_boxes():
    result = snapshot.inspect_pdf_reading_order(make_pdf(duplicate=True), 1)
    assert result["status"] == "available"
    assert result["unpositioned_count"] == 2
    assert [b["bbox"] for b in result["blocks"]] == [None, None]


def test_actual_text_is_exact_semantic_text_without_a_painted_box():
    text = " Accessible expansion\nwith another line and Unicode: café "
    result = snapshot.inspect_pdf_reading_order(make_pdf(replacement=text), 1)
    assert result["status"] == "available"
    assert result["blocks"][1] == {
        "index": 2,
        "text": text,
        "source": "ActualText",
        "bbox": None,
    }
    assert result["unpositioned_count"] == 1


def test_marked_actual_text_and_mcr_references():
    result = snapshot.inspect_pdf_reading_order(
        make_pdf(kind="mcr", marked_actual=" accessible phrase "), 1
    )
    assert result["status"] == "available"
    assert result["blocks"][1] == {
        "index": 2,
        "text": " accessible phrase ",
        "source": "ActualText",
        "bbox": None,
    }


def test_alt_is_semantic_and_duplicates_prevent_matching():
    result = snapshot.inspect_pdf_reading_order(make_pdf(alt="Second page 0"), 1)
    assert result["status"] == "available"
    assert result["blocks"][1]["source"] == "Alt"
    assert result["blocks"][1]["bbox"] is None
    assert result["blocks"][0]["bbox"] is None


def test_alt_page_is_resolved_from_child_mcr():
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        owner = pdf.Root.StructTreeRoot.K[0]
        owner.Alt = String(" Figure description ")
        owner.K = Dictionary(Type=Name.MCR, Pg=pdf.pages[0].obj, MCID=owner.K)
        del owner.Pg
        output = io.BytesIO()
        pdf.save(output)
    result = snapshot.inspect_pdf_reading_order(output.getvalue(), 1)
    assert result["status"] == "available"
    assert result["blocks"][0] == {
        "index": 1,
        "text": " Figure description ",
        "source": "Alt",
        "bbox": None,
    }


def test_unsupported_filter_chain_is_explicitly_unavailable():
    with pikepdf.open(io.BytesIO(make_pdf())) as pdf:
        stream = pdf.pages[0].Contents
        encoded = zlib.compress(zlib.compress(stream.read_bytes()))
        stream.write(encoded, filter=Array([Name.FlateDecode, Name.FlateDecode]))
        output = io.BytesIO()
        pdf.save(
            output,
            compress_streams=False,
            stream_decode_level=pikepdf.StreamDecodeLevel.none,
        )
    result = snapshot.inspect_pdf_reading_order(output.getvalue(), 1)
    assert result["status"] == "unavailable"
    assert result["reason"] == "unsupported_pdf"
    assert result["blocks"] == []


@pytest.mark.parametrize("defect", ["substring", "multiline"])
def test_ambiguous_or_multiline_text_has_no_box(defect):
    result = snapshot.inspect_pdf_reading_order(make_pdf(defect=defect), 1)
    assert result["status"] == "available"
    assert result["blocks"][0]["bbox"] is None
    if defect == "multiline":
        assert result["blocks"][0]["text"] == "Second\npage 0"


def test_encrypted_pdf_does_not_expose_content():
    result = snapshot.inspect_pdf_reading_order(make_pdf(encrypted=True), 1)
    assert result["reason"] == "encrypted_pdf"
    assert result["blocks"] == []
    assert result["preview_png_base64"] is None


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_cropped_coordinates_match_render_frame(rotation):
    content = make_pdf(rotation=rotation, crop=True)
    result = snapshot.inspect_pdf_reading_order(content, 1)
    assert result["status"] == "available"
    with fitz.open(stream=content, filetype="pdf") as pdf:
        page = pdf[0]
        expected = page.search_for("Second page 0")[0] * page.rotation_matrix
        assert result["blocks"][0]["bbox"] == pytest.approx(list(expected))
        assert result["width"] == page.rect.width
        assert result["height"] == page.rect.height


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("untagged", "untagged_pdf"),
        ("owner", "unresolved_structure"),
        ("missing_content", "unresolved_structure"),
        ("object_reference", "unresolved_structure"),
        ("empty", "no_tagged_content"),
        ("invalid_alt", "unresolved_structure"),
        ("missing_tag", "unresolved_structure"),
        ("huge_page", "limit_exceeded"),
        ("cycle", "limit_exceeded"),
        ("expansion", "limit_exceeded"),
    ],
)
def test_unavailable_never_asserts_partial_order(defect, reason):
    result = snapshot.inspect_pdf_reading_order(make_pdf(defect=defect), 1)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert result["blocks"] == []


def test_malformed_pdf_is_safe():
    content = b"%PDF-1.7\nThis is not a PDF"
    result = snapshot.inspect_pdf_reading_order(content, 1)
    assert result["reason"] == "invalid_pdf"
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["preview_png_base64"] is None


@pytest.mark.parametrize(
    "limit,value",
    [
        ("MAX_SOURCE_BYTES", 10),
        ("MAX_PAGES", 0),
        ("MAX_BLOCKS", 1),
        ("MAX_TEXT_CHARACTERS", 4),
        ("MAX_PREVIEW_BYTES", 1),
        ("MAX_DECODED_STREAM_BYTES", 10),
    ],
)
def test_limits_fail_explicitly_without_truncation(monkeypatch, limit, value):
    monkeypatch.setattr(snapshot, limit, value)
    result = snapshot.inspect_pdf_reading_order(make_pdf(), 1)
    assert result["status"] == "unavailable"
    assert result["reason"] == "limit_exceeded"
    assert result["blocks"] == []
