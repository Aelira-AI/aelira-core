"""Regenerate tests/journeys/fixtures/picture-without-alt.docx.

A one-page document with a heading, a sentence and a flat-colour picture that
has no alternative text, and no title in its properties. Run it with python-docx
and Pillow available, for example from a built API image:

    docker run --rm --entrypoint python -v "$PWD:/work" <api-image> \
        /work/scripts/generate_journey_docx_fixture.py /work/tests/journeys/fixtures
"""

import io
import sys
from pathlib import Path

from docx import Document
from docx.shared import Inches
from PIL import Image


def main(target_dir: str) -> None:
    picture = io.BytesIO()
    Image.new("RGB", (96, 64), (31, 58, 95)).save(picture, "PNG")
    picture.seek(0)

    document = Document()
    document.core_properties.title = ""
    document.core_properties.author = "Journey fixture"
    document.add_heading("Plant growth practical", level=1)
    document.add_paragraph("Record the height of each seedling every day for a week.")
    document.add_picture(picture, width=Inches(1.5))
    document.add_paragraph("Enter the results in the table provided.")
    destination = Path(target_dir) / "picture-without-alt.docx"
    document.save(destination)
    print(destination)


if __name__ == "__main__":
    main(sys.argv[1])
