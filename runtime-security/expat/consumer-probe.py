"""Real Python XML consumer controls for the installed Expat shared object."""

import hashlib
import json
import pathlib
import subprocess
import ctypes
import os
import xml.dom.minidom
import xml.etree.ElementTree as ET
from xml.parsers import expat


lib_paths = sorted(
    {
        line.split()[-1]
        for line in pathlib.Path("/proc/self/maps").read_text().splitlines()
        if "/libexpat.so.1." in line and line.split()[-1].startswith("/")
    }
)
assert len(lib_paths) == 1, lib_paths
lib_path = pathlib.Path(lib_paths[0])
loaded_hash = hashlib.sha256(lib_path.read_bytes()).hexdigest()
assert loaded_hash == os.environ["AELIRA_EXPAT_EXPECTED_LIB_SHA256"], (str(lib_path), loaded_hash, os.environ["AELIRA_EXPAT_EXPECTED_LIB_SHA256"])
lib = ctypes.CDLL(str(lib_path))
lib.XML_ParserCreate.argtypes = [ctypes.c_char_p]
lib.XML_ParserCreate.restype = ctypes.c_void_p
lib.XML_GetBuffer.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.XML_GetBuffer.restype = ctypes.c_void_p
lib.XML_ParseBuffer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
lib.XML_ParseBuffer.restype = ctypes.c_int
lib.XML_GetErrorCode.argtypes = [ctypes.c_void_p]
lib.XML_GetErrorCode.restype = ctypes.c_int
lib.XML_ErrorString.argtypes = [ctypes.c_int]
lib.XML_ErrorString.restype = ctypes.c_char_p
lib.XML_ParserFree.argtypes = [ctypes.c_void_p]

direct = lib.XML_ParserCreate(None)
assert direct
try:
    # Establish a working parser before exercising rejection paths.
    buffer = lib.XML_GetBuffer(direct, 4)
    assert buffer
    ctypes.memmove(buffer, b"<r/>", 4)
    assert lib.XML_ParseBuffer(direct, 4, 1) == 1
finally:
    lib.XML_ParserFree(direct)

direct = lib.XML_ParserCreate(None)
assert direct
try:
    assert lib.XML_ParseBuffer(direct, 10, 0) == 0
    assert b"buffer" in lib.XML_ErrorString(lib.XML_GetErrorCode(direct)).lower()
    buffer = lib.XML_GetBuffer(direct, 10)
    assert buffer
    assert lib.XML_ParseBuffer(direct, 10000, 0) == 0
    assert lib.XML_ErrorString(lib.XML_GetErrorCode(direct)) == b"invalid argument", ("initial_capacity", lib.XML_GetErrorCode(direct), lib.XML_ErrorString(lib.XML_GetErrorCode(direct)))
    ctypes.memmove(buffer, b"<r/>", 4)
    assert lib.XML_ParseBuffer(direct, 4, 0) == 1
    assert lib.XML_ParseBuffer(direct, 10000, 1) == 0
    assert lib.XML_ErrorString(lib.XML_GetErrorCode(direct)) == b"invalid argument", ("continued_capacity", lib.XML_GetErrorCode(direct), lib.XML_ErrorString(lib.XML_GetErrorCode(direct)))
finally:
    lib.XML_ParserFree(direct)

# Full native capacity boundary controls: each refusal must leave a valid
# continuation possible on the same parser.
capacity_cases = 0
for final in (0, 1):
    for used in (0, 3, 4095):
        direct = lib.XML_ParserCreate(None)
        assert direct
        try:
            buffer = lib.XML_GetBuffer(direct, 4096)
            assert buffer
            content = b"<r>" + b"x" * 4089 + b"</r>"
            assert len(content) == 4096
            ctypes.memmove(buffer, content, len(content))
            if used:
                assert lib.XML_ParseBuffer(direct, used, 0) == 1
            remaining = 4096 - used
            assert lib.XML_ParseBuffer(direct, remaining + 1, final) == 0
            assert lib.XML_ErrorString(lib.XML_GetErrorCode(direct)) == b"invalid argument", ("boundary_capacity", final, used, lib.XML_GetErrorCode(direct), lib.XML_ErrorString(lib.XML_GetErrorCode(direct)))
            assert lib.XML_ParseBuffer(direct, remaining, 1) == 1
            capacity_cases += 1
        finally:
            lib.XML_ParserFree(direct)

utf16_valid = utf16_invalid = 0
contexts = (("<r>", "</r>"), ('<r a="', '"/>'), ("<r><![CDATA[", "]]></r>"), ("<r><!--", "--></r>"), ("<r><?pi ", "?></r>"))
valid_values = ("AΩ🙂", "\ud800\udc00", "\udbff\udfff", "\ud7ff\ue000")
invalid_values = ("\ud800A", "\ud800\ud800", "\udbff\ue000", "\udc00")

def check_utf16(values, expect_valid):
    count = 0
    for encoding, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
        for with_bom in (False, True):
            for prefix, suffix in contexts:
                for value in values:
                    data = (bom if with_bom else b"") + (prefix + value + suffix).encode(encoding, "surrogatepass")
                    chunks = ([data[:split], data[split:]] for split in range(len(data) + 1))
                    for parts in (*chunks, [data[index:index + 1] for index in range(len(data))]):
                        parser = expat.ParserCreate()
                        try:
                            for part in parts:
                                parser.Parse(part, False)
                            parser.Parse(b"", True)
                            accepted = True
                        except expat.ExpatError:
                            accepted = False
                        assert accepted is expect_valid, (encoding, with_bom, prefix, value)
                        count += 1
    return count

# The acceptance half runs first so a uniformly broken parser cannot pass
# by rejecting every input.
utf16_valid = check_utf16(valid_values, True)
utf16_invalid = check_utf16(invalid_values, False)
assert (utf16_valid, utf16_invalid, capacity_cases) == (2656, 2536, 6)

document = '<r xmlns:a="urn:a" a:k="v">Ω🙂<a:c>text</a:c></r>'
expected_root_tag = "r"
root = ET.fromstring(document)
assert root.tag == expected_root_tag
assert root.attrib["{urn:a}k"] == "v"
assert root.text == "Ω🙂"
assert root.find("{urn:a}c").text == "text"

pull = ET.XMLPullParser(events=("start", "end"))
for part in ("<r xmlns:a=", '"urn:a"><a:', "c>Ω🙂</a:c></r>"):
    pull.feed(part)
events = [(event, node.tag) for event, node in pull.read_events()]
assert events == [
    ("start", "r"),
    ("start", "{urn:a}c"),
    ("end", "{urn:a}c"),
    ("end", "r"),
]

dom = xml.dom.minidom.parseString(document)
assert dom.documentElement.getAttributeNS("urn:a", "k") == "v"
assert dom.getElementsByTagNameNS("urn:a", "c")[0].firstChild.data == "text"

external = '<!DOCTYPE r [<!ENTITY e SYSTEM "file:///nonexistent/aelira-expat-probe">]><r>&e;</r>'
try:
    ET.fromstring(external)
except ET.ParseError as error:
    assert "undefined entity" in str(error), str(error)
else:
    raise AssertionError("ElementTree unexpectedly resolved an external entity")
assert xml.dom.minidom.parseString(external).documentElement.toxml() == "<r/>"

parser = expat.ParserCreate(namespace_separator="}")
start_names = []
parser.StartElementHandler = lambda name, attrs: start_names.append(name)
parser.Parse(document.encode(), True)
assert start_names == ["r", "urn:a}c"]
internal = expat.ParserCreate()
internal_text = []
internal.CharacterDataHandler = internal_text.append
internal.Parse(b'<!DOCTYPE r [<!ENTITY x "retained">]><r>&x;</r>', True)
assert "".join(internal_text) == "retained"

package_version = subprocess.check_output(
    ["dpkg-query", "-W", "-f=${Version}", "libexpat1:" + os.environ["AELIRA_EXPAT_HOST_ARCH"]], text=True
).strip()
assert package_version == "2.7.4-1ubuntu0.2+aelira1", package_version
assert subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip() == os.environ["AELIRA_EXPAT_HOST_ARCH"]
assert hashlib.sha256(lib_path.read_bytes()).hexdigest() == os.environ["AELIRA_EXPAT_EXPECTED_LIB_SHA256"]
wide_path = lib_path.with_name("libexpatw.so.1.11.2")
assert hashlib.sha256(wide_path.read_bytes()).hexdigest() == os.environ["AELIRA_EXPAT_EXPECTED_WIDE_SHA256"]
wide_lib = ctypes.CDLL(str(wide_path))
wide_lib.XML_ParserCreate.argtypes = [ctypes.c_void_p]
wide_lib.XML_ParserCreate.restype = ctypes.c_void_p
wide_lib.XML_Parse.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
wide_lib.XML_Parse.restype = ctypes.c_int
wide_lib.XML_ParserFree.argtypes = [ctypes.c_void_p]
wide_cases = 0
for encoding in ("utf-16-le", "utf-16-be"):
    for value, valid in (("AΩ🙂", True), ("\ud800\udc00", True), ("\udbff\udfff", True), ("\ud800A", False), ("\ud800\ud800", False), ("\udc00", False)):
        data = ("<r>" + value + "</r>").encode(encoding, "surrogatepass")
        for split in range(len(data) + 1):
            wide_parser = wide_lib.XML_ParserCreate(None)
            assert wide_parser
            try:
                status = wide_lib.XML_Parse(wide_parser, data[:split], split, 0)
                if status == 1:
                    status = wide_lib.XML_Parse(wide_parser, data[split:], len(data) - split, 1)
                assert (status == 1) is valid
                wide_cases += 1
            finally:
                wide_lib.XML_ParserFree(wide_parser)
assert wide_cases == 232
result = {
    "architecture": subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip(),
    "package_version": package_version,
    "expat_version": expat.EXPAT_VERSION,
    "loaded_library": str(lib_path),
    "loaded_library_sha256": hashlib.sha256(lib_path.read_bytes()).hexdigest(),
    "installed_wide_library": str(wide_path),
    "installed_wide_library_sha256": hashlib.sha256(wide_path.read_bytes()).hexdigest(),
    "wide_utf16_cases": wide_cases,
    "elementtree_unicode_namespace": "pass",
    "elementtree_incremental": "pass",
    "minidom_unicode_namespace": "pass",
    "external_entity_policy": "ElementTree rejects; minidom omits",
    "pyexpat_namespace": "pass",
    "internal_entity_dtd": "pass",
    "direct_parse_buffer_capacity": "pass",
    "capacity_boundary_cases": capacity_cases,
    "utf16_valid_cases": utf16_valid,
    "utf16_invalid_rejected_cases": utf16_invalid,
    "features": dict(expat.features),
}
print(json.dumps(result, indent=2, sort_keys=True))
