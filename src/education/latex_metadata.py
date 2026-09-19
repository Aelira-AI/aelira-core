"""Literal authored metadata only; unsupported TeX requires review, never guessing."""

from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path
import re

LANGUAGES = {
    "english": "en",
    "american": "en-US",
    "british": "en-GB",
    "german": "de",
    "ngerman": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "dutch": "nl",
    "portuguese": "pt",
}


def language_tag(value: str) -> str | None:
    value = value.strip().strip("{}").strip()
    return LANGUAGES.get(value) or (
        value if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value) else None
    )


def uncomment(source: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", source)


def groups(source: str, command: str):
    """Yield balanced literal arguments without executing any TeX."""
    for match in re.finditer(r"\\" + command + r"\s*\{", source):
        start = match.end()
        depth = 1
        for index in range(start, len(source)):
            if index and source[index - 1] == "\\":
                continue
            depth += (source[index] == "{") - (source[index] == "}")
            if depth == 0:
                yield source[start:index]
                break
        else:
            yield "\\unparsed"


def literal(value: str) -> str | None:
    # Metadata containing commands or grouping needs TeX-aware review.
    if any(c in value for c in "\\{}$#^"):
        return None
    return " ".join(value.split()) or None


@dataclass
class AuthoredMetadata:
    language: str | None = None
    title: str | None = None
    author: str | None = None
    spans: list[tuple[str, str]] = field(default_factory=list)
    issues: set[str] = field(default_factory=set)


def extract_metadata(source: str | None) -> AuthoredMetadata:
    text = uncomment(source or "")
    preamble = text.split(r"\begin{document}", 1)[0]
    metadata = AuthoredMetadata()
    if re.search(
        r"\\(?:title|author)\s*\[|\\(?:babelprovide|babeltags|setmainlanguage|setotherlanguage|setotherlanguages|textenglish|textgerman)\b|\\begin\{otherlanguage\*\}",
        text,
    ):
        metadata.issues.add("metadata_unsupported")
    if re.search(
        r"\\(?:title|author|hypersetup|DocumentMetadata|setdefaultlanguage)\b",
        text[len(preamble) :],
    ):
        metadata.issues.add("metadata_unsupported")
    if re.search(r"\\(?:newcommand|renewcommand|def|if[a-zA-Z]*)\b", preamble):
        # This profile cannot establish whether a declaration executes inside
        # a macro definition or conditional branch.
        metadata.issues.add("metadata_unsupported")
    declarations: dict[str, list[str | None]] = {
        k: [] for k in ("language", "title", "author")
    }
    for name in ("title", "author"):
        declarations[name].extend(literal(value) for value in groups(preamble, name))
    for command in ("DocumentMetadata", "hypersetup"):
        for options in groups(preamble, command):
            # Commas inside a braced literal value are not separators.
            for match in re.finditer(
                r"(?:^|,)\s*(lang|pdflang|pdftitle|pdfauthor)\s*=\s*(\{[^{}]*\}|[^,{}]*)",
                options,
            ):
                key, value = match.groups()
                value = value.strip().strip("{}")
                name = {
                    "lang": "language",
                    "pdflang": "language",
                    "pdftitle": "title",
                    "pdfauthor": "author",
                }[key]
                declarations[name].append(
                    language_tag(value) if name == "language" else literal(value)
                )
            if re.search(
                r"(?:lang|pdflang|pdftitle|pdfauthor)\s*=\s*\{[^}]*\{", options
            ):
                metadata.issues.add("metadata_unsupported")
    for options in re.findall(r"\\documentclass\s*\[([^\]]*)\]", preamble):
        declarations["language"].extend(
            LANGUAGES[option.strip()]
            for option in options.split(",")
            if option.strip() in LANGUAGES
        )
    for match in re.finditer(
        r"\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\])?\s*\{([^}]+)\}", preamble
    ):
        if "babel" not in [s.strip() for s in match[2].split(",")]:
            continue
        options = [s.strip() for s in (match[1] or "").split(",")]
        main = [s.split("=", 1)[1] for s in options if s.startswith("main=")]
        known = [s for s in options if language_tag(s)]
        if any(
            s and not language_tag(s) and not s.startswith("main=") for s in options
        ):
            metadata.issues.add("metadata_unsupported")
        if main:
            declarations["language"].extend(language_tag(s) for s in main)
        elif known:
            declarations["language"].append(language_tag(known[-1]))
        else:
            declarations["language"].append(None)
    declarations["language"].extend(
        language_tag(v) for v in groups(preamble, "setdefaultlanguage")
    )
    if re.search(r"\\setdefaultlanguage\s*\[", preamble):
        metadata.issues.add("metadata_unsupported")
    for name, values in declarations.items():
        if values and (None in values or len(set(values)) != 1):
            metadata.issues.add("metadata_ambiguous")
        elif values:
            setattr(metadata, name, values[0])
    # Supported span syntax is literal foreignlanguage/otherlanguage. Refuse
    # unscoped switches rather than flattening them into a document default.
    if re.search(r"\\selectlanguage\b", text):
        metadata.issues.add("metadata_unsupported")
    span_matches = list(
        re.finditer(
            r"\\foreignlanguage\{([^{}]+)\}\{([^{}]*)\}|\\begin\{otherlanguage\}\{([^{}]+)\}([\s\S]*?)\\end\{otherlanguage\}",
            text,
        )
    )
    if len(span_matches) != len(
        re.findall(r"\\foreignlanguage\b|\\begin\{otherlanguage\}", text)
    ):
        metadata.issues.add("metadata_unsupported")
    for match in span_matches:
        lang, content = (match[1], match[2]) if match[1] else (match[3], match[4])
        tag, content = language_tag(lang), literal(content)
        if not tag or not content:
            metadata.issues.add("metadata_unsupported")
        else:
            metadata.spans.append((tag, content))
    return metadata


def save_html_metadata(path: Path, metadata: AuthoredMetadata) -> bool:
    from bs4 import BeautifulSoup

    if metadata.issues:
        return False
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if soup.html is None:
        return False
    if metadata.language:
        soup.html["lang"] = metadata.language
    else:
        soup.html.attrs.pop("lang", None)
    soup.html.attrs.pop("xml:lang", None)
    # Span identity is bound to both language and exact normalized text. Never
    # invent span boundaries after a converter has lost them.
    for (language, text), count in Counter(metadata.spans).items():
        if (
            sum(
                language_tag(tag.get("lang", tag.get("xml:lang", ""))) == language
                and " ".join(tag.get_text(" ").split()) == text
                for tag in soup.find_all(
                    lambda tag: tag.has_attr("lang") or tag.has_attr("xml:lang")
                )
            )
            < count
        ):
            return False
    if soup.head is None:
        head = soup.new_tag("head")
        soup.html.insert(0, head)
    for tag in soup.find_all("title") + soup.find_all(
        "meta",
        attrs={
            "name": re.compile(r"^(author|dc\.creator|dc\.title|dc\.language)$", re.I)
        },
    ):
        tag.decompose()
    if metadata.title:
        tag = soup.new_tag("title")
        tag.string = metadata.title
        soup.head.append(tag)
    if metadata.author:
        soup.head.append(
            soup.new_tag("meta", attrs={"name": "author", "content": metadata.author})
        )
    path.write_text(str(soup), encoding="utf-8")
    saved = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    author = saved.find("meta", attrs={"name": "author"})
    return (
        saved.html.get("lang") == metadata.language
        and (saved.title.get_text() if saved.title else None) == metadata.title
        and (author.get("content") if author else None) == metadata.author
    )


def save_pdf_metadata(path: Path, metadata: AuthoredMetadata) -> bool:
    import pikepdf

    # Span language needs structure-tree verification, not a root Lang patch.
    if metadata.issues or metadata.spans:
        return False
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for key, value in (("/Lang", metadata.language),):
            if value:
                pdf.Root[key] = value
            elif key in pdf.Root:
                del pdf.Root[key]
        for key, value in (("/Title", metadata.title), ("/Author", metadata.author)):
            if value:
                pdf.docinfo[key] = value
            elif key in pdf.docinfo:
                del pdf.docinfo[key]
        with pdf.open_metadata(
            set_pikepdf_as_editor=False, update_docinfo=False
        ) as xmp:
            for key, value in (
                ("dc:title", metadata.title),
                ("dc:creator", [metadata.author] if metadata.author else None),
                ("dc:language", [metadata.language] if metadata.language else None),
            ):
                if value:
                    xmp[key] = value
                elif key in xmp:
                    del xmp[key]
        pdf.save(path)
    with pikepdf.open(path) as saved:
        info_matches = all(
            (str(container[key]) if key in container else None) == value
            for container, key, value in (
                (saved.Root, "/Lang", metadata.language),
                (saved.docinfo, "/Title", metadata.title),
                (saved.docinfo, "/Author", metadata.author),
            )
        )
        with saved.open_metadata(
            set_pikepdf_as_editor=False, update_docinfo=False
        ) as xmp:
            languages = xmp.get("dc:language")
            language_matches = (
                languages is None
                if metadata.language is None
                else (
                    isinstance(languages, (list, set))
                    and set(languages) == {metadata.language}
                )
            )
            return (
                info_matches
                and language_matches
                and all(
                    xmp.get(key) == value
                    for key, value in (
                        ("dc:title", metadata.title),
                        ("dc:creator", [metadata.author] if metadata.author else None),
                    )
                )
            )
