"""Manual WCAG maintenance scripts remain importable operator entry points."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"test_script_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manual_seed_script_remains_an_executable_entry_point():
    module = _load_script("seed_wcag_guidelines.py")

    assert callable(module.main)
    assert callable(module.seed)


def test_manual_embedding_script_remains_an_executable_entry_point():
    module = _load_script("generate_wcag_embeddings.py")

    assert callable(module.main)
    assert callable(module.generate_embedding)
