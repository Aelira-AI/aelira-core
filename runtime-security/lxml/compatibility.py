"""Record observable local compression and Schematron behavior on both wheels."""

from __future__ import annotations

import gzip
import io
import json
import lzma

from lxml import etree


def attempt(blob: bytes) -> dict[str, object]:
    try:
        tree = etree.parse(io.BytesIO(blob), etree.XMLParser(no_network=True))
    except (etree.XMLSyntaxError, OSError, ValueError) as error:
        return {"accepted": False, "error_type": type(error).__name__}
    return {"accepted": True, "text": tree.xpath("string(/campus/name)")}


source = b"<campus><name>local</name></campus>"
schema = etree.XML(
    b'<schema xmlns="http://purl.oclc.org/dsdl/schematron"><pattern><rule context="campus"><assert test="name">name required</assert></rule></pattern></schema>'
)
try:
    validator = etree.Schematron(schema)
    schematron = {
        "available": True,
        "valid": validator.validate(etree.XML(source)),
        "invalid_refused": not validator.validate(etree.XML(b"<campus/>")),
    }
except (etree.SchematronError, etree.XMLSyntaxError) as error:
    schematron = {"available": False, "error_type": type(error).__name__}

print(
    json.dumps(
        {
            "libxml": etree.LIBXML_VERSION,
            "gzip": attempt(gzip.compress(source)),
            "lzma": attempt(lzma.compress(source)),
            "schematron": schematron,
        },
        sort_keys=True,
    )
)
