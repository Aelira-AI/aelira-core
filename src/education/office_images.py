"""Write images extracted from Office packages for pixel analysis."""

from typing import IO

from PIL import Image


def save_extracted_png(image: Image.Image, stream: IO[bytes]) -> None:
    """Write an image taken from a Word, PowerPoint or Excel package as PNG.

    Pillow 13 refuses to write 32-bit integer greyscale (mode ``I``) images as
    PNG; earlier releases clipped them to 16 bits. Converting to ``I;16`` first
    applies the same clipping, so a 32-bit or signed 16-bit TIFF embedded in a
    document still yields the PNG it did before and is still analysed.
    """
    if image.mode == "I":
        image = image.convert("I;16")
    image.save(stream, format="PNG")
