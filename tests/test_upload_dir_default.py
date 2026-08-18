"""The upload directory must be writable wherever the app runs.

The default was the absolute /app/uploads, which is right inside the
container and wrong from source: the process tried to create a directory at
the filesystem root. Deriving it from the working directory keeps the
container behaviour identical, because the container works out of /app.
"""

import importlib
import os
from pathlib import Path


def _reload_with(monkeypatch, cwd, upload_dir=None):
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path(cwd)))
    if upload_dir is None:
        monkeypatch.delenv("UPLOAD_DIR", raising=False)
    else:
        monkeypatch.setenv("UPLOAD_DIR", upload_dir)
    import src.utils.file_storage as file_storage

    return importlib.reload(file_storage)


def test_default_sits_under_the_working_directory(monkeypatch, tmp_path):
    module = _reload_with(monkeypatch, tmp_path)
    assert module.UPLOAD_BASE_DIR == tmp_path / "uploads"


def test_the_container_layout_is_unchanged(monkeypatch):
    module = _reload_with(monkeypatch, "/app")
    assert str(module.UPLOAD_BASE_DIR) == "/app/uploads"


def test_an_explicit_setting_still_wins(monkeypatch, tmp_path):
    explicit = str(tmp_path / "elsewhere")
    module = _reload_with(monkeypatch, "/app", upload_dir=explicit)
    assert str(module.UPLOAD_BASE_DIR) == explicit


def teardown_module():
    """Leave the module as the rest of the suite expects to find it."""
    os.environ.pop("UPLOAD_DIR", None)
    import src.utils.file_storage as file_storage

    importlib.reload(file_storage)
