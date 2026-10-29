"""Find and remove active content in Office Open XML and PDF uploads.

The upload validator rates a file HIGH when it carries macros or content that
runs or loads something when the file is opened. ``rebuild_ooxml`` and
``rebuild_pdf`` return a copy without that content. They raise
``SanitizationError`` when they cannot produce one, and never hand back the
original bytes in place of a rebuild.

An Office Open XML package finds its parts through ``[Content_Types].xml`` and
its ``.rels`` relationships, so parts are identified by content type and
relationship type, not by file name. Removing a part also removes every
relationship, content type entry and document element that names it, so the
rebuilt package stays readable.
"""

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from lxml import etree

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# Relationship types, by their final path segment in lower case, whose target
# is active content: VBA projects and their data, macro keyboard and toolbar
# customisations, ActiveX controls, Excel 4 macro sheets, embedded or linked
# OLE objects and packages, custom ribbons that call macros, and attached
# templates, which Word loads (with their macros) when the document opens.
ACTIVE_RELATIONSHIP_TYPES = frozenset(
    {
        "vbaproject",
        "wordvbadata",
        "keymapcustomizations",
        "attachedtoolbars",
        "control",
        "activexcontrolbinary",
        "xlmacrosheet",
        "xlintlmacrosheet",
        "oleobject",
        "package",
        "extensibility",
        "attachedtemplate",
    }
)

# External relationships of these types load content when the file is opened.
# Hyperlinks and other external relationships are kept.
EXTERNAL_LOADING_TYPES = frozenset(
    {"attachedtemplate", "oleobject", "frame", "subdocument"}
)

# Content types of active parts, matched as substrings of the lower-case type.
ACTIVE_CONTENT_TYPE_MARKERS = (
    "vbaproject",
    "vbadata",
    "keymapcustomizations",
    "attachedtoolbars",
    "activex",
    "macrosheet",
    "oleobject",
)

# A rebuilt package carries no macros, so its main part takes the type of the
# macro-free format that the upload routes accept.
MACRO_ENABLED_MAIN_TYPES = {
    "application/vnd.ms-word.document.macroenabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    ),
    "application/vnd.ms-excel.sheet.macroenabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ),
    "application/vnd.ms-powerpoint.presentation.macroenabled.main+xml": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
    ),
}

# Excel defined names that run a macro when the workbook opens or closes.
AUTO_RUN_NAMES = frozenset(
    {
        "auto_open",
        "auto_close",
        "auto_activate",
        "auto_deactivate",
        "_xlnm.auto_open",
        "_xlnm.auto_close",
        "_xlnm.auto_activate",
        "_xlnm.auto_deactivate",
    }
)

# Containers that are left empty, and so invalid, once their only children
# are removed.
EMPTY_CONTAINERS = frozenset(
    {
        "AlternateContent",
        "Choice",
        "Fallback",
        "oleObjects",
        "controls",
        "externalReferences",
        "definedNames",
    }
)

_MAX_XML_BYTES = 64 * 1024 * 1024
# Decompressed size limits. The validator refuses an archive whose compression
# ratio suggests a zip bomb; these bound what is read even when it does not.
_MAX_PART_BYTES = 256 * 1024 * 1024
_MAX_PACKAGE_BYTES = 512 * 1024 * 1024
_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False
)


class SanitizationError(Exception):
    """A file rated HIGH could not be rebuilt without its active content."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _kind(relationship_type: str) -> str:
    return relationship_type.rstrip("/").rsplit("/", 1)[-1].lower()


def _is_active_type(content_type: Optional[str]) -> bool:
    lowered = (content_type or "").lower()
    return any(marker in lowered for marker in ACTIVE_CONTENT_TYPE_MARKERS)


def _rels_source(rels_name: str) -> str:
    """The part a .rels file describes, or "" for the package relationships."""
    folder = posixpath.dirname(posixpath.dirname(rels_name))
    base = posixpath.basename(rels_name)[: -len(".rels")]
    return posixpath.join(folder, base) if base else ""


def _rels_name(part: str) -> str:
    return posixpath.join(
        posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
    )


def _resolve(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _parse(data: bytes, name: str) -> etree._Element:
    if len(data) > _MAX_XML_BYTES:
        raise SanitizationError("package_xml_too_large")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise SanitizationError("package_xml_has_doctype")
    try:
        return etree.fromstring(data, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise SanitizationError("package_xml_unreadable") from exc


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _drop(element: etree._Element) -> None:
    """Remove an element, keeping any text that followed it."""
    parent = element.getparent()
    if parent is None:
        return
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


@dataclass
class Relationship:
    rels_name: str
    element: etree._Element
    id: str
    kind: str
    external: bool
    target: Optional[str]  # resolved internal part name, lower case


@dataclass
class Package:
    """The parts of an Office Open XML package and how they refer to each other."""

    infos: List[zipfile.ZipInfo]
    data: Dict[str, bytes]
    names: Dict[str, str]  # lower-case name -> member name
    content_types: Optional[etree._Element]
    content_types_name: Optional[str]
    overrides: Dict[str, str] = field(default_factory=dict)  # lower name -> type
    defaults: Dict[str, str] = field(default_factory=dict)  # extension -> type
    rels: Dict[str, etree._Element] = field(default_factory=dict)
    relationships: List[Relationship] = field(default_factory=list)

    def content_type(self, name: str) -> Optional[str]:
        lowered = name.lower()
        if lowered in self.overrides:
            return self.overrides[lowered]
        extension = posixpath.splitext(lowered)[1].lstrip(".")
        return self.defaults.get(extension)


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: List[int]
) -> bytes:
    """Read one member within the per-part and whole-package limits.

    Sizes are checked as declared before decompressing, and the read is
    bounded, so a member that declares a small size cannot expand past it.
    """
    if info.file_size > _MAX_PART_BYTES or info.file_size > budget[0]:
        raise SanitizationError("package_too_large")
    try:
        with archive.open(info) as handle:
            data = handle.read(info.file_size + 1)
    except Exception as exc:
        raise SanitizationError("package_unreadable") from exc
    if len(data) != info.file_size:
        raise SanitizationError("package_unreadable")
    budget[0] -= len(data)
    return data


def read_package(content: bytes, *, all_parts: bool = False) -> Package:
    """Read a package's content types and relationships, or raise.

    Only ``[Content_Types].xml`` and the ``.rels`` parts are decompressed
    unless ``all_parts`` is set, which a rebuild needs. Both are bounded by
    the declared sizes and by a limit for the whole package.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SanitizationError("package_unreadable") from exc
    with archive:
        infos = archive.infolist()
        names: Dict[str, str] = {}
        for info in infos:
            lowered = info.filename.lower()
            if lowered in names:
                raise SanitizationError("package_duplicate_part")
            names[lowered] = info.filename
        budget = [_MAX_PACKAGE_BYTES]
        data = {
            info.filename: _read_member(archive, info, budget)
            for info in infos
            if all_parts
            or info.filename.lower() == "[content_types].xml"
            or info.filename.lower().endswith(".rels")
        }

    content_types_name = names.get("[content_types].xml")
    content_types = (
        _parse(data[content_types_name], content_types_name)
        if content_types_name
        else None
    )
    package = Package(
        infos=infos,
        data=data,
        names=names,
        content_types=content_types,
        content_types_name=content_types_name,
    )
    if content_types is not None:
        for element in content_types:
            local = _local(element)
            if local == "Override":
                part = element.get("PartName", "").lstrip("/").lower()
                package.overrides[part] = element.get("ContentType", "")
            elif local == "Default":
                extension = element.get("Extension", "").lower()
                package.defaults[extension] = element.get("ContentType", "")

    for lowered, name in names.items():
        if not lowered.endswith(".rels"):
            continue
        root = _parse(data[name], name)
        package.rels[name] = root
        source = _rels_source(name)
        for element in root:
            if _local(element) != "Relationship":
                continue
            external = element.get("TargetMode", "").lower() == "external"
            target = None
            if not external:
                target = _resolve(source, element.get("Target", "")).lower()
            package.relationships.append(
                Relationship(
                    rels_name=name,
                    element=element,
                    id=element.get("Id", ""),
                    kind=_kind(element.get("Type", "")),
                    external=external,
                    target=target,
                )
            )
    return package


@dataclass
class ActiveContent:
    """Active content an Office Open XML package declares."""

    macro_sheets: Set[str] = field(default_factory=set)
    external_loaders: Set[str] = field(default_factory=set)  # relationship kinds


def find_active_content(content: bytes) -> Optional[ActiveContent]:
    """Report Excel 4 macro sheets and content loaded from outside the file.

    Returns None when the archive opens but its content types or relationships
    cannot be read.
    """
    try:
        package = read_package(content)
    except SanitizationError as exc:
        # A file that is not a readable zip is reported by the archive checks.
        return ActiveContent() if exc.reason == "package_unreadable" else None
    found = ActiveContent()
    for lowered, name in package.names.items():
        if "macrosheet" in (package.content_type(name) or "").lower():
            found.macro_sheets.add(name)
    for relationship in package.relationships:
        if relationship.kind in {"xlmacrosheet", "xlintlmacrosheet"}:
            if relationship.target:
                found.macro_sheets.add(
                    package.names.get(relationship.target, relationship.target)
                )
        if relationship.external and relationship.kind in EXTERNAL_LOADING_TYPES:
            found.external_loaders.add(relationship.kind)
    return found


def _active_parts(package: Package) -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """Lower-case part names and (rels file, relationship id) pairs to remove."""
    # Embedded objects live in an embeddings folder whatever their type.
    removed_parts = {
        lowered
        for lowered, name in package.names.items()
        if _is_active_type(package.content_type(name))
        or posixpath.basename(lowered) == "vbaproject.bin"
        or "embeddings" in lowered.split("/")[:-1]
    }
    removed_relationships: Set[Tuple[str, str]] = set()

    while True:
        before = (len(removed_parts), len(removed_relationships))
        for relationship in package.relationships:
            source = _rels_source(relationship.rels_name).lower()
            if source in removed_parts:
                continue
            active = (
                relationship.kind in ACTIVE_RELATIONSHIP_TYPES
                or (
                    relationship.external
                    and relationship.kind in EXTERNAL_LOADING_TYPES
                )
                or (
                    relationship.target is not None
                    and relationship.target in removed_parts
                )
            )
            if not active:
                continue
            removed_relationships.add((relationship.rels_name, relationship.id))
            if relationship.target is not None and relationship.target in package.names:
                removed_parts.add(relationship.target)

        # A part reached only from removed parts goes with them.
        kept_targets = {
            relationship.target
            for relationship in package.relationships
            if relationship.target is not None
            and _rels_source(relationship.rels_name).lower() not in removed_parts
            and (relationship.rels_name, relationship.id) not in removed_relationships
        }
        for relationship in package.relationships:
            source = _rels_source(relationship.rels_name).lower()
            if (
                source in removed_parts
                and relationship.target is not None
                and relationship.target in package.names
                and relationship.target not in kept_targets
            ):
                removed_parts.add(relationship.target)

        if (len(removed_parts), len(removed_relationships)) == before:
            return removed_parts, removed_relationships


def _drop_references(root: etree._Element, ids: Set[str]) -> List[etree._Element]:
    """Remove elements that name a removed relationship; return them."""
    dropped = []
    for element in list(root.iter()):
        # Skip comments, and elements already removed with an ancestor.
        if (
            not isinstance(element.tag, str)
            or element.getroottree().getroot() is not root
        ):
            continue
        if any(
            etree.QName(attribute).namespace == OFFICE_RELS_NS and value in ids
            for attribute, value in element.attrib.items()
        ):
            # An OLE object frame in PowerPoint is meaningless without its object.
            target = element
            if (
                etree.QName(element).namespace == PRESENTATION_NS
                and _local(element) == "oleObj"
            ):
                for ancestor in element.iterancestors():
                    if _local(ancestor) == "graphicFrame":
                        target = ancestor
                        break
            if target.getparent() is not None:
                dropped.append(target)
                parent = target.getparent()
                _drop(target)
                _prune_empty(parent)
    return dropped


def _prune_empty(element: Optional[etree._Element]) -> None:
    while (
        element is not None
        and _local(element) in EMPTY_CONTAINERS
        and not any(isinstance(child.tag, str) for child in element)
    ):
        parent = element.getparent()
        if parent is None:
            return
        _drop(element)
        element = parent


def _clean_workbook(
    root: etree._Element,
    sheets_before: List[etree._Element],
    dropped: Iterable[etree._Element],
) -> None:
    """Keep a workbook consistent after sheets are removed from it."""
    dropped = set(dropped)
    removed_sheets = [e for e in sheets_before if e in dropped]
    if not removed_sheets:
        return
    names = [e.get("name", "") for e in removed_sheets]
    remaining = [e for e in sheets_before if e not in dropped]
    # A name scoped to a sheet refers to it by position, which shifts.
    positions: Dict[int, Optional[int]] = {}
    for old_index, element in enumerate(sheets_before):
        positions[old_index] = None if element in dropped else remaining.index(element)

    references = [
        re.compile(rf"(^|[^A-Za-z0-9_.])'?{re.escape(name)}'?!", re.IGNORECASE)
        for name in names
    ]
    defined_names = root.find(f"{{{SPREADSHEET_NS}}}definedNames")
    if defined_names is not None:
        for defined in list(defined_names):
            if _local(defined) != "definedName":
                continue
            text = defined.text or ""
            scope = defined.get("localSheetId")
            if (
                defined.get("name", "").lower() in AUTO_RUN_NAMES
                or any(pattern.search(text) for pattern in references)
                or (scope is not None and positions.get(int(scope)) is None)
            ):
                _drop(defined)
                continue
            if scope is not None:
                defined.set("localSheetId", str(positions[int(scope)]))
        _prune_empty(defined_names)

    for view in root.iter(f"{{{SPREADSHEET_NS}}}workbookView"):
        for attribute in ("activeTab", "firstSheet"):
            value = view.get(attribute)
            if value is not None and int(value) >= max(len(remaining), 1):
                view.set(attribute, "0")


def rebuild_ooxml(content: bytes) -> bytes:
    """Return the package without its active content, or raise SanitizationError."""
    package = read_package(content, all_parts=True)
    removed_parts, removed_relationships = _active_parts(package)

    removed_ids: Dict[str, Set[str]] = {}
    for rels_name, relationship_id in removed_relationships:
        removed_ids.setdefault(rels_name, set()).add(relationship_id)

    replaced: Dict[str, bytes] = {}

    for rels_name, ids in removed_ids.items():
        root = package.rels[rels_name]
        for element in list(root):
            if _local(element) == "Relationship" and element.get("Id") in ids:
                _drop(element)
        replaced[rels_name] = _serialize(root)

        source = _rels_source(rels_name)
        if not source:
            continue
        source_name = package.names.get(source.lower())
        if source_name is None or not source_name.lower().endswith(".xml"):
            continue
        source_root = _parse(package.data[source_name], source_name)
        sheets_before = list(source_root.iter(f"{{{SPREADSHEET_NS}}}sheet"))
        dropped = _drop_references(source_root, ids)
        if source_root.tag == f"{{{SPREADSHEET_NS}}}workbook":
            _clean_workbook(source_root, sheets_before, dropped)
        replaced[source_name] = _serialize(source_root)

    if package.content_types is not None:
        root = package.content_types
        for element in list(root):
            local = _local(element)
            content_type = element.get("ContentType", "")
            if local == "Override":
                part = element.get("PartName", "").lstrip("/").lower()
                if part in removed_parts:
                    _drop(element)
                    continue
            elif local == "Default" and _is_active_type(content_type):
                _drop(element)
                continue
            standard = MACRO_ENABLED_MAIN_TYPES.get(content_type.lower())
            if standard:
                element.set("ContentType", standard)
        replaced[package.content_types_name] = _serialize(root)

    dropped_rels = {
        package.names[_rels_name(package.names[part]).lower()]
        for part in removed_parts
        if _rels_name(package.names[part]).lower() in package.names
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for info in package.infos:
            lowered = info.filename.lower()
            if lowered in removed_parts or info.filename in dropped_rels:
                continue
            copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            copy.compress_type = info.compress_type
            copy.external_attr = info.external_attr
            archive.writestr(
                copy, replaced.get(info.filename, package.data[info.filename])
            )
    rebuilt = output.getvalue()
    _check_rebuilt(rebuilt, removed_parts)
    return rebuilt


def _check_rebuilt(rebuilt: bytes, removed_parts: Set[str]) -> None:
    """Refuse a rebuild that still names a part it removed."""
    package = read_package(rebuilt)
    if any(lowered in removed_parts for lowered in package.names):
        raise SanitizationError("rebuild_kept_active_part")
    if any(part in removed_parts for part in package.overrides):
        raise SanitizationError("rebuild_kept_content_type")
    for relationship in package.relationships:
        if relationship.target is not None and relationship.target in removed_parts:
            raise SanitizationError("rebuild_kept_relationship")
    remaining_parts, remaining_relationships = _active_parts(package)
    if remaining_parts or remaining_relationships:
        raise SanitizationError("rebuild_kept_active_part")


# PDF actions that run code, start programs or send or load data.
ACTIVE_PDF_ACTIONS = frozenset(
    {
        "/JavaScript",
        "/Launch",
        "/ImportData",
        "/SubmitForm",
        "/Rendition",
        "/RichMediaExecute",
    }
)


def rebuild_pdf(content: bytes) -> bytes:
    """Return the PDF without scripts and active actions, or raise SanitizationError.

    Destinations, links to web addresses and other navigation are kept. The
    copy is written without object streams so that its dictionaries can be
    checked again.
    """
    import pikepdf

    try:
        pdf = pikepdf.open(io.BytesIO(content))
    except Exception as exc:
        raise SanitizationError("pdf_unreadable") from exc

    with pdf:
        try:
            root = pdf.Root
            # An OpenAction dictionary is an action; an array is a destination.
            if "/OpenAction" in root and isinstance(
                root.OpenAction, pikepdf.Dictionary
            ):
                del root["/OpenAction"]
            names = root.get("/Names")
            if isinstance(names, pikepdf.Dictionary) and "/JavaScript" in names:
                del names["/JavaScript"]
            acroform = root.get("/AcroForm")
            if isinstance(acroform, pikepdf.Dictionary) and "/XFA" in acroform:
                # XFA forms carry their own scripts.
                del acroform["/XFA"]
            _clean_pdf_object(root)
            for obj in pdf.objects:
                _clean_pdf_object(obj)

            output = io.BytesIO()
            pdf.save(output, object_stream_mode=pikepdf.ObjectStreamMode.disable)
        except SanitizationError:
            raise
        except Exception as exc:
            raise SanitizationError("pdf_rebuild_failed") from exc
    return output.getvalue()


def _clean_pdf_object(obj) -> None:
    """Remove triggers and active actions from an object and its direct children."""
    import pikepdf

    if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
        if "/AA" in obj:
            del obj["/AA"]
        action = obj.get("/A")
        if isinstance(action, pikepdf.Dictionary) and str(action.get("/S", "")) in (
            ACTIVE_PDF_ACTIONS
        ):
            del obj["/A"]
        if str(obj.get("/S", "")) in ACTIVE_PDF_ACTIONS:
            # Left unreachable but inert: no type, script, file or chain.
            for key in ("/S", "/JS", "/F", "/Win", "/Mac", "/Unix", "/Next"):
                if key in obj:
                    del obj[key]
        elif "/S" in obj and "/Next" in obj:
            # A chained action can lead to an active one.
            del obj["/Next"]
        if "/JS" in obj:
            del obj["/JS"]
        for key in list(obj.keys()):
            value = obj[key]
            # Indirect objects are cleaned on their own; numbers come back as int.
            if not getattr(value, "is_indirect", True):
                _clean_pdf_object(value)
    elif isinstance(obj, pikepdf.Array):
        for value in obj:
            if not getattr(value, "is_indirect", True):
                _clean_pdf_object(value)
