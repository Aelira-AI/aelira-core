"""Repository-authored synthetic fonts and PDFs. No third-party font bytes."""

import io

import pikepdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pikepdf import Array, Dictionary, Name


def tiny_truetype(*, alias=False, supplementary=False):
    builder = FontBuilder(1000, isTTF=True)
    names = [".notdef", "A", "B", "space"]
    builder.setupGlyphOrder(names)
    cmap = {65: "A", 66: "B", 32: "space"}
    if alias:
        cmap[0x391] = "A"
    if supplementary:
        cmap.pop(65)
        cmap[0x1F600] = "A"
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for index, name in enumerate(names):
        pen = TTGlyphPen(None)
        if name != "space":
            # Different authored silhouettes, deliberately simple test glyphs.
            pen.moveTo((50, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500 - index * 60, 700))
            pen.lineTo((50, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "AeliraTest", "styleName": "Regular"})
    builder.setupOS2(
        sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.font["head"].created = builder.font["head"].modified = 2082844800
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def cmap_bytes(mappings, width=2):
    lines = [
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Aelira-Test def /CMapType 2 def",
        f"1 begincodespacerange <{'00' * width}> <{'FF' * width}> endcodespacerange".encode(),
    ]
    items = sorted(mappings.items())
    for offset in range(0, len(items), 100):
        batch = items[offset : offset + 100]
        lines.append(f"{len(batch)} beginbfchar".encode())
        for code, text in batch:
            lines.append(
                f"<{code:0{width * 2}X}> <{text.encode('utf-16-be').hex().upper()}>".encode()
            )
        lines.append(b"endbfchar")
    lines.append(b"endcmap CMapName currentdict /CMap defineresource pop end end")
    return b"\n".join(lines)


def truetype_pdf(
    *, gid_map=None, mappings=None, alias=False, supplementary=False, codes=(1, 2)
):
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 400))
    descriptor = pdf.make_indirect(
        Dictionary(
            Type=Name.FontDescriptor,
            FontName=Name.AeliraTest,
            Flags=32,
            FontBBox=Array([0, 0, 600, 800]),
            ItalicAngle=0,
            Ascent=800,
            Descent=-200,
            CapHeight=700,
            StemV=80,
            FontFile2=pdf.make_stream(
                tiny_truetype(alias=alias, supplementary=supplementary)
            ),
        )
    )
    descendant = Dictionary(
        Type=Name.Font,
        Subtype=Name.CIDFontType2,
        BaseFont=Name.AeliraTest,
        CIDSystemInfo=Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        FontDescriptor=descriptor,
        DW=600,
    )
    if gid_map is not None:
        descendant.CIDToGIDMap = pdf.make_stream(gid_map)
    font = pdf.make_indirect(
        Dictionary(
            Type=Name.Font,
            Subtype=Name.Type0,
            BaseFont=Name.AeliraTest,
            Encoding=Name("/Identity-H"),
            DescendantFonts=Array([pdf.make_indirect(descendant)]),
        )
    )
    if mappings is not None:
        font.ToUnicode = pdf.make_stream(cmap_bytes(mappings))
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    data = b"".join(code.to_bytes(2, "big") for code in codes).hex().encode()
    page.Contents = pdf.make_stream(b"BT /F1 20 Tf 30 300 Td <" + data + b"> Tj ET")
    return pdf


def snapshot(pdf):
    output = io.BytesIO()
    pdf.save(output, deterministic_id=True)
    return output.getvalue()
