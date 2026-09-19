"""Archive preservation and bounded literal dependency resolution."""

from hashlib import sha256
from io import BytesIO
import json
import stat
import zipfile

import pytest

from src.education import latex_project as lp


def archive(files, compression=zipfile.ZIP_STORED):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as result:
        items = files.items() if isinstance(files, dict) else files
        for name, content in items:
            result.writestr(name, content)
    return buffer.getvalue()


def project(source, extras=None):
    return lp.inspect_archive(
        archive({"main.tex": source, **(extras or {})}), "main.tex"
    )


def codes(value):
    return {issue["code"] for issue in value.issues}


def test_nested_project_preserves_every_original_byte_and_builds_graph(tmp_path):
    files = {
        "paper/main.tex": b"\\documentclass{article}\r\n\\title{Authored title}\r\n\\input{sections/first}\r\n",
        "paper/sections/first.tex": b"First \\input{second section} \\includegraphics{plot file.png}",
        "paper/sections/second section.tex": b"Second \\bibliography{refs}",
        "paper/sections/plot file.png": b"original binary\x00\xff",
        "paper/refs.bib": b"@book{one,title={Original}}",
        "unused.txt": b"Preserve this too",
    }
    data = archive(files)
    result = lp.inspect_archive(data, "paper/main.tex")
    assert not result.issues
    assert dict(result.files) == files
    assert result.archive_digest == sha256(data).hexdigest()
    assert "\\title{Authored title}\r\n" in result.flattened_source
    assert "First" in result.flattened_source and "Second" in result.flattened_source
    assert len(result.dependencies) == 5
    assert r"\includegraphics{paper/sections/plot file.png}" in result.flattened_source
    assert any(
        row["kind"] == "analysis_only_graphics_path_resolution"
        for row in result.manifest["transformations"]
    )
    destination = tmp_path / "original"
    assert lp.write_project(result, destination) == result.manifest
    assert lp.load_project(destination).manifest == result.manifest
    assert all(
        (destination / "files" / name).read_bytes() == content
        for name, content in files.items()
    )
    with pytest.raises(TypeError):
        result.files["main.tex"] = b"changed"
    with pytest.raises(FileExistsError):
        lp.write_project(result, destination)


@pytest.mark.parametrize(
    "name",
    [
        "../evil.tex",
        "/evil.tex",
        "a/../evil.tex",
        "a\\evil.tex",
        "C:evil.tex",
        "a//evil.tex",
        "./evil.tex",
        "a/ evil.tex",
        "a./evil.tex",
        "a\x01.tex",
        "e\u0301.tex",
    ],
)
def test_archive_paths_are_rejected_before_extraction(name):
    with pytest.raises(lp.ProjectError, match="unsafe_path"):
        lp.inspect_archive(archive({"main.tex": "Hello", name: "bad"}), "main.tex")


@pytest.mark.parametrize(
    "names",
    [
        ["A.tex", "a.tex"],
        ["Pictures/a.png", "pictures/b.png"],
        ["x.tex", "x.tex/child.tex"],
    ],
)
def test_case_and_file_directory_collisions_rejected(names):
    with pytest.raises(lp.ProjectError):
        lp.inspect_archive(
            archive({"main.tex": "Hi", **dict.fromkeys(names, "data")}), "main.tex"
        )


def test_duplicate_members_rejected():
    with pytest.warns(UserWarning):
        data = archive([("main.tex", "first"), ("main.tex", "second")])
    with pytest.raises(lp.ProjectError, match="duplicate_path"):
        lp.inspect_archive(data, "main.tex")


@pytest.mark.parametrize(
    "mode", [stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o600, stat.S_IFIFO | 0o600]
)
def test_symlinks_and_special_files_rejected(mode):
    info = zipfile.ZipInfo("bad.tex")
    info.create_system = 3
    info.external_attr = mode << 16
    with pytest.raises(lp.ProjectError, match="unsafe_file_type"):
        lp.inspect_archive(archive([("main.tex", "Hi"), (info, "target")]), "main.tex")


@pytest.mark.parametrize(
    "name", ["run.sh", ".env", "script.py", "script.js", "document.html"]
)
def test_unsupported_archive_file_types_rejected(name):
    with pytest.raises(lp.ProjectError, match="unsupported_file_type"):
        lp.inspect_archive(archive({"main.tex": "Hi", name: "payload"}), "main.tex")


def test_expansion_archive_member_count_and_compression_bounds(monkeypatch):
    data = archive({"main.tex": "x" * 20})
    monkeypatch.setattr(lp, "MAX_FILE_BYTES", 10)
    with pytest.raises(lp.ProjectError, match="expanded_size_limit"):
        lp.inspect_archive(data, "main.tex")
    monkeypatch.setattr(lp, "MAX_FILE_BYTES", 100000)
    monkeypatch.setattr(lp, "MAX_TOTAL_BYTES", 10)
    with pytest.raises(lp.ProjectError, match="expanded_size_limit"):
        lp.inspect_archive(data, "main.tex")
    monkeypatch.setattr(lp, "MAX_TOTAL_BYTES", 100000)
    monkeypatch.setattr(lp, "MAX_FILES", 1)
    with pytest.raises(lp.ProjectError, match="file_count_limit"):
        lp.inspect_archive(archive({"main.tex": "a", "second.tex": "b"}), "main.tex")
    monkeypatch.setattr(lp, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(lp.ProjectError, match="archive_size_limit"):
        lp.inspect_archive(data, "main.tex")


def test_compression_bomb_rejected():
    data = archive({"main.tex": "x" * 100000}, zipfile.ZIP_DEFLATED)
    with pytest.raises(lp.ProjectError, match="compression_ratio_limit"):
        lp.inspect_archive(data, "main.tex")


@pytest.mark.parametrize("entry", ["absent.tex", "main.txt", "../main.tex"])
def test_explicit_existing_tex_entry_required(entry):
    with pytest.raises(lp.ProjectError):
        lp.inspect_archive(archive({"main.tex": "Hi"}), entry)


def test_invalid_zip_rejected():
    with pytest.raises(lp.ProjectError, match="invalid_archive"):
        lp.inspect_archive(b"not zip", "main.tex")


@pytest.mark.parametrize(
    "source,extras,expected",
    [
        (r"\input{absent}", {}, "dependency_missing"),
        (r"\input{loop}", {"loop.tex": r"\input{main}"}, "dependency_cycle"),
        (r"\input{\filename}", {}, "dependency_dynamic"),
        (r"\input child", {"child.tex": "present"}, "dependency_dynamic"),
        (r"\input{../secret}", {}, "dependency_unsafe_path"),
        (
            r"\includegraphics{plot}",
            {"plot.png": "one", "plot.jpg": "two"},
            "dependency_ambiguous",
        ),
        (
            r"\newcommand{\pull}{\input{child}}",
            {"child.tex": "present"},
            "dependency_dynamic",
        ),
        (r"\iftrue\input{child}\fi", {"child.tex": "present"}, "dependency_dynamic"),
        (r"\usepackage{privatecustom}", {}, "dependency_missing"),
        (
            r"\graphicspath{{images/}}\includegraphics{image.png}",
            {"images/image.png": "one"},
            "dependency_dynamic",
        ),
        (
            r"\csname input\endcsname{child}",
            {"child.tex": "present"},
            "dependency_dynamic",
        ),
    ],
)
def test_unresolved_dependencies_preserve_originals_and_refuse_flatten(
    source, extras, expected
):
    result = project(source, extras)
    assert expected in codes(result)
    assert result.flattened_source is None
    assert result.files["main.tex"] == source.encode()
    assert result.manifest["transformations"] == []


def test_root_and_includer_candidates_ambiguous():
    result = project(
        r"\input{chapter/one}",
        {
            "chapter/one.tex": r"\input{two}",
            "chapter/two.tex": "local",
            "two.tex": "root",
        },
    )
    assert "dependency_ambiguous" in codes(result)


def test_comments_not_dependencies_and_escaped_percent_not_a_comment():
    result = project(
        "% \\input{absent}\nText \\% \\input{child}", {"child.tex": "Child"}
    )
    assert not result.issues
    assert "% \\input{absent}" in result.flattened_source
    assert "Child" in result.flattened_source


def test_system_package_profile_is_explicit():
    result = project(
        r"\documentclass{article}\usepackage{amsmath,graphicx}\bibliographystyle{plain}"
    )
    assert not result.issues
    assert all(row["resolution"] == "system_profile" for row in result.dependencies)


def test_local_math_package_and_class_expanded_only_in_analysis_view():
    source = r"\documentclass{college}\usepackage{mathnotes}\begin{document}$\RR$\end{document}"
    style = r"\ProvidesPackage{mathnotes}[2026/09/20]\RequirePackage{amsmath}\newcommand{\RR}{\mathbb{R}}"
    klass = r"\NeedsTeXFormat{LaTeX2e}\ProvidesClass{college}\LoadClass{article}\newcommand{\course}{Mathematics}"
    result = project(source, {"mathnotes.sty": style, "college.cls": klass})
    assert not result.issues
    flat = result.flattened_source
    assert r"\newcommand{\RR}{\mathbb{R}}" in flat
    assert r"\newcommand{\course}{Mathematics}" in flat
    assert r"\documentclass" in flat and "{article}" in flat
    assert "ProvidesPackage" not in flat and "ProvidesClass" not in flat
    assert result.files["mathnotes.sty"] == style.encode()
    assert result.files["college.cls"] == klass.encode()
    assert (
        result.manifest["transformations"][0]["result_sha256"]
        == sha256(flat.encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "source,style",
    [
        (r"\usepackage[custom]{local}", r"\newcommand{\X}{X}"),
        (r"\usepackage{local,amsmath}", r"\newcommand{\X}{X}"),
        (r"\usepackage{local}", r"\DeclareOption{foo}{bar}\ProcessOptions"),
        (r"\usepackage{local}", r"\AtBeginDocument{\newcommand{\X}{X}}"),
        (r"\usepackage{local}\usepackage{local}", r"\newcommand{\X}{X}"),
    ],
)
def test_unsupported_local_preamble_context_withheld(source, style):
    result = project(source, {"local.sty": style})
    assert "local_preamble_unsupported" in codes(result)
    assert result.flattened_source is None


def test_limits_include_repeated_expansion_and_graph_depth(monkeypatch):
    monkeypatch.setattr(lp, "MAX_TOTAL_BYTES", 100)
    result = project(
        r"\input{child}\input{child}\input{child}", {"child.tex": "x" * 34}
    )
    assert "flatten_size_limit" in codes(result)
    monkeypatch.setattr(lp, "MAX_DEPTH", 1)
    result = project(r"\input{a}", {"a.tex": r"\input{b}", "b.tex": "B"})
    assert "dependency_depth_limit" in codes(result)


def test_working_copy_records_changes_without_touching_original(tmp_path):
    result = project(r"\input{chapter}", {"chapter.tex": "original"})
    original, working = tmp_path / "original", tmp_path / "working"
    lp.write_project(result, original)
    manifest = lp.prepare_project_copy(original, working, {"chapter.tex": b"reviewed"})
    assert (original / "files/chapter.tex").read_bytes() == b"original"
    assert (working / "files/chapter.tex").read_bytes() == b"reviewed"
    record = manifest["working_copy"]["transformations"][0]
    assert record["before_sha256"] == sha256(b"original").hexdigest()
    assert record["after_sha256"] == sha256(b"reviewed").hexdigest()
    assert lp.load_project(working).source_digest != result.source_digest


@pytest.mark.parametrize("tamper", ["source", "manifest", "extra", "symlink"])
def test_stored_project_integrity_refuses_tampering(tmp_path, tamper):
    destination = tmp_path / "project"
    lp.write_project(project("original"), destination)
    target = destination / "files/main.tex"
    if tamper == "source":
        target.chmod(0o600)
        target.write_bytes(b"changed")
    elif tamper == "manifest":
        target = destination / "manifest.json"
        target.chmod(0o600)
        value = json.loads(target.read_bytes())
        value["entry"] = "different.tex"
        target.write_text(json.dumps(value))
    elif tamper == "extra":
        (destination / "files/extra.tex").write_text("extra")
    else:
        (destination / "files/link.tex").symlink_to(target)
    with pytest.raises(lp.ProjectError, match="stored_project_invalid"):
        lp.load_project(destination)


def test_source_digest_independent_of_zip_member_order():
    first = lp.inspect_archive(
        archive([("main.tex", "Main"), ("extra.txt", "Extra")]), "main.tex"
    )
    second = lp.inspect_archive(
        archive([("extra.txt", "Extra"), ("main.tex", "Main")]), "main.tex"
    )
    assert first.archive_digest != second.archive_digest
    assert first.source_digest == second.source_digest


@pytest.mark.parametrize(
    "replacement", [b"changed-content-xxxxx", b"changed-content-xxxxxx"]
)
def test_corrupt_member_crc_is_rejected(replacement):
    data = archive({"main.tex": "unique-content-for-crc"})
    damaged = data.replace(b"unique-content-for-crc", replacement, 1)
    with pytest.raises(lp.ProjectError, match="invalid_archive"):
        lp.inspect_archive(damaged, "main.tex")


@pytest.mark.parametrize("name", ["CON.tex", "aux.txt", "folder/LPT1.tex", "bad?.tex"])
def test_nonportable_paths_rejected(name):
    with pytest.raises(lp.ProjectError, match="unsafe_path"):
        lp.inspect_archive(archive({"main.tex": "Main", name: "Extra"}), "main.tex")


def test_graph_count_limit_withholds_flattened_source(monkeypatch):
    monkeypatch.setattr(lp, "MAX_DEPENDENCIES", 1)
    result = project(r"\documentclass{article}\usepackage{amsmath}")
    assert "dependency_count_limit" in codes(result)
    assert result.flattened_source is None


def test_missing_stored_manifest_has_bounded_error(tmp_path):
    with pytest.raises(lp.ProjectError, match="stored_project_invalid"):
        lp.load_project(tmp_path / "absent")


def test_malformed_dependencies_stop_after_one_bounded_argument_scan(monkeypatch):
    original = lp._argument
    calls = []

    def counted(*args):
        calls.append(args[1])
        return original(*args)

    monkeypatch.setattr(lp, "_argument", counted)
    result = project(r"\input{" * 1200)
    assert "dependency_dynamic" in codes(result)
    assert len(calls) == 2  # one optional argument and one required argument


def test_lexical_budget_counts_unknown_commands_and_missing_edges(monkeypatch):
    monkeypatch.setattr(lp, "MAX_TOKENS", 4)
    for source in [r"\unknown " * 10, r"\input{absent}" * 10]:
        result = project(source)
        assert "dependency_token_limit" in codes(result)
        assert result.flattened_source is None


@pytest.mark.parametrize(
    "source", [r"^^5cinput{missing}", r"\newcommand{\pull}{^^5cinput{missing}}\pull"]
)
def test_tex_character_code_escape_is_explicitly_unsupported(source):
    result = project(source)
    assert "dependency_dynamic" in codes(result)
    assert result.flattened_source is None
