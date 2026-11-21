from __future__ import annotations

from dataclasses import replace

from config import load_settings
from repositories import settings_for_repository


def test_repository_state_follows_configured_catalog_root(tmp_path) -> None:
    root = tmp_path / "isolated-runtime"
    settings = replace(load_settings(), catalog_path=root / "catalog.sqlite3")

    scoped = settings_for_repository(settings, "traceflow")

    assert scoped.catalog_path == root / "repositories" / "traceflow" / "catalog.sqlite3"
    assert scoped.upload_dir == root / "repositories" / "traceflow" / "uploads"
