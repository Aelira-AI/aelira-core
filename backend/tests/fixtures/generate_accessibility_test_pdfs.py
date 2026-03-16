"""Generate test PDF fixtures with known accessibility issues.

NOTE: This is a SEPARATE file from generate_test_pdfs.py which creates
the existing syllabus/paper/lecture fixtures. Do not overwrite that file.
"""
import pikepdf
from pikepdf import Array, Dictionary, Name, String
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "pdfs"


def create_forms_links_pdf():
    """Create a PDF with unlabeled form fields and vague link text."""
    pdf = pikepdf.new()
    page = pikepdf.Page(pikepdf.Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
        "/Contents": pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (Form test page) Tj ET"),
        "/Resources": Dictionary({
            "/Font": Dictionary({
                "/F1": pdf.make_indirect(Dictionary({
                    "/Type": Name.Font,
                    "/Subtype": Name("/Type1"),
                    "/BaseFont": Name("/Helvetica"),
                })),
            }),
        }),
    }))
    pdf.pages.append(page)

    # Add an AcroForm with a field missing /TU
    field = pdf.make_indirect(Dictionary({
        "/Type": Name("/Annot"),
        "/Subtype": Name("/Widget"),
        "/FT": Name("/Tx"),  # Text field
        "/T": String("name_field"),
        "/Rect": Array([72, 680, 200, 700]),
        "/P": pdf.pages[0].obj,
    }))
    pdf.Root["/AcroForm"] = pdf.make_indirect(Dictionary({
        "/Fields": Array([field]),
    }))
    pdf.pages[0].obj["/Annots"] = Array([field])

    # Add a link annotation with vague text
    link = pdf.make_indirect(Dictionary({
        "/Type": Name("/Annot"),
        "/Subtype": Name("/Link"),
        "/Rect": Array([72, 640, 150, 660]),
        "/A": Dictionary({
            "/Type": Name("/Action"),
            "/S": Name("/URI"),
            "/URI": String("https://example.com/report"),
        }),
        # No /Contents — this is the issue
    }))
    pdf.pages[0].obj["/Annots"].append(link)

    out = FIXTURES_DIR / "test_forms_links.pdf"
    pdf.save(str(out))
    print(f"Created {out}")


def create_math_content_pdf():
    """Create a PDF with math-like content for MathFixer testing."""
    pdf = pikepdf.new()
    # Simple page with text that looks like equations
    content = b"BT /F1 12 Tf 72 720 Td (E = mc^2) Tj 0 -20 Td (x^2 + 2x + 1 = 0) Tj ET"
    page = pikepdf.Page(pikepdf.Dictionary({
        "/Type": Name.Page,
        "/MediaBox": [0, 0, 612, 792],
        "/Contents": pdf.make_stream(content),
        "/Resources": Dictionary({
            "/Font": Dictionary({
                "/F1": pdf.make_indirect(Dictionary({
                    "/Type": Name.Font,
                    "/Subtype": Name("/Type1"),
                    "/BaseFont": Name("/Helvetica"),
                })),
            }),
        }),
    }))
    pdf.pages.append(page)
    out = FIXTURES_DIR / "test_math_content.pdf"
    pdf.save(str(out))
    print(f"Created {out}")


if __name__ == "__main__":
    create_forms_links_pdf()
    create_math_content_pdf()
