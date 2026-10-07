"""Uploads must never create or replace files outside their scan directory."""

import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from src.utils import file_storage


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


@pytest.fixture
def storage(monkeypatch, tmp_path):
    root = tmp_path / "uploads"
    monkeypatch.setattr(file_storage, "UPLOAD_BASE_DIR", root)
    return root


def save(department="department-1", scan="scan_1", filename="lesson.txt"):
    return asyncio.run(
        file_storage.save_uploaded_file(
            UploadFile(filename=filename, file=BytesIO(b"course material")),
            department,
            scan,
        )
    )


def test_valid_upload_and_cleanup(storage):
    saved = Path(save())
    assert saved == storage / "department-1" / "scan_1" / "lesson.txt"
    assert saved.read_bytes() == b"course material"
    assert saved.stat().st_mode & 0o077 == 0
    file_storage.cleanup_scan_files("department-1", "scan_1")
    assert not saved.parent.exists()


@pytest.mark.parametrize(
    "bad", ["", ".", "..", "../escape", "/absolute", "a/b", "a\\b", "a\x00b"]
)
@pytest.mark.parametrize("component", ["department", "scan"])
def test_invalid_ids_have_no_filesystem_side_effect(storage, bad, component):
    with pytest.raises(ValueError, match="storage identifier"):
        save(
            department=bad if component == "department" else "department-1",
            scan=bad if component == "scan" else "scan_1",
        )
    assert not storage.exists()


@pytest.mark.parametrize("component", ["department", "scan"])
def test_symlink_directory_cannot_escape_root(storage, tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.mkdir()
    if component == "department":
        (storage / "department-1").symlink_to(outside, target_is_directory=True)
    else:
        (storage / "department-1").mkdir()
        (storage / "department-1" / "scan_1").symlink_to(
            outside, target_is_directory=True
        )
    with pytest.raises(OSError):
        save()
    assert list(outside.iterdir()) == []


def test_leaf_symlink_and_existing_file_are_not_overwritten(storage, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    directory = file_storage.get_scan_storage_dir("department-1", "scan_1")
    file_storage.ensure_storage_dir(directory)
    (directory / "lesson.txt").symlink_to(outside)
    with pytest.raises((ValueError, FileExistsError)):
        save()
    assert outside.read_bytes() == b"keep"
    (directory / "ordinary.txt").write_bytes(b"original")
    with pytest.raises(FileExistsError):
        save(filename="ordinary.txt")
    assert (directory / "ordinary.txt").read_bytes() == b"original"


def test_parent_replaced_after_preflight_cannot_redirect_write(
    storage, tmp_path, monkeypatch
):
    original = file_storage.ensure_storage_dir
    outside = tmp_path / "outside"
    outside.mkdir()

    def replace_parent(directory):
        original(directory)
        directory.rename(directory.with_name("retained"))
        directory.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(file_storage, "ensure_storage_dir", replace_parent)
    with pytest.raises(OSError):
        save()
    assert list(outside.iterdir()) == []


def test_unconfined_directory_is_rejected(storage, tmp_path):
    with pytest.raises(ValueError, match="outside"):
        file_storage.ensure_storage_dir(tmp_path / "uploads-sibling" / "scan")
    assert not (tmp_path / "uploads-sibling").exists()


@pytest.mark.parametrize("component", ["department", "scan"])
def test_cleanup_does_not_follow_symlink(storage, tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep")
    storage.mkdir()
    if component == "department":
        (storage / "department-1").symlink_to(outside, target_is_directory=True)
    else:
        (storage / "department-1").mkdir()
        (storage / "department-1" / "scan_1").symlink_to(
            outside, target_is_directory=True
        )
    file_storage.cleanup_scan_files("department-1", "scan_1")
    assert sentinel.read_bytes() == b"keep"


def test_cleanup_hardlink_only_removes_scan_name(storage, tmp_path):
    import os

    outside = tmp_path / "keep.txt"
    outside.write_bytes(b"keep")
    directory = file_storage.get_scan_storage_dir("department-1", "scan_1")
    file_storage.ensure_storage_dir(directory)
    os.link(outside, directory / "linked.txt")
    file_storage.cleanup_scan_files("department-1", "scan_1")
    assert outside.read_bytes() == b"keep"
    assert not directory.exists()


def test_storage_logs_do_not_include_filename_path_or_exception(storage, caplog):
    with caplog.at_level("INFO"):
        save(filename="private-learner.txt")
        with pytest.raises(FileExistsError):
            save(filename="private-learner.txt")
    assert "private-learner" not in caplog.text
    assert str(storage) not in caplog.text
    assert "FileExistsError" in caplog.text
