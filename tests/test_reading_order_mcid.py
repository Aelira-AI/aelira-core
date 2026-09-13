"""Real tagged PDF fixtures for page-scoped marked-content decoding."""

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from src.education.pdf_checks.completeness import (
    IncompletePDFScanError,
    require_complete_pdf_scan,
)
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit


def tagged_pdf(tmp_path, order=(0, 1), *, pages=1, kind="integer", no_pg=False):
    pdf = pikepdf.new()
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    children, numbers = [], []
    for page_index in range(pages):
        page = pdf.add_blank_page(page_size=(400, 400))
        page.obj.StructParents = page_index
        page.obj.Resources = Dictionary(
            Font=Dictionary(
                F1=Dictionary(
                    Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica
                )
            )
        )
        labels = [f"First page {page_index}", f"Second page {page_index}"]
        # Deliberately paint bottom before top: /K determines semantic order.
        page.obj.Contents = pdf.make_stream(
            "\n".join(
                f"/P <</MCID {i}>> BDC BT /F1 12 Tf 30 {330-i*60} Td ({labels[i]}) Tj ET EMC"
                for i in (1, 0)
            ).encode()
        )
        owners = []
        for i in range(2):
            element = pdf.make_indirect(
                Dictionary(Type=Name.StructElem, S=Name.P, P=root)
            )
            if not no_pg:
                element.Pg = page.obj
            element.K = Dictionary(Type=Name.MCR, MCID=i) if kind == "mcr" else i
            owners.append(element)
        children.extend(owners[i] for i in order)
        numbers.extend([page_index, Array(owners)])
    root.K = Array(children)
    root.ParentTree = Dictionary(Nums=Array(numbers))
    return pdf, tmp_path / "marked.pdf"


def texts(path, page=0):
    return [
        block["text"]
        for block in ReadingOrderVerifier()._get_structure_tree_order(
            None, str(path), page
        )
    ]


@pytest.mark.parametrize("kind", ["integer", "mcr"])
def test_mcid_text_follows_structure_order_not_paint_order(tmp_path, kind):
    pdf, path = tagged_pdf(tmp_path, kind=kind)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]
        result = ReadingOrderVerifier().check(str(path))
    assert result.has_structure_tree
    assert result.issues == []


def test_wrong_structure_order_is_visible(tmp_path):
    pdf, path = tagged_pdf(tmp_path, order=(1, 0))
    pdf.save(path)
    with require_complete_pdf_scan(True):
        result = ReadingOrderVerifier().check(str(path))
    assert result.issues and result.issues[0].actual_order == [
        "Second page 0",
        "First page 0",
    ]


@pytest.mark.parametrize("kind", ["integer", "mcr"])
@pytest.mark.parametrize("no_pg", [False, True])
def test_repeated_mcid_numbers_belong_to_distinct_pages(tmp_path, kind, no_pg):
    pdf, path = tagged_pdf(tmp_path, pages=2, kind=kind, no_pg=no_pg)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path, 0) == ["First page 0", "Second page 0"]
        assert texts(path, 1) == ["First page 1", "Second page 1"]


@pytest.mark.parametrize("singleton", [False, True])
def test_inherited_page_and_singleton_or_array_k(tmp_path, singleton):
    pdf, path = tagged_pdf(tmp_path, no_pg=True)
    root = pdf.Root.StructTreeRoot
    for owner in root.K:
        owner.K = owner.K if singleton else Array([owner.K])
    document = pdf.make_indirect(
        Dictionary(Type=Name.StructElem, S=Name.Document, Pg=pdf.pages[0].obj, K=root.K)
    )
    root.K = document if singleton else Array([document])
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


def test_parent_tree_number_tree_kids(tmp_path):
    pdf, path = tagged_pdf(tmp_path, pages=2, no_pg=True)
    tree = pdf.Root.StructTreeRoot.ParentTree
    tree.Kids = Array(
        [
            Dictionary(Nums=Array(list(tree.Nums)[:2]), Limits=Array([0, 0])),
            Dictionary(Nums=Array(list(tree.Nums)[2:]), Limits=Array([1, 1])),
        ]
    )
    del tree.Nums
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path, 0) == ["First page 0", "Second page 0"]
        assert texts(path, 1) == ["First page 1", "Second page 1"]


@pytest.mark.parametrize("order", [(0,), (0, 0, 1)])
def test_missing_or_duplicated_structure_content_cannot_pass(tmp_path, order):
    pdf, path = tagged_pdf(tmp_path, order=order)
    pdf.save(path)
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues and result.issues[0].severity == "critical"
    assert result.compliance_score < 100
    if order == (0, 0, 1):
        with pytest.raises(
            IncompletePDFScanError, match="duplicate_structure_reference"
        ):
            with require_complete_pdf_scan(True):
                ReadingOrderVerifier().check(str(path))


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("absent_parent_tree", "parent_tree"),
        ("wrong_owner", "parent_tree_owner"),
        ("missing_parent_entry", "parent_tree_owner"),
        ("bad_page", "page_reference"),
        ("missing_stream_mcid", "missing_mcid"),
        ("duplicate_stream_mcid", "duplicate_mcid"),
        ("negative_mcid", "mcid_reference"),
        ("string_mcid", "mcid_reference"),
        ("stream_mcr", "stream_scope"),
        ("unbalanced_stream", "marked_content_balance"),
        ("missing_font", "font_reference"),
    ],
)
def test_malformed_references_are_incomplete_and_visible_in_partial_mode(
    tmp_path, defect, reason
):
    pdf, path = tagged_pdf(tmp_path)
    root = pdf.Root.StructTreeRoot
    page = pdf.pages[0]
    if defect == "absent_parent_tree":
        del root.ParentTree
    elif defect == "wrong_owner":
        root.ParentTree.Nums[1][0] = root.K[1]
    elif defect == "missing_parent_entry":
        root.ParentTree.Nums[1][0] = None
    elif defect == "bad_page":
        root.K[0].Pg = 123
    elif defect == "missing_stream_mcid":
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes().replace(b"/MCID 0", b"/MCID 2")
        )
    elif defect == "duplicate_stream_mcid":
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes() + b" /P <</MCID 0>> BDC EMC"
        )
    elif defect == "negative_mcid":
        root.K[0].K = -1
    elif defect == "string_mcid":
        root.K[0].K = Dictionary(Type=Name.MCR, MCID="0")
    elif defect == "stream_mcr":
        root.K[0].K = Dictionary(Type=Name.MCR, MCID=0, Stm=page.Contents)
    elif defect == "unbalanced_stream":
        page.Contents = pdf.make_stream(page.Contents.read_bytes() + b" EMC")
    elif defect == "missing_font":
        page.Contents = pdf.make_stream(
            page.Contents.read_bytes().replace(b"/F1", b"/Unknown")
        )
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order." + reason):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    result = ReadingOrderVerifier().check(str(path))
    assert result.issues and result.compliance_score < 100


def test_no_page_with_ambiguous_reverse_parent_tree_is_incomplete(tmp_path):
    pdf, path = tagged_pdf(tmp_path, pages=2, no_pg=True)
    root = pdf.Root.StructTreeRoot
    root.ParentTree.Nums[3][0] = root.K[0]
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.mcid_page"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))


@pytest.mark.parametrize("scope", ["element", "stream", "inner_stream"])
def test_actual_text_replaces_content_once_and_keeps_unrelated_mcid(tmp_path, scope):
    pdf, path = tagged_pdf(tmp_path)
    if scope == "element":
        pdf.Root.StructTreeRoot.K[0].ActualText = "First page 0"
    else:
        page = pdf.pages[0]
        stream = page.Contents.read_bytes()
        if scope == "stream":
            stream = stream.replace(b"/MCID 0", b"/MCID 0 /ActualText (First page 0)")
        else:
            stream = stream.replace(
                b"(First page 0) Tj",
                b"/Span <</ActualText (First page 0)>> BDC (First page 0) Tj EMC",
            )
        page.Contents = pdf.make_stream(stream)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]
        assert ReadingOrderVerifier().check(str(path)).issues == []


def test_actual_text_no_page_resolves_descendant_ownership(tmp_path):
    pdf, path = tagged_pdf(tmp_path, no_pg=True)
    pdf.Root.StructTreeRoot.K[0].ActualText = "First page 0"
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


def test_actual_text_does_not_hide_broken_child_reference(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    owner = pdf.Root.StructTreeRoot.K[0]
    owner.ActualText = "First page 0"
    owner.K = 999
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="parent_tree_owner"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))


def test_property_resource_reference_and_split_text_operators(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    page.Resources.Properties = Dictionary(Tag0=Dictionary(MCID=0))
    stream = page.Contents.read_bytes().replace(b"<</MCID 0>>", b"/Tag0")
    stream = stream.replace(b"(First page 0) Tj", b"[(First) -278 (page) -278 (0)] TJ")
    page.Contents = pdf.make_stream(stream)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]
        assert ReadingOrderVerifier().check(str(path)).issues == []


def test_encoded_font_decodes_tounicode_not_raw_string_bytes(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    font = page.Resources.Font.F1
    font.ToUnicode = pdf.make_stream(b"""
        /CIDInit /ProcSet findresource begin 12 dict begin begincmap
        /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
        /CMapName /SourceText def /CMapType 2 def
        1 begincodespacerange <00> <FF> endcodespacerange
        3 beginbfchar <01> <004600690072007300740020007000610067006500200030>
        <02> <005300650063006f006e00640020007000610067006500200030>
        <20> <0020> endbfchar
        endcmap CMapName currentdict /CMap defineresource pop end end
    """)
    stream = (
        page.Contents.read_bytes()
        .replace(b"(First page 0)", b"<01>")
        .replace(b"(Second page 0)", b"<02>")
    )
    page.Contents = pdf.make_stream(stream)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


def test_form_xobject_cannot_be_assumed_to_use_page_mcid_namespace(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    form = pdf.make_stream(b"/P <</MCID 0>> BDC EMC")
    form.Type, form.Subtype = Name.XObject, Name.Form
    form.BBox = Array([0, 0, 10, 10])
    page.Resources.XObject = Dictionary(Fm=form)
    page.Contents = pdf.make_stream(page.Contents.read_bytes() + b" /Fm Do")
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order.stream_scope"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


def test_multiple_integer_kids_can_share_one_parent_tree_owner(tmp_path):
    pdf, path = tagged_pdf(tmp_path, no_pg=True)
    root = pdf.Root.StructTreeRoot
    owner = root.K[0]
    owner.K = Array([0, 1])
    root.K = owner
    root.ParentTree.Nums[1] = Array([owner, owner])
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


def test_mcr_explicit_page_overrides_parent_page(tmp_path):
    pdf, path = tagged_pdf(tmp_path, pages=2, kind="mcr")
    owner = pdf.Root.StructTreeRoot.K[2]
    owner.K.Pg = pdf.pages[1].obj
    owner.Pg = pdf.pages[0].obj
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path, 0) == ["First page 0", "Second page 0"]
        assert texts(path, 1) == ["First page 1", "Second page 1"]


def test_page_content_stream_array_is_one_mcid_namespace(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    page.Contents = Array(
        [pdf.make_stream(line) for line in page.Contents.read_bytes().splitlines()]
    )
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


def test_type0_multibyte_font_decodes_source_cmap(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    descendant = Dictionary(
        Type=Name.Font,
        Subtype=Name.CIDFontType2,
        BaseFont=Name.TestFont,
        CIDSystemInfo=Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        FontDescriptor=Dictionary(
            Type=Name.FontDescriptor,
            FontName=Name.TestFont,
            Flags=4,
            FontBBox=Array([0, -200, 1000, 800]),
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
        ),
    )
    cmap = pdf.make_stream(b"""
        /CIDInit /ProcSet findresource begin 12 dict begin begincmap
        /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
        /CMapName /SourceText def /CMapType 2 def
        1 begincodespacerange <0000> <FFFF> endcodespacerange
        1 beginbfrange <0000> <00FF> <0000> endbfrange
        endcmap CMapName currentdict /CMap defineresource pop end end
    """)
    page.Resources.Font.F1 = Dictionary(
        Type=Name.Font,
        Subtype=Name.Type0,
        BaseFont=Name.TestFont,
        Encoding=Name("/Identity-H"),
        DescendantFonts=Array([descendant]),
        ToUnicode=cmap,
    )
    content = page.Contents.read_bytes()
    for text in ("First page 0", "Second page 0"):
        content = content.replace(
            f"({text})".encode(), b"<" + text.encode("utf-16-be").hex().encode() + b">"
        )
    page.Contents = pdf.make_stream(content)
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]


@pytest.mark.parametrize(
    "defect",
    [
        "unknown_encoding",
        "unmapped_unicode",
        "nested_mcid",
        "shared_structparents",
        "cyclic_parent_tree",
    ],
)
def test_unsupported_or_ambiguous_decoding_never_returns_clean(tmp_path, defect):
    pdf, path = tagged_pdf(tmp_path, pages=2)
    page = pdf.pages[0]
    if defect == "unknown_encoding":
        page.Resources.Font.F1.Encoding = Name.UnknownEncoding
    elif defect == "unmapped_unicode":
        page.Resources.Font.F1.ToUnicode = pdf.make_stream(
            b"begincmap 1 beginbfchar <01> <0041> endbfchar endcmap"
        )
    elif defect == "nested_mcid":
        page.Contents = pdf.make_stream(
            b"/Span <</MCID 2>> BDC " + page.Contents.read_bytes() + b" EMC"
        )
    elif defect == "shared_structparents":
        pdf.pages[1].StructParents = page.StructParents
    elif defect == "cyclic_parent_tree":
        tree = pdf.make_indirect(Dictionary())
        tree.Kids = Array([tree])
        pdf.Root.StructTreeRoot.ParentTree = tree
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


def test_partial_actual_text_preserves_positioned_word_boundaries(tmp_path):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    page.Contents = pdf.make_stream(
        page.Contents.read_bytes().replace(
            b"(First page 0) Tj",
            b"[(First) -278] TJ /Span <</ActualText (page)>> BDC [(page) -278] TJ EMC (0) Tj",
        )
    )
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First page 0", "Second page 0"]
        assert ReadingOrderVerifier().check(str(path)).issues == []


@pytest.mark.parametrize(
    "kind", ["unknown_composite", "unknown_difference", "invalid_cmap"]
)
def test_actual_text_cannot_mask_unsupported_font(tmp_path, kind):
    pdf, path = tagged_pdf(tmp_path)
    font = pdf.pages[0].Resources.Font.F1
    if kind == "unknown_composite":
        font.Subtype = Name.Type0
        font.Encoding = Name.UnknownEncoding
        font.DescendantFonts = Array(
            [
                Dictionary(
                    Type=Name.Font,
                    Subtype=Name.CIDFontType2,
                    BaseFont=Name.TestFont,
                    CIDSystemInfo=Dictionary(
                        Registry="Adobe", Ordering="Identity", Supplement=0
                    ),
                )
            ]
        )
    elif kind == "unknown_difference":
        font.Encoding = Dictionary(
            BaseEncoding=Name.StandardEncoding,
            Differences=Array([70, Name.UnmappedGlyph]),
        )
    elif kind == "invalid_cmap":
        font.ToUnicode = pdf.make_stream(b"invalid cmap")
    for index, owner in enumerate(pdf.Root.StructTreeRoot.K):
        owner.ActualText = ("First", "Second")[index] + " page 0"
    pdf.save(path)
    with pytest.raises(IncompletePDFScanError, match="reading_order"):
        with require_complete_pdf_scan(True):
            ReadingOrderVerifier().check(str(path))
    assert ReadingOrderVerifier().check(str(path)).issues


@pytest.mark.parametrize("code", [127, 129, 141, 143, 144, 157])
def test_winansi_spare_bullet_codes_have_source_defined_unicode(tmp_path, code):
    pdf, path = tagged_pdf(tmp_path)
    page = pdf.pages[0]
    page.Resources.Font.F1.Encoding = Name.WinAnsiEncoding
    page.Contents = pdf.make_stream(
        page.Contents.read_bytes().replace(
            b"(First page 0)",
            b"(First " + bytes([code]) + b" page 0)",
        )
    )
    pdf.save(path)
    with require_complete_pdf_scan(True):
        assert texts(path) == ["First \u2022 page 0", "Second page 0"]
        assert ReadingOrderVerifier().check(str(path)).issues == []
