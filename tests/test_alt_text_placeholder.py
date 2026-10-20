"""Alt text that is only a file name or a tool placeholder is not a description."""

import pytest

from src.education.alt_text_quality import alt_text_is_placeholder

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "nature1.png",
        "C:\\pics\\photo.JPG",
        "image001.emf",
        "IMG_1234.HEIC",
        "photo.jpeg",
        "scan.TIFF",
        "scan.tif",
        "logo.svg",
        "banner.webp",
        "diagram.bmp",
        "clip.wmf",
        "anim.gif",
        "/home/user/My Pictures/holiday.png",
        "https://example.edu/media/chart.png",
        "Screen Shot 2024-01-01 at 10.00.00 AM.png",
        "  photo.png  ",
        "PHOTO.PNG",
        "Picture 3",
        "Image 1",
        "image001",
        "Graphic 2",
        "Screenshot",
        "screen shot",
        "Screenshot 4",
        "IMG_1234",
        "picture",
    ],
)
def test_filenames_and_tool_placeholders_are_placeholders(text):
    assert alt_text_is_placeholder(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Diagram of a PNG compression pipeline",
        "Screenshot of the login page showing an error banner",
        "Image 3 shows enrolment rising each year",
        "Picture of a red bicycle leaning on a wall",
        "Bar chart of enrolment by year, rising from 120 to 480 students",
        "Photo of the lab, saved as lab.jpg",
        "See the chart; it is saved as chart.png",
        "photo.txt",
        "report.pdf",
        "png",
        "Picture 12345",
        "one two three four five six seven eight nine.png",
        "a.png\nb",
        "",
        "   ",
    ],
)
def test_real_descriptions_and_other_text_are_not_placeholders(text):
    assert alt_text_is_placeholder(text) is False


@pytest.mark.parametrize("value", [None, 3, b"photo.png", ["photo.png"]])
def test_non_text_is_not_a_placeholder(value):
    assert alt_text_is_placeholder(value) is False
