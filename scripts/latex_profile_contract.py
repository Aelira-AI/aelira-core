"""Research-only observations of saved PDFs and byte-bound veraPDF CLI reports.

Neither structure observations nor declared metadata certify accessibility.
The caller must bind the validator invocation to the same bytes before/after.
"""

from collections import Counter
import hashlib
from io import BytesIO
from pathlib import Path
import re
import zlib

from defusedxml import ElementTree as ET
import pikepdf

MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_STREAM_TOTAL = 8 * 1024 * 1024
MAX_NODES = 10000
MAX_DEPTH = 64
MAX_TEXT = 4096
MAX_FAILED_RULES = 256
MATHML_NS = "http://www.w3.org/1998/Math/MathML"


def _xml(data, limit):
    if not data or len(data) > limit:
        raise ValueError("XML empty or exceeds byte limit")
    try:
        root = ET.fromstring(
            data, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except Exception as exc:
        raise ValueError("Unsafe or malformed XML") from exc
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("XML traversal limit exceeded")
        stack.extend((child, depth + 1) for child in node)
    return root


def _one(parent, name):
    nodes = parent.findall(name)
    if len(nodes) != 1:
        raise ValueError(f"Expected exactly one {name}")
    return nodes[0]


def _integer(node, key):
    raw = node.get(key, "")
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError(f"Missing or invalid count: {key}")
    return int(raw)


def parse_verapdf_report(
    raw: bytes, profile: str, expected_path: Path, expected_size: int
) -> dict:
    """Reject incomplete/ambiguous CLI XML; report only the observed outcome."""
    if profile not in {"ua1", "ua2"} or expected_size < 0:
        raise ValueError("Invalid expected profile or size")
    root = _xml(raw, MAX_REPORT_BYTES)
    if root.tag != "report":
        raise ValueError("Expected veraPDF report")
    versions = {}
    for release in _one(root, "buildInformation").findall("releaseDetails"):
        component, version = release.get("id"), release.get("version")
        if not component or not version or component in versions:
            raise ValueError("Missing or duplicate validator version")
        versions[component] = version
    if "core" not in versions:
        raise ValueError("Missing veraPDF core version")
    job = _one(_one(root, "jobs"), "job")
    item = _one(job, "item")
    if (
        _one(item, "name").text != str(expected_path)
        or _integer(item, "size") != expected_size
    ):
        raise ValueError("Validator input path or size mismatch")
    report = _one(job, "validationReport")
    profile_names = {
        f"PDF/UA-{profile[-1]} validation profile",
        f"PDF/UA-{profile[-1]} + Tagged PDF validation profile",
    }
    profile_name = report.get("profileName")
    if profile_name not in profile_names:
        raise ValueError("Validator profile mismatch")
    if report.get("jobEndStatus") != "normal":
        raise ValueError("Validator job did not end normally")
    if report.get("isCompliant") not in {"true", "false"}:
        raise ValueError("Missing compliance outcome")
    compliant = report.get("isCompliant") == "true"
    details = _one(report, "details")
    counts = {
        key: _integer(details, key)
        for key in ("passedRules", "failedRules", "passedChecks", "failedChecks")
    }
    if counts["passedChecks"] + counts["failedChecks"] <= 0:
        raise ValueError("No executed checks")
    if compliant != (counts["failedRules"] == counts["failedChecks"] == 0):
        raise ValueError("Inconsistent compliance counts")
    if bool(counts["failedRules"]) != bool(counts["failedChecks"]):
        raise ValueError("Inconsistent failed counts")
    if counts["failedChecks"] < counts["failedRules"]:
        raise ValueError("Failed rules exceed checks")
    failed = []
    for rule in details.findall("rule"):
        if rule.get("status") not in {"passed", "failed"}:
            raise ValueError("Invalid rule status")
        if rule.get("status") == "failed":
            identity = {
                key: rule.get(key) for key in ("specification", "clause", "testNumber")
            }
            if not all(identity.values()):
                raise ValueError("Incomplete failed rule identity")
            failed.append(identity)
    if len(failed) != counts["failedRules"]:
        raise ValueError("Failed rule count mismatch")
    summary = _one(root, "batchSummary")
    if _integer(summary, "totalJobs") != 1:
        raise ValueError("Batch job count mismatch")
    for key in ("failedToParse", "encrypted", "outOfMemory", "veraExceptions"):
        if _integer(summary, key) != 0:
            raise ValueError(f"Validator error: {key}")
    reports = _one(summary, "validationReports")
    if (reports.text or "").strip() != "1":
        raise ValueError("Batch report count mismatch")
    if (
        _integer(reports, "compliant") != int(compliant)
        or _integer(reports, "nonCompliant") != int(not compliant)
        or _integer(reports, "failedJobs") != 0
    ):
        raise ValueError("Batch compliance count mismatch")
    for node in root.iter():
        if node.tag.lower() in {"exception", "error", "errorreport", "taskexception"}:
            raise ValueError("Validator reported an error")
    return {
        "status": "passed" if compliant else "failed",
        "profile": profile,
        "profile_name": profile_name,
        "validator_versions": versions,
        "counts": counts,
        "failed_rules": failed[:MAX_FAILED_RULES],
        "failed_rules_truncated": len(failed) > MAX_FAILED_RULES,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
    }


def inspect_pdf(path: Path) -> dict:
    """Observe bounded PDF objects, namespaces, and actual embedded XML bytes.

    Indirect IDs use object/generation numbers; direct IDs use deterministic
    traversal paths. Unsupported stream filters are explicit unknown evidence.
    """
    with Path(path).open("rb") as source:
        data = source.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDF exceeds research inspection byte limit")
    limits = {"truncated": False, "events": [], "events_truncated": False}

    def event(kind, location):
        limits["truncated"] |= kind in {
            "depth_limit",
            "node_limit",
            "text_limit",
            "stream_limit",
            "container_limit",
        }
        if len(limits["events"]) < 256:
            limits["events"].append({"kind": kind, "location": location})
        else:
            limits["events_truncated"] = True

    def text(value, location="text"):
        value = str(value)
        if len(value) > MAX_TEXT:
            event("text_limit", location)
        return value[:MAX_TEXT]

    def identity(obj, location):
        if isinstance(obj, pikepdf.Object) and obj.is_indirect:
            return f"{obj.objgen[0]} {obj.objgen[1]} R"
        return f"direct:{location}"

    serial_budget = [MAX_NODES]

    def describe(obj, location, active=None, depth=0):
        active = set() if active is None else active
        serial_budget[0] -= 1
        if depth > MAX_DEPTH or serial_budget[0] < 0:
            event("depth_limit" if depth > MAX_DEPTH else "node_limit", location)
            return {"truncated": True}
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            oid = identity(obj, location)
            if oid in active:
                event("cycle", location)
                return {"reference": oid, "cycle": True}
            active = active | {oid}
            if isinstance(obj, pikepdf.Stream):
                return {"stream": oid}
            if isinstance(obj, pikepdf.Array):
                if len(obj) > 256:
                    event("container_limit", location)
                return [
                    describe(v, f"{location}[{i}]", active, depth + 1)
                    for i, v in enumerate(obj[:256])
                ]
            keys = sorted(obj.keys())
            if len(keys) > 256:
                event("container_limit", location)
            return {
                key: describe(obj[key], location + key, active, depth + 1)
                for key in keys[:256]
            }
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        return text(obj, location)

    stream_total = [0]

    def stream_bytes(stream, location):
        if not isinstance(stream, pikepdf.Stream):
            raise ValueError("missing_embedded_stream")
        if stream_total[0] >= MAX_STREAM_TOTAL:
            event("stream_limit", location)
            raise ValueError("total_stream_limit")
        raw = stream.read_raw_bytes()
        if len(raw) > MAX_XML_BYTES:
            event("stream_limit", location)
            raise ValueError("encoded_stream_limit")
        filters = stream.get("/Filter")
        if isinstance(filters, pikepdf.Array):
            filters = filters[0] if len(filters) == 1 else "unsupported"
        if filters is None:
            decoded = raw
        elif str(filters) == "/FlateDecode" and stream.get("/DecodeParms") is None:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(raw, MAX_XML_BYTES + 1)
            if len(decoded) > MAX_XML_BYTES or decoder.unconsumed_tail:
                event("stream_limit", location)
                raise ValueError("decoded_stream_limit")
            if not decoder.eof or decoder.unused_data:
                raise ValueError("invalid_flate_stream")
        else:
            raise ValueError("unsupported_stream_filter")
        stream_total[0] += len(decoded)
        if stream_total[0] > MAX_STREAM_TOTAL:
            event("stream_limit", location)
            raise ValueError("total_stream_limit")
        return decoded

    result = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "metadata": {},
        "structure": {"nodes": [], "namespaces": []},
        "formula_nodes": [],
        "associated_files": [],
        "mathml_structure_nodes": [],
        "figures": [],
        "tables": [],
        "links": [],
        "limits": limits,
    }
    with pikepdf.open(BytesIO(data)) as pdf:
        result.update(
            pdf_version=pdf.pdf_version, language=text(pdf.Root.get("/Lang", ""))
        )
        result["metadata"]["document_info"] = {
            key[1:].lower(): text(pdf.docinfo.get(key, ""))
            for key in ("/Title", "/Author")
        }
        metadata = pdf.Root.get("/Metadata")
        if metadata is not None:
            try:
                raw = stream_bytes(metadata, "catalog/Metadata")
                xmp = _xml(raw, MAX_XML_BYTES)
                values = {}
                for key in ("title", "creator"):
                    elements = xmp.findall(
                        f".//{{http://purl.org/dc/elements/1.1/}}{key}"
                    )
                    values[key] = [
                        text(" ".join(" ".join(e.itertext()).split())) for e in elements
                    ]
                result["metadata"]["xmp"] = {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "values": values,
                    "declared_pdfua": {
                        key: [
                            text(e.text or "")
                            for e in xmp.findall(
                                f".//{{http://www.aiim.org/pdfua/ns/id/}}{key}"
                            )
                        ]
                        for key in ("part", "rev", "amd")
                    },
                }
            except (ValueError, zlib.error, pikepdf.PdfError) as exc:
                result["metadata"]["xmp"] = {"error": str(exc)}
        root = pdf.Root.get("/StructTreeRoot")
        result["structure"]["present"] = isinstance(root, pikepdf.Dictionary)
        mark = pdf.Root.get("/MarkInfo")
        result["structure"]["marked"] = (
            bool(mark.get("/Marked", False))
            if isinstance(mark, pikepdf.Dictionary)
            else False
        )
        seen, active = set(), set()
        walk_budget = [MAX_NODES]

        def associated(owner, location):
            af = owner.get("/AF")
            if af is None:
                return
            if not isinstance(af, pikepdf.Array):
                event("invalid_associated_files", location)
                return
            if len(af) > 256:
                event("container_limit", location + "/AF")
            for index, spec in enumerate(af[:256]):
                if len(result["associated_files"]) >= MAX_NODES:
                    event("node_limit", location + "/AF")
                    break
                loc = f"{location}/AF[{index}]"
                record = {
                    "owner_id": identity(owner, location),
                    "id": identity(spec, loc),
                }
                result["associated_files"].append(record)
                if not isinstance(spec, pikepdf.Dictionary):
                    record["error"] = "invalid_file_specification"
                    continue
                record.update(
                    filename=text(spec.get("/UF", spec.get("/F", ""))),
                    relationship=text(spec.get("/AFRelationship", "")),
                )
                ef = spec.get("/EF")
                stream = (
                    ef.get("/UF", ef.get("/F"))
                    if isinstance(ef, pikepdf.Dictionary)
                    else None
                )
                try:
                    raw = stream_bytes(stream, loc)
                    record.update(
                        stream_id=identity(stream, loc + "/EF"),
                        subtype=text(stream.get("/Subtype", "")),
                        sha256=hashlib.sha256(raw).hexdigest(),
                        size_bytes=len(raw),
                    )
                    xml = _xml(raw, MAX_XML_BYTES)
                    tags = Counter(element.tag for element in xml.iter())
                    math_elements = sum(
                        n
                        for tag, n in tags.items()
                        if tag.startswith("{" + MATHML_NS + "}")
                    )
                    meaningful = any(
                        e.tag.startswith("{" + MATHML_NS + "}")
                        and e.tag != "{" + MATHML_NS + "}math"
                        for e in xml.iter()
                    )
                    record["xml"] = {
                        "root_tag": xml.tag,
                        "element_count": sum(tags.values()),
                        "mathml_elements": math_elements,
                        "mathml_root": xml.tag == "{" + MATHML_NS + "}math",
                        "has_mathml_content": xml.tag == "{" + MATHML_NS + "}math"
                        and meaningful,
                        "tags": dict(sorted(tags.items())),
                        "text_excerpt": text(" ".join(xml.itertext())),
                    }
                except (ValueError, zlib.error, pikepdf.PdfError) as exc:
                    record["error"] = str(exc)

        def walk(node, location, depth=0, parent_id=None):
            walk_budget[0] -= 1
            if depth > MAX_DEPTH or walk_budget[0] < 0:
                event("depth_limit" if depth > MAX_DEPTH else "node_limit", location)
                return
            if isinstance(node, pikepdf.Array):
                for index, child in enumerate(node):
                    if walk_budget[0] <= 0:
                        event("node_limit", location)
                        break
                    walk(child, f"{location}[{index}]", depth + 1, parent_id)
                return
            if not isinstance(node, pikepdf.Dictionary):
                return
            oid = identity(node, location)
            if oid in active:
                event("structure_cycle", oid)
                return
            if oid in seen:
                event("repeated_structure_reference", oid)
                return
            seen.add(oid)
            active.add(oid)
            role = text(node.get("/S", ""))
            ns = node.get("/NS")
            ns_uri = (
                text(ns.get("/NS", "")) if isinstance(ns, pikepdf.Dictionary) else None
            )
            record = {
                "id": oid,
                "role": role,
                "namespace": ns_uri,
                "parent_id": parent_id,
                "structure_id": text(node.get("/ID", "")),
                "title": text(node.get("/T", "")),
                "language": text(node.get("/Lang", "")),
                "object_type": text(node.get("/Type", "")),
                "mcid": describe(node.get("/MCID"), location + "/MCID"),
                "page_id": (
                    identity(node["/Pg"], location + "/Pg") if "/Pg" in node else None
                ),
                "object_reference": (
                    identity(node["/Obj"], location + "/Obj")
                    if "/Obj" in node
                    else None
                ),
                "namespace_id": (
                    identity(ns, location + "/NS") if ns is not None else None
                ),
                "alt": text(node.get("/Alt", "")),
                "actual_text": text(node.get("/ActualText", "")),
                "attributes": describe(node.get("/A"), location + "/A"),
            }
            result["structure"]["nodes"].append(record)
            if role == "/Formula":
                result["formula_nodes"].append(record)
            if ns_uri == MATHML_NS:
                result["mathml_structure_nodes"].append(record)
            if role == "/Figure":
                result["figures"].append(record)
            if role in {"/Table", "/TR", "/TH", "/TD", "/THead", "/TBody", "/TFoot"}:
                result["tables"].append(record)
            associated(node, location)
            walk(node.get("/K"), location + "/K", depth + 1, oid)
            active.remove(oid)

        associated(pdf.Root, "catalog")

        def destination(value, location):
            if isinstance(value, pikepdf.Array):
                if len(value) > 16:
                    event("container_limit", location)
                return [
                    (
                        identity(item, f"{location}[{index}]")
                        if isinstance(item, pikepdf.Dictionary)
                        else describe(item, f"{location}[{index}]")
                    )
                    for index, item in enumerate(value[:16])
                ]
            return describe(value, location)

        for index, page in enumerate(pdf.pages):
            if index >= MAX_NODES:
                event("node_limit", "pages")
                break
            annotations = page.obj.get("/Annots")
            if not isinstance(annotations, pikepdf.Array):
                continue
            for number, annotation in enumerate(annotations):
                if number >= MAX_NODES or len(result["links"]) >= MAX_NODES:
                    event("node_limit", "annotations")
                    break
                if (
                    not isinstance(annotation, pikepdf.Dictionary)
                    or annotation.get("/Subtype") != pikepdf.Name.Link
                ):
                    continue
                loc = f"pages[{index}]/Annots[{number}]"
                action = annotation.get("/A")
                link = {
                    "id": identity(annotation, loc),
                    "page_id": identity(page.obj, f"pages[{index}]"),
                    "destination": destination(annotation.get("/Dest"), loc + "/Dest"),
                    "contents": text(annotation.get("/Contents", "")),
                    "struct_parent": describe(
                        annotation.get("/StructParent"), loc + "/StructParent"
                    ),
                }
                if isinstance(action, pikepdf.Dictionary):
                    link["action"] = {
                        "type": text(action.get("/S", "")),
                        "uri": text(action.get("/URI", "")),
                        "destination": destination(action.get("/D"), loc + "/A/D"),
                    }
                result["links"].append(link)
        if isinstance(root, pikepdf.Dictionary):
            result["structure"]["role_map"] = describe(
                root.get("/RoleMap"), "structure/RoleMap"
            )
            namespaces = root.get("/Namespaces")
            result["structure"]["namespaces"] = describe(
                namespaces, "structure/Namespaces"
            )
            walk(root.get("/K"), "structure/K")
        result["structure"]["node_count"] = len(result["structure"]["nodes"])
    return result
