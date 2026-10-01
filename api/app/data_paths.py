"""Shared data-path configuration without database or filesystem initialization."""

import os
from collections.abc import Mapping
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1]


def resolve_inventory_data_dir(
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    configured_directory = environment.get("INVENTORY_DATA_DIR", "").strip()
    if configured_directory:
        return Path(configured_directory).expanduser().resolve()
    return (API_DIR / "data").resolve()


def resolve_database_url(
    environ: Mapping[str, str] | None = None,
    *,
    data_directory: Path | None = None,
) -> str:
    environment = os.environ if environ is None else environ
    configured_url = environment.get("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url
    root = data_directory or resolve_inventory_data_dir(environment)
    return f"sqlite:///{(root / 'inventory.db').as_posix()}"


def resolve_import_previews_directory(
    environ: Mapping[str, str] | None = None,
    *,
    data_directory: Path | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    root = data_directory or resolve_inventory_data_dir(environment)
    if environment.get("INVENTORY_DATA_DIR", "").strip():
        return (root / "import-previews").resolve()
    # Preserve the existing local development location when no override is set.
    return (root / "tmp" / "product-import").resolve()
