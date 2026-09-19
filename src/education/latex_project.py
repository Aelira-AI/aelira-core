"""Bounded, non-executing inventory of an authored LaTeX source project.

This is a literal dependency profile, not a TeX sandbox or a package installer.
Unresolved projects retain all original bytes, but have no flattened analysis view.
"""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType
from typing import Mapping
import unicodedata
import zipfile
import zlib

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 256
MAX_RATIO = 200
MAX_DEPTH = 32
MAX_ISSUES = 64
MAX_DEPENDENCIES = 4096
MAX_TOKENS = 16384
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
EXTENSIONS = frozenset(
    {
        ".tex",
        ".sty",
        ".cls",
        ".bib",
        ".bst",
        ".png",
        ".jpg",
        ".jpeg",
        ".pdf",
        ".eps",
        ".svg",
        ".txt",
    }
)
# Explicit recognition only. Runtime capability checks still determine installation.
SYSTEM_PACKAGES = frozenset(
    "amsmath amssymb amsfonts amsthm babel graphicx graphics hyperref fontenc inputenc geometry xcolor color booktabs array longtable tabularx multirow caption subcaption float enumitem url natbib biblatex csquotes microtype lmodern fontspec unicode-math polyglossia tagpdf accessibility axessibility accsupp siunitx listings fancyvrb verbatim textcomp mathtools titlesec fancyhdr setspace parskip etoolbox iftex expl3 xparse pdfmanagement-testphase".split()
)
SYSTEM_CLASSES = frozenset(
    "article report book letter slides minimal beamer memoir scrartcl scrreprt scrbook".split()
)
SYSTEM_STYLES = frozenset(
    "plain unsrt alpha abbrv plainnat abbrvnat unsrtnat apalike".split()
)
COMMANDS = {
    "input": (".tex",),
    "include": (".tex",),
    "includegraphics": (".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"),
    "bibliography": (".bib",),
    "addbibresource": (".bib",),
    "usepackage": (".sty",),
    "RequirePackage": (".sty",),
    "documentclass": (".cls",),
    "LoadClass": (".cls",),
    "bibliographystyle": (".bst",),
}
COMMAND_RE = re.compile(r"\\([A-Za-z@]+|.)", re.DOTALL)
DYNAMIC_RE = re.compile(
    r"\\(?:csname|catcode|inputencoding|endinput|includeonly|graphicspath|DeclareGraphicsExtensions|import|subimport|subfile|InputIfFileExists|IfFileExists|openin|read|write|openout|directlua|special|pdfximage|pdfextension|let|futurelet|def|edef|gdef|xdef|if[A-Za-z@]*|else|fi)\b"
)


class ProjectError(ValueError):
    def __init__(self, code: str, path: str = ""):
        self.code = code
        self.path = path
        super().__init__(code)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _path(name: str) -> str:
    if (
        not name
        or len(name) > 240
        or "\\" in name
        or any(c in name for c in ':<>"|?*')
        or name.startswith("/")
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
        or unicodedata.normalize("NFC", name) != name
    ):
        raise ProjectError("unsafe_path")
    parts = name.split("/")
    if any(p in {"", ".", ".."} or p != p.strip() or p.endswith(".") for p in parts):
        raise ProjectError("unsafe_path")
    if any(
        p.split(".", 1)[0].casefold()
        in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10)),
        }
        for p in parts
    ):
        raise ProjectError("unsafe_path")
    if len(parts) > 16 or any(len(p) > 100 for p in parts):
        raise ProjectError("unsafe_path")
    return name


@dataclass(frozen=True)
class LatexProject:
    entry: str
    files: Mapping[str, bytes]
    dependencies: tuple[dict, ...]
    issues: tuple[dict, ...]
    flattened_source: str | None
    archive_digest: str

    @property
    def source_digest(self) -> str:
        return _digest(_json({p: _digest(b) for p, b in sorted(self.files.items())}))

    @property
    def manifest(self) -> dict:
        result = {
            "schema_version": 1,
            "profile": "literal-project-v1",
            "entry": self.entry,
            "archive_sha256": self.archive_digest,
            "source_sha256": self.source_digest,
            "files": [
                {"path": p, "size": len(b), "sha256": _digest(b)}
                for p, b in sorted(self.files.items())
            ],
            "dependencies": list(self.dependencies),
            "issues": list(self.issues),
            "transformations": [],
        }
        if self.flattened_source is not None:
            result["transformations"].append(
                {
                    "kind": "analysis_only_literal_dependency_expansion",
                    "local_preamble_profile": "literal-wrappers-and-macros",
                    "entry": self.entry,
                    "source_sha256": self.source_digest,
                    "result_sha256": _digest(self.flattened_source.encode("utf-8")),
                }
            )
        if self.flattened_source is not None:
            result["transformations"].extend(
                {
                    "kind": "analysis_only_graphics_path_resolution",
                    "source": edge["source"],
                    "requested": edge["requested"],
                    "target": edge["target"],
                    "sha256": edge["sha256"],
                }
                for edge in self.dependencies
                if edge["command"] == "includegraphics"
                and edge["resolution"] == "local"
            )
        result["manifest_sha256"] = _digest(_json(result))
        return result


def inspect_archive(data: bytes, entry: str) -> LatexProject:
    """Inventory a ZIP without extracting or executing any archive content."""
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE_BYTES:
        raise ProjectError("archive_size_limit")
    entry = _path(entry)
    if PurePosixPath(entry).suffix.lower() != ".tex":
        raise ProjectError("invalid_entry")
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    spellings: dict[str, str] = {}
    total = 0
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_FILES:
                raise ProjectError("file_count_limit")
            for member in members:
                original = member.orig_filename
                is_dir = member.is_dir()
                name = _path(original[:-1] if is_dir else original)
                if name.casefold() in seen:
                    raise ProjectError("duplicate_path", name)
                seen.add(name.casefold())
                for prefix in (PurePosixPath(name), *PurePosixPath(name).parents):
                    spelling = str(prefix)
                    if (
                        spelling.casefold() in spellings
                        and spellings[spelling.casefold()] != spelling
                    ):
                        raise ProjectError("case_collision", name)
                    spellings[spelling.casefold()] = spelling
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFDIR if is_dir else stat.S_IFREG}:
                    raise ProjectError("unsafe_file_type", name)
                if member.flag_bits & 1 or member.compress_type not in {
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                }:
                    raise ProjectError("unsupported_archive_member", name)
                if is_dir:
                    if member.file_size:
                        raise ProjectError("unsafe_file_type", name)
                    continue
                if PurePosixPath(name).suffix.lower() not in EXTENSIONS:
                    raise ProjectError("unsupported_file_type", name)
                total += member.file_size
                if member.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ProjectError("expanded_size_limit", name)
                if member.file_size > max(member.compress_size, 1) * MAX_RATIO:
                    raise ProjectError("compression_ratio_limit", name)
                with archive.open(member) as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) != member.file_size or len(content) > MAX_FILE_BYTES:
                    raise ProjectError("expanded_size_limit", name)
                files[name] = content
    except ProjectError:
        raise
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        OSError,
        EOFError,
        ValueError,
        zlib.error,
    ) as exc:
        raise ProjectError("invalid_archive") from exc
    folded = {p.casefold(): p for p in files}
    for path in files:
        if any(
            str(parent).casefold() in folded
            for parent in PurePosixPath(path).parents
            if str(parent) != "."
        ):
            raise ProjectError("file_directory_collision", path)
    if entry not in files:
        raise ProjectError("entry_missing", entry)
    return _inspect_files(files, entry, _digest(data))


def _uncomment(text: str) -> str:
    # Keep character offsets stable so include expansion leaves authored bytes alone.
    chars = list(text)
    for match in re.finditer(r"%[^\r\n]*", text):
        before = match.start() - 1
        escapes = 0
        while before >= 0 and text[before] == "\\":
            escapes += 1
            before -= 1
        if escapes % 2 == 0:
            chars[match.start() : match.end()] = " " * (match.end() - match.start())
    return "".join(chars)


def _argument(text: str, offset: int, opening: str, closing: str):
    while offset < len(text) and text[offset].isspace():
        offset += 1
    if offset >= len(text) or text[offset] != opening:
        return None
    start = offset + 1
    depth = 1
    for index in range(start, len(text)):
        if index and text[index - 1] == "\\":
            continue
        depth += (text[index] == opening) - (text[index] == closing)
        if not depth:
            return text[start:index], index + 1
    return None


def _preamble_profile(text: str):
    """Accept declaration wrappers, literal package loads and simple macro bodies."""
    offset = 0
    wrappers = []
    while offset < len(text):
        if text[offset].isspace():
            offset += 1
            continue
        match = COMMAND_RE.match(text, offset)
        if not match:
            return None
        command = match[1]
        start = offset
        offset = match.end()
        if offset < len(text) and text[offset] == "*":
            offset += 1
        if command in {"ProvidesPackage", "ProvidesClass", "NeedsTeXFormat"}:
            argument = _argument(text, offset, "{", "}")
            if not argument:
                return None
            _, offset = argument
            optional = _argument(text, offset, "[", "]")
            if optional:
                _, offset = optional
            wrappers.append((start, offset))
        elif command in {
            "newcommand",
            "renewcommand",
            "providecommand",
            "DeclareMathOperator",
        }:
            name = _argument(text, offset, "{", "}")
            if not name or not re.fullmatch(r"\\[A-Za-z]+", name[0]):
                return None
            _, offset = name
            for _ in range(2):
                optional = _argument(text, offset, "[", "]")
                if optional:
                    _, offset = optional
            body = _argument(text, offset, "{", "}")
            if not body:
                return None
            _, offset = body
        elif command in {"RequirePackage", "usepackage", "LoadClass", "input"}:
            optional = _argument(text, offset, "[", "]")
            if optional:
                _, offset = optional
            argument = _argument(text, offset, "{", "}")
            if not argument:
                return None
            _, offset = argument
        else:
            return None
    return wrappers


def _inspect_files(
    files: Mapping[str, bytes], entry: str, archive_digest: str
) -> LatexProject:
    issues: list[dict] = []
    dependencies: list[dict] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    expansions: dict[str, list[tuple[int, int, str | None, str]]] = {}
    expanded_preamble: set[str] = set()
    texts: dict[str, str] = {}
    root = PurePosixPath(entry).parent
    token_count = 0

    def issue(code, path):
        value = {
            "code": code,
            "path": path,
            "action": "Review the source project and provide literal, unambiguous local dependencies.",
        }
        if value not in issues and len(issues) < MAX_ISSUES:
            issues.append(value)

    def resolve(path, command, value):
        if len(dependencies) >= MAX_DEPENDENCIES:
            issue("dependency_count_limit", path)
            return None
        if any(c in value for c in "\\{}#$^~%") or not value.strip():
            issue("dependency_dynamic", path)
            return None
        try:
            value = _path(value.strip())
        except ProjectError:
            issue("dependency_unsafe_path", path)
            return None
        suffixes = COMMANDS[command]
        supplied = PurePosixPath(value).suffix
        candidates = set()
        for base in {root, PurePosixPath(path).parent}:
            names = [value] if supplied else [value + suffix for suffix in suffixes]
            for name in names:
                candidate = str(base / name)
                if candidate in files:
                    candidates.add(candidate)
        if len(candidates) > 1:
            issue("dependency_ambiguous", path)
            return None
        if candidates:
            target = candidates.pop()
            if PurePosixPath(target).suffix.lower() not in suffixes:
                issue("dependency_unsupported_type", path)
                return None
            dependencies.append(
                {
                    "source": path,
                    "command": command,
                    "requested": value,
                    "target": target,
                    "sha256": _digest(files[target]),
                    "resolution": "local",
                }
            )
            return target
        system = (
            SYSTEM_PACKAGES
            if suffixes == (".sty",)
            else (
                SYSTEM_CLASSES
                if suffixes == (".cls",)
                else SYSTEM_STYLES if suffixes == (".bst",) else frozenset()
            )
        )
        if value in system:
            dependencies.append(
                {
                    "source": path,
                    "command": command,
                    "requested": value,
                    "target": None,
                    "resolution": "system_profile",
                }
            )
            return None
        issue("dependency_missing", path)
        return None

    def visit(path, depth=0):
        nonlocal token_count
        if token_count >= MAX_TOKENS:
            issue("dependency_token_limit", path)
            return
        if path in visiting:
            issue("dependency_cycle", path)
            return
        if depth > MAX_DEPTH:
            issue("dependency_depth_limit", path)
            return
        if path in visited:
            return
        visiting.add(path)
        try:
            source = files[path].decode("utf-8-sig")
        except UnicodeDecodeError:
            issue("source_encoding_unsupported", path)
            visiting.remove(path)
            visited.add(path)
            return
        if "\x00" in source:
            issue("source_encoding_unsupported", path)
        texts[path] = source
        text = _uncomment(source)
        if (
            "^^" in text
            or DYNAMIC_RE.search(text)
            or re.search(
                r"\\(?:verb\*?|begin\s*\{(?:verbatim\*?|Verbatim|lstlisting|minted)\})",
                text,
            )
        ):
            issue("dependency_dynamic", path)
        expansions[path] = []
        if PurePosixPath(path).suffix.lower() in {".sty", ".cls"}:
            wrappers = _preamble_profile(text)
            if wrappers is None:
                issue("local_preamble_unsupported", path)
            else:
                expansions[path].extend(
                    (start, end, None, "") for start, end in wrappers
                )
        cursor = 0
        braces = 0
        for match in COMMAND_RE.finditer(text):
            token_count += 1
            if token_count > MAX_TOKENS:
                issue("dependency_token_limit", path)
                break
            if match.start() < cursor:
                continue
            braces += text[cursor : match.start()].count("{") - text[
                cursor : match.start()
            ].count("}")
            command = match[1]
            cursor = match.end()
            if command not in COMMANDS:
                continue
            if braces:
                issue("dependency_dynamic", path)
            offset = match.end()
            if offset < len(text) and text[offset] == "*":
                offset += 1
            optional = _argument(text, offset, "[", "]")
            if optional:
                _, offset = optional
            argument = _argument(text, offset, "{", "}")
            if argument is None:
                issue("dependency_dynamic", path)
                break
            value, end = argument
            cursor = end
            values = (
                value.split(",")
                if command in {"bibliography", "usepackage", "RequirePackage"}
                else [value]
            )
            local_targets = []
            for item in values:
                target = resolve(path, command, item)
                if target and PurePosixPath(target).suffix.lower() in {
                    ".tex",
                    ".sty",
                    ".cls",
                }:
                    visit(target, depth + 1)
                if target and command in {"input", "include"}:
                    expansions[path].append((match.start(), end, target, ""))
                elif target and command == "includegraphics":
                    expansions[path].append(
                        (end - len(value) - 2, end, None, "{" + target + "}")
                    )
                elif target and command in {
                    "usepackage",
                    "RequirePackage",
                    "documentclass",
                    "LoadClass",
                }:
                    local_targets.append(target)
            if local_targets:
                if (
                    optional
                    or len(values) != 1
                    or local_targets[0] in expanded_preamble
                ):
                    issue("local_preamble_unsupported", path)
                else:
                    expanded_preamble.add(local_targets[0])
                    expansions[path].append((match.start(), end, local_targets[0], ""))
            elif (
                command == "LoadClass" and PurePosixPath(path).suffix.lower() == ".cls"
            ):
                expansions[path].append(
                    (match.start(), match.end(), None, r"\documentclass")
                )
        visiting.remove(path)
        visited.add(path)

    visit(entry)

    flatten_cache = {}
    flatten_cache_bytes = 0

    def flatten(path, depth=0):
        nonlocal flatten_cache_bytes
        if path in flatten_cache:
            return flatten_cache[path]
        if depth > MAX_DEPTH:
            raise ProjectError("flatten_size_limit")
        text = texts[path]
        output = []
        offset = 0
        size = 0
        for start, end, target, literal in sorted(expansions[path]):
            replacement = (
                "\n" + flatten(target, depth + 1) + "\n" if target else literal
            )
            segment = text[offset:start] + replacement
            size += len(segment.encode("utf-8"))
            if size > MAX_TOTAL_BYTES:
                raise ProjectError("flatten_size_limit")
            output.append(segment)
            offset = end
        output.append(text[offset:])
        result = "".join(output)
        if len(result.encode("utf-8")) > MAX_TOTAL_BYTES:
            raise ProjectError("flatten_size_limit")
        flatten_cache_bytes += len(result.encode("utf-8"))
        if flatten_cache_bytes > MAX_TOTAL_BYTES:
            raise ProjectError("flatten_size_limit")
        flatten_cache[path] = result
        return result

    flattened = None
    if not issues:
        try:
            flattened = flatten(entry)
        except ProjectError:
            issue("flatten_size_limit", entry)
    return LatexProject(
        entry,
        MappingProxyType(dict(files)),
        tuple(dependencies),
        tuple(issues),
        flattened,
        archive_digest,
    )


def write_project(project: LatexProject, destination: Path) -> dict:
    """Create a new owned directory; never overwrite any existing path."""
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    content = destination / "files"
    content.mkdir(mode=0o700)
    for name, data in project.files.items():
        target = content / _path(name)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o400)
    manifest = project.manifest
    (destination / "manifest.json").write_bytes(_json(manifest))
    (destination / "manifest.json").chmod(0o400)
    return manifest


def load_project(destination: Path) -> LatexProject:
    """Verify an owned stored project; callers retain an external trusted digest."""
    destination = Path(destination)
    manifest_path = destination / "manifest.json"
    try:
        if (
            destination.is_symlink()
            or manifest_path.is_symlink()
            or manifest_path.stat().st_size > MAX_MANIFEST_BYTES
        ):
            raise ProjectError("stored_project_invalid")
        manifest = json.loads(manifest_path.read_bytes())
        expected = manifest.pop("manifest_sha256")
        if _digest(_json(manifest)) != expected or manifest["schema_version"] != 1:
            raise ProjectError("stored_project_invalid")
        rows = manifest["files"]
        if len(rows) > MAX_FILES:
            raise ProjectError("stored_project_invalid")
        files = {}
        total = 0
        for row in rows:
            name = _path(row["path"])
            target = destination / "files" / name
            if (
                any(p.is_symlink() for p in (target, *target.parents))
                or not target.is_file()
            ):
                raise ProjectError("stored_project_invalid")
            size = target.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ProjectError("stored_project_invalid")
            data = target.read_bytes()
            if len(data) != row["size"] or _digest(data) != row["sha256"]:
                raise ProjectError("stored_project_invalid")
            files[name] = data
        actual = {
            str(p.relative_to(destination / "files"))
            for p in (destination / "files").rglob("*")
            if p.is_file() or p.is_symlink()
        }
        if actual != set(files):
            raise ProjectError("stored_project_invalid")
        project = _inspect_files(
            files, _path(manifest["entry"]), manifest["archive_sha256"]
        )
        if project.manifest != {**manifest, "manifest_sha256": expected}:
            raise ProjectError("stored_project_invalid")
        return project
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ProjectError("stored_project_invalid") from exc


def prepare_project_copy(
    source: Path, destination: Path, transformations: Mapping[str, bytes] | None = None
) -> dict:
    """Materialize an isolated working copy with an explicit byte-change ledger."""
    original = load_project(source)
    changes = transformations or {}
    files = dict(original.files)
    records = []
    for name, value in changes.items():
        if (
            name not in files
            or PurePosixPath(name).suffix.lower() != ".tex"
            or not isinstance(value, bytes)
            or len(value) > MAX_FILE_BYTES
        ):
            raise ProjectError("invalid_transformation")
        records.append(
            {
                "kind": "source_edit",
                "path": name,
                "before_sha256": _digest(files[name]),
                "after_sha256": _digest(value),
            }
        )
        files[name] = value
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise ProjectError("expanded_size_limit")
    updated = _inspect_files(files, original.entry, original.archive_digest)
    manifest = write_project(updated, destination)
    ledger = {
        "original_source_sha256": original.source_digest,
        "result_source_sha256": updated.source_digest,
        "transformations": records,
    }
    (Path(destination) / "transformations.json").write_bytes(_json(ledger))
    return {**manifest, "working_copy": ledger}
